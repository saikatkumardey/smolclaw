"""Live Claude Code sessions over Telegram via ACP (Agent Client Protocol)."""
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

_ACPX_BIN = shutil.which("acpx") or os.path.expanduser("~/.npm-global/bin/acpx")
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
    """Format a single ACP JSON-RPC event for Telegram display."""
    method = event.get("method", "")
    params = event.get("params", {})
    update = params.get("update", {})
    result = event.get("result", {})

    # Stream chunk: agent text
    if method == "session/update":
        kind = update.get("sessionUpdate", "")

        if kind == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                return content.get("text", "")
            if content.get("type") == "tool_use":
                name = content.get("name", "?")
                inp = content.get("input", {})
                if name == "Bash":
                    cmd = inp.get("command", "")[:200]
                    return f"\n`> {name}: {cmd}`\n"
                if name in ("Read", "Write", "Edit", "Glob", "Grep"):
                    path = inp.get("file_path", inp.get("pattern", ""))
                    return f"\n`> {name}: {path}`\n"
                return f"\n`> {name}`\n"
            if content.get("type") == "tool_result":
                text = str(content.get("content", ""))[:150]
                return f"`  → {text}`\n"

        if kind == "thinking":
            text = update.get("text", "")
            if text:
                return f"_thinking: {text[:100]}_\n"

        # Skip other session updates (usage, commands, etc.)
        return ""

    # Prompt result
    if "result" in event and "stopReason" in result:
        return "\n--- done ---\n"

    return ""


def _truncate_buffer(buf: str) -> str:
    """Keep only the tail of the buffer to stay within Telegram limits."""
    if len(buf) <= _MAX_TG_MSG:
        return buf
    return "…(truncated)\n" + buf[-((_MAX_TG_MSG) - 15):]


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
        # "message is not modified" is expected when buffer hasn't changed
        if "not modified" not in str(e).lower():
            logger.debug("CC edit failed: %s", e)


async def _stream_loop(session: CCSession, bot) -> None:
    """Read ACP events from acpx stdout and relay to Telegram."""
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

            # Capture session ID from session/new result
            if "result" in event and "sessionId" in event.get("result", {}):
                session.session_id = event["result"]["sessionId"]

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


def _build_acpx_cmd(prompt: str, session: CCSession, one_shot: bool = False) -> list[str]:
    """Build the acpx command line."""
    cmd = [
        _ACPX_BIN,
        "--approve-all",
        "--format", "json",
        "claude",
    ]
    if one_shot:
        cmd.append("exec")
    cmd.append(prompt)
    return cmd


async def start_session(chat_id: str, prompt: str, bot, working_dir: str | None = None) -> None:
    """Start a new Claude Code session and stream output to Telegram."""
    if chat_id in _sessions and _sessions[chat_id].process is not None:
        await bot.send_message(chat_id=chat_id, text="CC session already running. Send /cc stop first.")
        return

    # Send initial output message
    msg = await bot.send_message(chat_id=chat_id, text="CC: starting…")

    session = CCSession(
        chat_id=chat_id,
        output_msg_id=msg.message_id,
        working_dir=working_dir or str(workspace.HOME),
    )

    env = {**os.environ}
    env.pop("CLAUDECODE", None)

    cmd = _build_acpx_cmd(prompt, session, one_shot=True)

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
            chat_id=chat_id,
            message_id=msg.message_id,
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
        # Still running — can't send yet
        return False

    # New output message for the continuation
    msg = await bot.send_message(chat_id=chat_id, text="CC: continuing…")
    session.output_msg_id = msg.message_id
    session.buffer = ""
    session.last_edit = 0.0

    env = {**os.environ}
    env.pop("CLAUDECODE", None)

    # Use session prompt (not exec) for multi-turn
    cmd = [_ACPX_BIN, "--approve-all", "--format", "json", "claude", prompt]

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
            chat_id=chat_id,
            message_id=msg.message_id,
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
