"""Live Claude Code sessions over Telegram via stream-json."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field

from . import workspace

logger = logging.getLogger(__name__)

_CLAUDE_BIN = shutil.which("claude") or "claude"
_EDIT_INTERVAL = 2.0  # seconds between Telegram message edits
_MAX_TG_MSG = 4000  # leave room for Telegram's 4096 limit
_MAX_TURNS = 15


@dataclass
class CCSession:
    """State for a live Claude Code session."""

    chat_id: str
    session_id: str | None = None
    process: asyncio.subprocess.Process | None = None
    output_msg_id: int | None = None
    buffer: str = ""
    last_edit: float = 0.0
    task: asyncio.Task | None = None
    working_dir: str = field(default_factory=lambda: str(workspace.HOME))


_sessions: dict[str, CCSession] = {}


def has_active_session(chat_id: str) -> bool:
    """Return True if the chat has a CC session (running or idle)."""
    return chat_id in _sessions


def _format_event(event: dict) -> str:
    """Format a stream-json event for Telegram display."""
    etype = event.get("type", "")

    if etype == "assistant":
        msg = event.get("message", {})
        content_blocks = msg.get("content", [])
        parts = []
        for block in content_blocks:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                if name == "Bash":
                    cmd = inp.get("command", "")[:200]
                    parts.append(f"\n> {name}: {cmd}\n")
                elif name in ("Read", "Write", "Edit", "Glob", "Grep"):
                    path = inp.get("file_path", inp.get("pattern", ""))
                    parts.append(f"\n> {name}: {path}\n")
                else:
                    parts.append(f"\n> {name}\n")
        return "".join(parts)

    if etype == "user":
        # Tool results
        msg = event.get("message", {})
        content_blocks = msg.get("content", [])
        parts = []
        for block in content_blocks:
            if block.get("type") == "tool_result":
                text = str(block.get("content", ""))[:150]
                parts.append(f"  → {text}\n")
        return "".join(parts)

    if etype == "result":
        return "\n--- done ---\n"

    return ""


def _truncate_buffer(buf: str) -> str:
    """Keep only the tail of the buffer to stay within Telegram limits."""
    if len(buf) <= _MAX_TG_MSG:
        return buf
    return "…(truncated)\n" + buf[-(_MAX_TG_MSG - 15):]


async def _edit_output(session: CCSession, bot, final: bool = False) -> None:
    """Edit the Telegram output message with current buffer."""
    now = time.monotonic()
    if not final and (now - session.last_edit) < _EDIT_INTERVAL:
        return
    if not session.output_msg_id or not session.buffer.strip():
        return

    text = _truncate_buffer(session.buffer)
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.output_msg_id,
            text=text,
        )
        session.last_edit = now
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.debug("CC edit failed: %s", e)


async def _stream_loop(session: CCSession, bot) -> None:
    """Read stream-json events from claude stdout and relay to Telegram."""
    proc = session.process
    if not proc or not proc.stdout:
        return

    try:
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Capture session ID
            if event.get("type") == "system" and event.get("session_id"):
                session.session_id = event["session_id"]
            if event.get("type") == "result" and event.get("session_id"):
                session.session_id = event["session_id"]

            formatted = _format_event(event)
            if formatted:
                session.buffer += formatted
                await _edit_output(session, bot)

        # Final edit with complete output
        await _edit_output(session, bot, final=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("CC stream error")
    finally:
        session.process = None


def _build_cmd(prompt: str, session: CCSession) -> list[str]:
    """Build the claude CLI command."""
    cmd = [
        _CLAUDE_BIN, "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(_MAX_TURNS),
        "--permission-mode", "bypassPermissions",
    ]
    if session.session_id:
        cmd.extend(["--session-id", session.session_id, "--continue"])
    return cmd


async def start_session(chat_id: str, prompt: str, bot, working_dir: str | None = None) -> None:
    """Start a new Claude Code session and stream output to Telegram."""
    if chat_id in _sessions and _sessions[chat_id].process is not None:
        await bot.send_message(chat_id=chat_id, text="CC session already running. Send /cc stop first.")
        return

    msg = await bot.send_message(chat_id=chat_id, text="CC: starting…")

    session = CCSession(
        chat_id=chat_id,
        output_msg_id=msg.message_id,
        working_dir=working_dir or str(workspace.HOME),
    )

    # Reuse session_id if we had one before
    old = _sessions.get(chat_id)
    if old and old.session_id:
        session.session_id = old.session_id

    env = {**os.environ}
    env.pop("CLAUDECODE", None)

    cmd = _build_cmd(prompt, session)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=session.working_dir,
            env=env,
        )
    except Exception as e:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"Failed to start CC: {e}",
        )
        return

    session.process = proc
    _sessions[chat_id] = session
    session.task = asyncio.create_task(_stream_loop(session, bot))


async def continue_session(chat_id: str, prompt: str, bot) -> bool:
    """Send a follow-up prompt to an existing CC session."""
    session = _sessions.get(chat_id)
    if not session:
        return False
    if session.process is not None:
        return False
    if not session.session_id:
        return False

    msg = await bot.send_message(chat_id=chat_id, text="CC: continuing…")
    session.output_msg_id = msg.message_id
    session.buffer = ""
    session.last_edit = 0.0

    env = {**os.environ}
    env.pop("CLAUDECODE", None)

    cmd = _build_cmd(prompt, session)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=session.working_dir,
            env=env,
        )
    except Exception as e:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg.message_id,
            text=f"CC continue failed: {e}",
        )
        return False

    session.process = proc
    session.task = asyncio.create_task(_stream_loop(session, bot))
    return True


async def stop_session(chat_id: str) -> bool:
    """Stop an active CC session."""
    session = _sessions.pop(chat_id, None)
    if not session:
        return False

    if session.task:
        session.task.cancel()
        try:
            await session.task
        except asyncio.CancelledError:
            pass

    if session.process:
        try:
            session.process.terminate()
            await asyncio.wait_for(session.process.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                session.process.kill()
            except ProcessLookupError:
                pass

    return True
