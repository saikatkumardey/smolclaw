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
    chat_id: str
    session_id: str | None = None
    process: asyncio.subprocess.Process | None = None
    output_msg_id: int | None = None
    buffer: str = ""
    last_edit: float = 0.0
    task: asyncio.Task | None = None
    working_dir: str = field(default_factory=lambda: str(workspace.HOME))
    tool_status: str = ""  # current tool activity line


_sessions: dict[str, CCSession] = {}


def has_active_session(chat_id: str) -> bool:
    return chat_id in _sessions


def is_session_busy(chat_id: str) -> bool:
    session = _sessions.get(chat_id)
    return session is not None and session.process is not None


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _tool_status_line(block: dict) -> str:
    """Build a compact one-line tool status like '🔧 Read: main.py'."""
    name = block.get("name", "?")
    inp = block.get("input", {})
    hint_map = {
        "Bash": lambda: inp.get("description", inp.get("command", "")[:60]),
        "Read": lambda: inp.get("file_path", "").split("/")[-1],
        "Write": lambda: inp.get("file_path", "").split("/")[-1],
        "Edit": lambda: inp.get("file_path", "").split("/")[-1],
        "Glob": lambda: inp.get("pattern", ""),
        "Grep": lambda: inp.get("pattern", ""),
    }
    hint = hint_map.get(name, lambda: "")()
    if hint:
        return f"🔧 {name}: {hint}"
    return f"🔧 {name}"


def _format_content_block(block: dict) -> str | None:
    btype = block.get("type")
    if btype == "text":
        return _html_escape(block.get("text", ""))
    return None


def _format_event(event: dict, session: CCSession) -> str:
    etype = event.get("type", "")

    if etype == "assistant":
        blocks = event.get("message", {}).get("content", [])
        text_parts = []
        for b in blocks:
            if b.get("type") == "tool_use":
                session.tool_status = _tool_status_line(b)
            elif b.get("type") == "text":
                text = _html_escape(b.get("text", ""))
                if text:
                    session.tool_status = ""  # clear tool status when text arrives
                    text_parts.append(text)
        return "".join(text_parts)

    if etype == "result":
        session.tool_status = ""
        cost = event.get("cost_usd")
        duration = event.get("duration_ms")
        parts = ["✅ <b>done</b>"]
        if cost is not None:
            parts.append(f"${cost:.3f}")
        if duration is not None:
            parts.append(f"{duration / 1000:.0f}s")
        return "\n\n" + " · ".join(parts)

    return ""


_CC_FOOTER_ACTIVE = "\n\n<i>— /cc session (/cc stop to end)</i>"
_CC_FOOTER_DONE = "\n\n<i>— /cc session ended</i>"


def _build_display(session: CCSession, done: bool = False) -> str:
    """Build the message text from buffer + tool status + footer."""
    footer = _CC_FOOTER_DONE if done else _CC_FOOTER_ACTIVE
    status_line = ""
    if session.tool_status and not done:
        status_line = f"\n\n<i>{_html_escape(session.tool_status)}</i>"

    footer_len = len(footer) + len(status_line) + 20
    max_body = _MAX_TG_MSG - footer_len
    buf = session.buffer
    if len(buf) > max_body:
        buf = "…\n" + buf[-(max_body - 5):]
    return buf + status_line + footer


def _strip_html(text: str) -> str:
    from html import unescape
    for tag in ("b", "i", "code"):
        text = text.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    return unescape(text)


async def _edit_output(session: CCSession, bot, final: bool = False) -> None:
    now = time.monotonic()
    if not final and (now - session.last_edit) < _EDIT_INTERVAL:
        return
    if not session.output_msg_id:
        return
    # Allow edits if there's buffer content OR a tool status to show
    if not session.buffer.strip() and not session.tool_status:
        return

    text = _build_display(session, done=final)
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.output_msg_id,
            text=text,
            parse_mode="HTML",
        )
        session.last_edit = now
    except Exception as e:
        err = str(e).lower()
        if "not modified" in err:
            return
        if "parse" not in err and "can't" not in err:
            logger.debug("CC edit failed: %s", e)
            return
        try:
            await bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.output_msg_id,
                text=_strip_html(text),
            )
            session.last_edit = now
        except Exception:
            logger.debug("HTML fallback edit failed", exc_info=True)


async def _maybe_split_message(session: CCSession, bot) -> None:
    """If buffer is getting large, finalize current message and start a new one."""
    if len(session.buffer) < _MAX_TG_MSG - 200:
        return

    # Finalize the current message (no footer, just content)
    text = session.buffer[:_MAX_TG_MSG - 50]
    try:
        await bot.edit_message_text(
            chat_id=session.chat_id,
            message_id=session.output_msg_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception:
        try:
            await bot.edit_message_text(
                chat_id=session.chat_id,
                message_id=session.output_msg_id,
                text=_strip_html(text),
            )
        except Exception:
            pass

    # Start a new message
    msg = await bot.send_message(chat_id=session.chat_id, text="⏳ continuing…")
    session.output_msg_id = msg.message_id
    # Keep only the overflow that didn't fit
    session.buffer = session.buffer[_MAX_TG_MSG - 50:]
    session.last_edit = 0.0


async def _stream_loop(session: CCSession, bot) -> None:
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

            if event.get("type") == "system" and event.get("session_id"):
                session.session_id = event["session_id"]
            if event.get("type") == "result" and event.get("session_id"):
                session.session_id = event["session_id"]

            formatted = _format_event(event, session)
            if formatted:
                session.buffer += formatted
                await _maybe_split_message(session, bot)
            # Edit even without new text — tool status may have changed
            await _edit_output(session, bot)

        stderr_text = ""
        if proc.stderr:
            try:
                stderr_data = await proc.stderr.read()
                stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            except Exception:
                logger.debug("Failed to read stderr", exc_info=True)

        if not session.buffer.strip() and stderr_text:
            session.buffer = f"⚠️ CC error:\n<code>{_html_escape(stderr_text[:500])}</code>"
        elif not session.buffer.strip():
            session.buffer = "✅ done (no output)"

        await _edit_output(session, bot, final=True)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("CC stream error")
    finally:
        session.process = None


def _build_cmd(prompt: str, session: CCSession) -> list[str]:
    cmd = [
        _CLAUDE_BIN, "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(_MAX_TURNS),
        "--permission-mode", "bypassPermissions",
    ]
    if session.session_id:
        cmd.extend(["--resume", session.session_id])
    return cmd


def _make_env() -> dict:
    env = {**os.environ}
    env.pop("CLAUDECODE", None)
    return env


async def _spawn_proc(session: CCSession, prompt: str) -> asyncio.subprocess.Process:
    env = _make_env()
    return await asyncio.create_subprocess_exec(
        *_build_cmd(prompt, session),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=session.working_dir,
        env=env,
    )


async def start_session(chat_id: str, prompt: str, bot, working_dir: str | None = None) -> None:
    if chat_id in _sessions and _sessions[chat_id].process is not None:
        await bot.send_message(chat_id=chat_id, text="CC session already running. Send /cc stop first.")
        return

    msg = await bot.send_message(chat_id=chat_id, text="⏳ starting…")
    session = CCSession(
        chat_id=chat_id,
        output_msg_id=msg.message_id,
        working_dir=working_dir or str(workspace.HOME),
    )
    old = _sessions.get(chat_id)
    if old and old.session_id:
        session.session_id = old.session_id

    try:
        session.process = await _spawn_proc(session, prompt)
    except Exception as e:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"Failed to start CC: {e}")
        return

    _sessions[chat_id] = session
    session.task = asyncio.create_task(_stream_loop(session, bot))


async def continue_session(chat_id: str, prompt: str, bot) -> bool:
    session = _sessions.get(chat_id)
    if not session or session.process is not None or not session.session_id:
        return False

    msg = await bot.send_message(chat_id=chat_id, text="⏳ continuing…")
    session.output_msg_id = msg.message_id
    session.buffer = ""
    session.last_edit = 0.0
    session.tool_status = ""

    try:
        session.process = await _spawn_proc(session, prompt)
    except Exception as e:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"CC continue failed: {e}")
        return False

    session.task = asyncio.create_task(_stream_loop(session, bot))
    return True


async def stop_session(chat_id: str) -> bool:
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
        except (TimeoutError, ProcessLookupError):
            try:
                session.process.kill()
            except ProcessLookupError:
                pass
    return True
