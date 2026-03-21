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
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_event(event: dict) -> str:
    etype = event.get("type", "")

    if etype == "assistant":
        msg = event.get("message", {})
        content_blocks = msg.get("content", [])
        parts = []
        for block in content_blocks:
            if block.get("type") == "text":
                parts.append(_html_escape(block.get("text", "")))
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                hint = ""
                if name == "Bash":
                    hint = inp.get("command", "")[:80]
                elif name in ("Read", "Write", "Edit"):
                    hint = inp.get("file_path", "")
                elif name in ("Glob", "Grep"):
                    hint = inp.get("pattern", "")
                if hint:
                    parts.append(f"\n<code>> {name}: {_html_escape(hint)}</code>")
                else:
                    parts.append(f"\n<code>> {name}</code>")
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
        if "not modified" not in err:
            if "parse" in err or "can't" in err:
                try:
                    from html import unescape
                    plain = unescape(text.replace("<b>", "").replace("</b>", "")
                                         .replace("<i>", "").replace("</i>", "")
                                         .replace("<code>", "").replace("</code>", ""))
                    await bot.edit_message_text(
                        chat_id=session.chat_id,
                        message_id=session.output_msg_id,
                        text=plain,
                    )
                    session.last_edit = now
                except Exception:
                    pass
            else:
                logger.debug("CC edit failed: %s", e)


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
                pass

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
