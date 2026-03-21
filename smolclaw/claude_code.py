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


def _format_tool_hint(block: dict) -> str:
    name = block.get("name", "?")
    inp = block.get("input", {})
    hint_map = {
        "Bash": lambda: inp.get("command", "")[:80],
        "Read": lambda: inp.get("file_path", ""),
        "Write": lambda: inp.get("file_path", ""),
        "Edit": lambda: inp.get("file_path", ""),
        "Glob": lambda: inp.get("pattern", ""),
        "Grep": lambda: inp.get("pattern", ""),
    }
    hint = hint_map.get(name, lambda: "")()
    if hint:
        return f"\n<code>> {name}: {_html_escape(hint)}</code>"
    return f"\n<code>> {name}</code>"


def _format_content_block(block: dict) -> str | None:
    btype = block.get("type")
    if btype == "text":
        return _html_escape(block.get("text", ""))
    if btype == "tool_use":
        return _format_tool_hint(block)
    return None


def _format_event(event: dict) -> str:
    etype = event.get("type", "")

    if etype == "assistant":
        blocks = event.get("message", {}).get("content", [])
        parts = [p for b in blocks if (p := _format_content_block(b)) is not None]
        return "".join(parts)

    if etype == "result":
        return "\n\n✅ <b>done</b>"

    return ""


_CC_FOOTER = "\n\n<i>— /cc session (/cc stop to end)</i>"


def _truncate_buffer(buf: str) -> str:
    footer_len = len(_CC_FOOTER)
    max_body = _MAX_TG_MSG - footer_len
    if len(buf) > max_body:
        buf = "…(truncated)\n" + buf[-(max_body - 15):]
    return buf + _CC_FOOTER


def _strip_html(text: str) -> str:
    from html import unescape
    for tag in ("b", "i", "code"):
        text = text.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    return unescape(text)


async def _edit_output(session: CCSession, bot, final: bool = False) -> None:
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

            formatted = _format_event(event)
            if formatted:
                session.buffer += formatted
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
            session.buffer = "✅ CC: done (no output)"

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

    msg = await bot.send_message(chat_id=chat_id, text="CC: starting…")
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

    msg = await bot.send_message(chat_id=chat_id, text="CC: continuing…")
    session.output_msg_id = msg.message_id
    session.buffer = ""
    session.last_edit = 0.0

    try:
        session.process = await _spawn_proc(session, prompt)
    except Exception as e:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"CC continue failed: {e}")
        return False

    session.task = asyncio.create_task(_stream_loop(session, bot))
    return True


async def _cancel_task(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def stop_session(chat_id: str) -> bool:
    session = _sessions.pop(chat_id, None)
    if not session:
        return False
    if session.task:
        await _cancel_task(session.task)
    if session.process:
        await _terminate_process(session.process)
    return True
