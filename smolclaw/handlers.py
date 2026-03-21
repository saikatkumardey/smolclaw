from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from . import workspace
from .agent import (
    cancel_all_tasks,
    get_streaming,
    interrupt_session,
    reset_session,
    session_log,
)
from .agent import (
    run as agent_run,
)
from .agent import (
    run_streaming as agent_run_streaming,
)
from .auth import require_allowed
from .handlers_commands import (  # noqa: F401
    CONTEXT_WARN_THRESHOLD,
    _context_fill,
    _format_last_turn,
    on_context,
    on_crons,
    on_effort,
    on_effort_callback,
    on_help,
    on_model,
    on_model_callback,
    on_models,
    on_restart,
    on_status,
    on_streaming,
    on_tasks,
    on_update,
)
from .tools import MAX_TG_MSG

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 1.5
_debounce_buffers: dict[str, dict] = {}


async def flush_debounce(chat_id: str) -> None:
    """Wait for any pending debounce task to complete. Used by tests."""
    buf = _debounce_buffers.get(chat_id)
    if buf and buf["task"] and not buf["task"].done():
        await buf["task"]


def _to_telegram_md(text: str) -> str:
    """Convert CommonMark bold/italic to Telegram Markdown v1 format."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text


def _is_tool_noise(reply: str) -> bool:
    """Return True if the reply is a default tool-only response with no real content."""
    return reply == "(no response)" or reply.startswith("Done. (used:")


_ERROR_MESSAGES = {
    asyncio.TimeoutError: "Request timed out. Please try again.",
    PermissionError: "Permission denied. Check the logs.",
    ConnectionError: "Connection error. Check your network and try again.",
}


def _classify_error(e: Exception) -> str:
    return _ERROR_MESSAGES.get(type(e), "Something went wrong. Check the logs.")


class _TypingLoop:
    """Keep the 'typing...' indicator alive until the task completes."""

    def __init__(self, bot, chat_id: str, interval: float = 2.0):
        self._bot = bot
        self._chat_id = chat_id
        self._interval = interval
        self._task: asyncio.Task | None = None

    async def _loop(self):
        try:
            while True:
                await self._bot.send_chat_action(chat_id=self._chat_id, action="typing")
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    async def __aenter__(self):
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def _strip_md(text: str) -> str:
    """Remove Markdown formatting characters so the text is safe as plain text."""
    return re.sub(r"[*_`\[\]]", "", text)


async def _send_md_msg(target, text: str, *, edit: bool = False) -> None:
    """Send/edit with Markdown, falling back to stripped plain text on failure."""
    fn = target.edit_text if edit else target.reply_text
    try:
        await fn(text, parse_mode="Markdown")
    except Exception:
        # Markdown rejected — strip formatting and send plain text.
        # For edit_text this is safe (same message). For reply_text the
        # Telegram API guarantees the message was NOT delivered on error,
        # so a second attempt won't duplicate.
        await fn(_strip_md(text))


async def _reply_chunked(message, text: str, edit_message=None) -> None:
    """Send text in <=MAX_TG_MSG-char chunks with Markdown, falling back to plain text."""
    formatted = _to_telegram_md(text)
    if not formatted:
        return
    chunks = [formatted[i : i + MAX_TG_MSG] for i in range(0, len(formatted), MAX_TG_MSG)]
    for idx, chunk in enumerate(chunks):
        if idx == 0 and edit_message is not None:
            try:
                await _send_md_msg(edit_message, chunk, edit=True)
            except Exception:
                await _send_md_msg(message, chunk)
        else:
            await _send_md_msg(message, chunk)


def _inject_reply_id(agent_msg: str, chat_id: str, reply_id: int) -> str:
    """Inject reply_id into the agent message metadata if not already present."""
    tag = f"[chat_id={chat_id}"
    if tag in agent_msg and "reply_id=" not in agent_msg:
        return agent_msg.replace(tag, f"[chat_id={chat_id} reply_id={reply_id}", 1)
    return agent_msg


async def _send_reply(bot, message, chat_id: str, reply: str, placeholder) -> None:
    """Send the agent reply via the appropriate channel."""
    if message or placeholder:
        await _reply_chunked(message, reply, edit_message=placeholder)
    else:
        fmt = _to_telegram_md(reply)
        try:
            await bot.send_message(chat_id=chat_id, text=fmt, parse_mode="Markdown")
        except Exception:
            await bot.send_message(chat_id=chat_id, text=fmt)


async def _send_error(message, placeholder, error_msg: str) -> None:
    """Send an error message via placeholder edit or message reply."""
    if placeholder:
        try:
            await placeholder.edit_text(error_msg)
            return
        except Exception:
            logger.debug("failed to edit placeholder with error", exc_info=True)
    if message:
        await message.reply_text(error_msg)


_DRAFT_INTERVAL = 0.5  # minimum seconds between draft updates
_DRAFT_ID = 1  # constant draft_id; same ID = animated updates


def _append_context_warn(reply: str, chat_id: str) -> str:
    _used, fill = _context_fill(chat_id)
    if fill >= CONTEXT_WARN_THRESHOLD:
        reply += f"\n\n⚠️ Context at {fill*100:.0f}% — consider /reset soon."
    return reply


async def _draft_sender(bot, chat_id: str, accumulated: list[str], done_event: asyncio.Event) -> None:
    while not done_event.is_set():
        if accumulated:
            text = "".join(accumulated)[:MAX_TG_MSG]
            try:
                await bot.send_message_draft(chat_id=int(chat_id), draft_id=_DRAFT_ID, text=text)
            except Exception:
                logger.debug("send_message_draft failed", exc_info=True)
        try:
            await asyncio.wait_for(done_event.wait(), timeout=_DRAFT_INTERVAL)
        except TimeoutError:
            pass


async def _run_agent_and_reply_streaming(
    bot, message, chat_id: str, agent_msg: str,
    *, context_warn: bool = False,
) -> None:
    accumulated: list[str] = []
    done_event = asyncio.Event()
    sender_task = asyncio.create_task(_draft_sender(bot, chat_id, accumulated, done_event))
    try:
        async for event_type, data in agent_run_streaming(chat_id=chat_id, user_message=agent_msg):
            if event_type == "text_delta":
                accumulated.append(data)
            elif event_type == "done":
                done_event.set()
                if not data or _is_tool_noise(data):
                    return
                reply = _append_context_warn(data, chat_id) if context_warn else data
                await _send_reply(bot, message, chat_id, reply, placeholder=None)
    except Exception as e:
        logger.exception("Streaming error: %s", e)
        if message:
            await message.reply_text(_classify_error(e))
    finally:
        done_event.set()
        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass


async def _run_agent_and_reply(
    bot, message, chat_id: str, agent_msg: str,
    *, context_warn: bool = False,
) -> None:
    """Run agent and send reply. Shared by on_message, on_reaction, _handle_upload."""
    # Use streaming for interactive sessions when enabled
    if message and not chat_id.startswith("cron:") and get_streaming():
        await _run_agent_and_reply_streaming(
            bot, message, chat_id, agent_msg, context_warn=context_warn,
        )
        return

    try:
        async with _TypingLoop(bot, chat_id):
            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
        if not reply or _is_tool_noise(reply):
            return
        if context_warn:
            _used, fill = _context_fill(chat_id)
            if fill >= CONTEXT_WARN_THRESHOLD:
                reply += f"\n\n⚠️ Context at {fill*100:.0f}% — consider /reset soon."
        await _send_reply(bot, message, chat_id, reply, placeholder=None)
    except Exception as e:
        logger.exception("Error: %s", e)
        await _send_error(message, None, _classify_error(e))


@require_allowed
async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "SmolClaw online. I'm your personal AI agent — "
        "just send a message or type /help to see what I can do."
    )


@require_allowed
async def on_reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    session_log(chat_id, "system", "SESSION_RESET")
    await reset_session(chat_id)
    await update.message.reply_text("Memory cleared. Starting fresh.")


@require_allowed
async def on_cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    interrupted = await interrupt_session(chat_id)
    await update.message.reply_text("Cancelled." if interrupted else "Nothing to cancel.")


@require_allowed
async def on_stop(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop everything: interrupt current turn, cancel all background tasks."""
    chat_id = str(update.effective_chat.id)
    interrupted = await interrupt_session(chat_id)
    tasks_cancelled = cancel_all_tasks(chat_id)
    parts = []
    if interrupted:
        parts.append("Interrupted active turn")
    if tasks_cancelled:
        parts.append(f"Cancelled {tasks_cancelled} background task{'s' if tasks_cancelled != 1 else ''}")
    if parts:
        await update.message.reply_text("Stopped. " + ". ".join(parts) + ".")
    else:
        await update.message.reply_text("Nothing running.")


@require_allowed
async def on_reload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    await reset_session(chat_id)
    await update.message.reply_text("Reloaded. Next message picks up fresh skills and memory.")


@require_allowed
async def on_btw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /btw — quick side question via claude -p, no session or tools."""
    import subprocess as _sp

    msg = update.message
    chat_id = str(update.effective_chat.id)
    text = (msg.text or "").split(None, 1)[1] if len((msg.text or "").split(None, 1)) > 1 else ""
    if not text.strip():
        await msg.reply_text("Usage: /btw <question>\nQuick side question — no tools, no history.")
        return

    from .config import Config
    system = "You are a helpful assistant. Be concise and direct. Use Telegram Markdown v1 formatting (*bold*, _italic_). No headers."
    btw_model = Config.load().get("btw_model")

    try:
        async with _TypingLoop(context.bot, chat_id):
            result = await asyncio.to_thread(
                _sp.run,
                [
                    "claude", "-p",
                    "--model", btw_model,
                    "--system-prompt", system,
                    "--allowedTools", "WebSearch", "WebFetch", "Read",
                ],
                input=text, capture_output=True, text=True, timeout=60,
            )
        reply = result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr[:300]}"
        btw_reply = f"_/btw_\n{reply}" if reply else "(no response)"
        await _reply_chunked(msg, btw_reply)
    except Exception as e:
        logger.exception("Error handling /btw: %s", e)
        await msg.reply_text(_classify_error(e))


@require_allowed
async def on_cc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cc — start or interact with a live Claude Code session."""
    from .claude_code import continue_session, has_active_session, start_session, stop_session

    msg = update.message
    chat_id = str(update.effective_chat.id)
    parts = (msg.text or "").split(None, 1)
    prompt = parts[1] if len(parts) > 1 else ""

    if prompt.strip().lower() == "stop":
        stopped = await stop_session(chat_id)
        await msg.reply_text("CC session stopped." if stopped else "No active CC session.")
        return

    if not prompt.strip():
        if has_active_session(chat_id):
            await msg.reply_text("CC session active. Send a message to continue, or /cc stop to end.")
        else:
            await msg.reply_text("Usage: /cc <prompt>\nStarts a live Claude Code session.")
        return

    if has_active_session(chat_id):
        continued = await continue_session(chat_id, prompt, context.bot)
        if not continued:
            await msg.reply_text("CC session is still running. Wait for it to finish or /cc stop.")
        return

    await start_session(chat_id, prompt, context.bot)


@require_allowed
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.edited_message or update.message
    chat_id = str(update.effective_chat.id)
    text = msg.text or ""
    is_edit = update.edited_message is not None
    logger.info("%s [%s]: %s", "Edit" if is_edit else "Incoming", chat_id, text[:80])

    # Route to CC session if one is active (no debounce)
    from .claude_code import continue_session, has_active_session, is_session_busy
    if has_active_session(chat_id):
        if is_session_busy(chat_id):
            await msg.reply_text("CC is still working… wait for it to finish, or /cc stop.")
            return
        continued = await continue_session(chat_id, text, context.bot)
        if continued:
            return

    # Debounce: accumulate rapid messages, process after pause
    buf = _debounce_buffers.get(chat_id)
    if buf is None:
        buf = {"messages": [], "task": None, "last_msg": msg, "bot": context.bot}
        _debounce_buffers[chat_id] = buf

    buf["messages"].append(text)
    buf["last_msg"] = msg
    buf["bot"] = context.bot

    # Cancel previous debounce timer if still waiting
    if buf["task"] is not None and not buf["task"].done():
        buf["task"].cancel()

    async def _flush():
        from .config import Config
        delay = Config.load().get("debounce_seconds", _DEBOUNCE_SECONDS)
        await asyncio.sleep(delay)
        pending = _debounce_buffers.pop(chat_id, None)
        if not pending or not pending["messages"]:
            return
        combined = "\n".join(pending["messages"])
        last_msg = pending["last_msg"]
        bot = pending["bot"]
        agent_msg = f"[chat_id={chat_id} message_id={last_msg.message_id}]\n{combined}"
        await _run_agent_and_reply(bot, last_msg, chat_id, agent_msg, context_warn=True)

    buf["task"] = asyncio.create_task(_flush())


def _extract_reaction_emojis(added: list) -> list[str]:
    return [
        r.emoji if hasattr(r, "emoji") else f"(custom:{r.custom_emoji_id})"
        for r in added
        if hasattr(r, "emoji") or hasattr(r, "custom_emoji_id")
    ]


@require_allowed
async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle message reactions — pass them to the agent as feedback."""
    reaction = update.message_reaction
    if not reaction:
        return
    chat_id = str(reaction.chat.id)
    added = [r for r in (reaction.new_reaction or []) if r not in (reaction.old_reaction or [])]
    emojis = _extract_reaction_emojis(added)
    if not emojis:
        return
    emoji_str = " ".join(emojis)
    logger.info("Reaction [%s]: %s", chat_id, emoji_str)
    await _run_agent_and_reply(context.bot, None, chat_id, f"[User reacted to a previous message with: {emoji_str}]")


@require_allowed
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    doc = update.message.document
    caption = update.message.caption or ""
    try:
        file = await context.bot.get_file(doc.file_id)
        raw_name = doc.file_name or f"{doc.file_unique_id}.bin"
        safe_name = Path(raw_name).name
        dest = workspace.UPLOADS_DIR / safe_name
        await file.download_to_drive(str(dest))
    except Exception as e:
        logger.exception("Error downloading document: %s", e)
        await update.message.reply_text(_classify_error(e))
        return
    mime = doc.mime_type or "application/octet-stream"
    agent_msg = f"[chat_id={chat_id} message_id={update.message.message_id}]\n[User sent file '{safe_name}' ({mime}). Saved to: {dest}]\n\n{caption}"
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)


@require_allowed
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    caption = update.message.caption or ""
    if not update.message.photo:
        await update.message.reply_text("No photo data received.")
        return
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        dest = workspace.UPLOADS_DIR / f"{photo.file_unique_id}.jpg"
        await file.download_to_drive(str(dest))
    except Exception as e:
        logger.exception("Error downloading photo: %s", e)
        await update.message.reply_text(_classify_error(e))
        return
    agent_msg = f"[chat_id={chat_id} message_id={update.message.message_id}]\n[User sent a photo. Saved to: {dest}]\n\n{caption}"
    await _run_agent_and_reply(context.bot, update.message, chat_id, agent_msg)
