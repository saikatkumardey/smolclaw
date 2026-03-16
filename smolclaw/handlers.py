"""handlers.py — Telegram bot command and message handlers."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

import yaml
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import workspace
from .agent import (
    _CONTEXT_WINDOW_TOKENS,
    AVAILABLE_EFFORTS,
    AVAILABLE_MODELS,
    get_current_effort,
    get_current_model,
    get_last_result,
    interrupt_session,
    list_tasks,
    reset_session,
    session_log,
    set_effort,
    set_model,
)
from .agent import (
    run as agent_run,
)
from .auth import require_allowed
from .session_state import SessionState
from .tool_loader import load_custom_tools
from .tools import MAX_TG_MSG
from .tools_sdk import CUSTOM_TOOLS
from .version import check_remote_version as _check_remote_version
from .version import get_update_summary as _get_update_summary
from .version import local_version as _local_version

logger = logging.getLogger(__name__)


def _to_telegram_md(text: str) -> str:
    """Convert CommonMark bold/italic to Telegram Markdown v1 format."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text


def _classify_error(e: Exception) -> str:
    """Return a user-friendly error message based on exception type."""
    if isinstance(e, asyncio.TimeoutError):
        return "Request timed out. Please try again."
    if isinstance(e, PermissionError):
        return "Permission denied. Check the logs."
    if isinstance(e, ConnectionError):
        return "Connection error. Check your network and try again."
    return "Something went wrong. Check the logs."


class _TypingLoop:
    """Keep the 'typing...' indicator alive until the task completes."""

    def __init__(self, bot, chat_id: str, interval: float = 4.0):
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



async def _reply_chunked(message, text: str, edit_message=None) -> None:
    """Send text in ≤MAX_TG_MSG-char chunks with Markdown, falling back to plain text.

    If edit_message is provided, the first chunk edits that message instead of
    sending a new one (reduces message spam).
    """
    formatted = _to_telegram_md(text)
    chunks = [formatted[i : i + MAX_TG_MSG] for i in range(0, max(len(formatted), 1), MAX_TG_MSG)]
    for idx, chunk in enumerate(chunks):
        if idx == 0 and edit_message is not None:
            try:
                await edit_message.edit_text(chunk, parse_mode="Markdown")
            except Exception:
                try:
                    await edit_message.edit_text(chunk)
                except Exception:
                    # Edit failed (message too old, deleted, etc.) — fall back to reply
                    try:
                        await message.reply_text(chunk, parse_mode="Markdown")
                    except Exception:
                        await message.reply_text(chunk)
        else:
            try:
                await message.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await message.reply_text(chunk)


@require_allowed
async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("SmolClaw online. Say hello.")


@require_allowed
async def on_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "SmolClaw — your personal AI agent.\n\n"
        "I can run shell commands, read/write files, search the web, "
        "and learn any CLI tool you point me at.\n\n"
        "Commands:\n"
        "/help — this message\n"
        "/status — current config and stats\n"
        "/model — show current Claude model\n"
        "/models — switch Claude model\n"
        "/effort — switch thinking effort (low/medium/high/max)\n"
        "/reset — clear conversation history\n"
        "/cancel — cancel the current running task\n"
        "/tasks — list background tasks\n"
        "/crons — list scheduled jobs\n"
        "/reload — reload skills and memory\n"
        "/restart — restart the bot process\n"
        "/update — update smolclaw and restart\n"
        "/btw — ask a side question (no conversation history)\n"
        "/context — show context window usage\n\n"
        "Or just talk to me."
    )
    await update.message.reply_text(text)


@require_allowed
async def on_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    dynamic_tools = load_custom_tools()
    builtin_count = 5  # Bash, Read, Write, WebSearch, WebFetch
    custom_sdk_count = len(CUSTOM_TOOLS) + 1  # +1 for spawn_task
    skills_dir = workspace.SKILLS_DIR
    skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir()) if skills_dir.exists() else 0
    memory_path = workspace.MEMORY
    try:
        memory_lines = len(memory_path.read_text().splitlines())
    except FileNotFoundError:
        memory_lines = 0
    current_model = get_current_model()
    current_effort = get_current_effort()
    dynamic_names = ", ".join(t.name for t in dynamic_tools) if dynamic_tools else "none"
    result = get_last_result(str(update.effective_chat.id))
    cost_line = ""
    if result:
        usage = result.usage or {}
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        cache_str = f" | cache ↓{cache_read} ↑{cache_write}" if (cache_read or cache_write) else ""
        cost_line = f"\nLast turn: {inp}in/{out}out | {result.num_turns} turns | {result.duration_ms}ms{cache_str}"
    usage_today = SessionState.load().get_usage_today()
    today_line = (
        f"\nToday: {usage_today['input_tokens']}in/{usage_today['output_tokens']}out | {usage_today['turns']} turns"
    )
    from .browser import BrowserManager
    browser_backend = BrowserManager.get().backend
    text = (
        f"Model: {current_model}\n"
        f"Effort: {current_effort}\n"
        f"Workspace: {workspace.HOME}\n"
        f"Browser: {browser_backend}\n"
        f"Built-in tools: {builtin_count}\n"
        f"Custom SDK tools: {custom_sdk_count}\n"
        f"Dynamic tools: {len(dynamic_tools)} ({dynamic_names})\n"
        f"Skills: {skill_count}\n"
        f"Memory: {memory_lines} lines"
        f"{cost_line}"
        f"{today_line}"
    )
    await update.message.reply_text(text)


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
async def on_reload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    await reset_session(chat_id)
    await update.message.reply_text("Reloaded. Next message picks up fresh skills and memory.")



@require_allowed
async def on_tasks(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    tasks = list_tasks()
    if not tasks:
        await update.message.reply_text("No background tasks.")
        return
    lines = []
    for t in tasks:
        icon = "running" if t["status"] == "running" else t["status"]
        lines.append(f"{t['id']} [{icon}] {t['elapsed_s']}s — {t['description']}")
    await update.message.reply_text("\n".join(lines))



@require_allowed
async def on_crons(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    crons_path = workspace.CRONS
    if not crons_path.exists():
        await update.message.reply_text("No crons.yaml found.")
        return
    data = yaml.safe_load(crons_path.read_text()) or {}
    jobs = data.get("jobs", [])
    if not jobs:
        await update.message.reply_text("No scheduled jobs.")
        return
    lines = []
    for job in jobs:
        jid = job.get("id", "?")
        cron = job.get("cron", "?")
        prompt = job.get("prompt", "")[:60]
        lines.append(f"{jid} ({cron}): {prompt}")
    await update.message.reply_text("Scheduled jobs:\n" + "\n".join(lines))

CONTEXT_WARN_THRESHOLD = 0.80


def _context_fill(chat_id: str) -> tuple[int, float]:
    """Return (used_tokens, fill_fraction) from last result for a chat."""
    result = get_last_result(chat_id)
    if not result:
        return 0, 0.0
    usage = result.usage or {}
    used = usage.get("cache_read_input_tokens", 0) + usage.get("input_tokens", 0)
    return used, used / _CONTEXT_WINDOW_TOKENS


@require_allowed
async def on_context(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    used, fill = _context_fill(chat_id)
    pct = fill * 100
    bar_filled = int(fill * 20)
    bar = "#" * bar_filled + "-" * (20 - bar_filled)
    status = "OK"
    if fill >= 0.95:
        status = "CRITICAL — reset soon"
    elif fill >= CONTEXT_WARN_THRESHOLD:
        status = "WARNING — approaching limit"
    text = (
        f"Context window: {pct:.1f}%\n"
        f"[{bar}]\n"
        f"{used:,} / {_CONTEXT_WINDOW_TOKENS:,} tokens\n"
        f"Status: {status}"
    )
    await update.message.reply_text(text)


@require_allowed
async def on_model(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    current = get_current_model()
    label = next((lbl for mid, lbl in AVAILABLE_MODELS if mid == current), current)
    await update.message.reply_text(
        f"Current model: *{label}*\n`{current}`",
        parse_mode="Markdown",
    )


@require_allowed
async def on_models(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    current = get_current_model()
    keyboard = [
        [InlineKeyboardButton(
            f"{'✓ ' if mid == current else ''}{lbl}",
            callback_data=f"model:{mid}",
        )]
        for mid, lbl in AVAILABLE_MODELS
    ]
    await update.message.reply_text(
        "Select a Claude model:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _handle_selection_callback(
    update: Update,
    prefix: str,
    choices: list[tuple[str, str]],
    apply_fn,
    success_msg: str,
) -> None:
    """Shared logic for model/effort inline keyboard callbacks."""
    from .auth import is_allowed
    cb = update.callback_query
    await cb.answer()
    if not (cb.data or "").startswith(prefix):
        return
    if not is_allowed(update.effective_chat.id):
        await cb.edit_message_text("Not authorised.")
        return
    selected = cb.data[len(prefix):]
    valid = {cid for cid, _ in choices}
    if selected not in valid:
        await cb.edit_message_text(f"Unknown {prefix.rstrip(':')}.")
        return
    await apply_fn(selected)
    label = next(lbl for cid, lbl in choices if cid == selected)
    await cb.edit_message_text(
        success_msg.format(label=label, id=selected),
        parse_mode="Markdown",
    )


async def on_model_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_selection_callback(
        update, "model:", AVAILABLE_MODELS, set_model,
        "✓ Switched to *{label}*\n`{id}`\n\nAll sessions reset — next message uses the new model.",
    )


@require_allowed
async def on_effort(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    current = get_current_effort()
    keyboard = [
        [InlineKeyboardButton(
            f"{'✓ ' if eid == current else ''}{lbl}",
            callback_data=f"effort:{eid}",
        )]
        for eid, lbl in AVAILABLE_EFFORTS
    ]
    await update.message.reply_text(
        "Select thinking effort level:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


@require_allowed
async def on_efforts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await on_effort(update, ctx)


async def on_effort_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_selection_callback(
        update, "effort:", AVAILABLE_EFFORTS, set_effort,
        "✓ Effort set to *{label}*\n\nAll sessions reset — next message uses the new effort level.",
    )


@require_allowed
async def on_restart(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    import signal
    await update.message.reply_text("Restarting…")
    try:
        from .handover import save
        save("Process restarting via /restart command.")
    except Exception:
        pass
    # Clean exit — let systemd (Restart=always) bring us back.
    os.kill(os.getpid(), signal.SIGTERM)


@require_allowed
async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import signal
    import subprocess as _subprocess

    from .handover import save as save_handover

    old_version = _local_version()
    source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")

    await update.message.reply_text("Checking for updates…")

    remote = await asyncio.to_thread(_check_remote_version, source)
    if remote and remote == old_version:
        await update.message.reply_text(f"Already on latest version (v{old_version}). No update needed.")
        return

    await update.message.reply_text("Update available — installing…")
    try:
        result = await asyncio.to_thread(
            _subprocess.run,
            ["uv", "tool", "install", "--upgrade", source],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        await update.message.reply_text(f"Update failed: {e}")
        return

    if result.returncode != 0:
        await update.message.reply_text(f"Update failed:\n{result.stderr[:500]}")
        return

    summary = await asyncio.to_thread(_get_update_summary, source, old_version)

    try:
        save_handover(f"Updated via /update command.\n\n{summary}\n\nPENDING: none")
    except Exception as e:
        logger.warning("Handover save failed: %s", e)

    await update.message.reply_text(f"Updated. Restarting…\n\n{summary}")

    # Clean exit — let systemd (Restart=always) bring us back with the new binary.
    os.kill(os.getpid(), signal.SIGTERM)


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
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.edited_message or update.message
    chat_id = str(update.effective_chat.id)
    text = msg.text or ""
    is_edit = update.edited_message is not None
    logger.info("%s [%s]: %s", "Edit" if is_edit else "Incoming", chat_id, text[:80])
    try:
        # Send a placeholder that we'll edit with the final reply (avoids message spam)
        placeholder = await msg.reply_text("...")
        agent_msg = (
            f"[chat_id={chat_id} message_id={msg.message_id} reply_id={placeholder.message_id}]\n{text}"
        )
        async with _TypingLoop(context.bot, chat_id):
            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
        logger.info("Reply [%s]: %s", chat_id, reply[:80])
        await _reply_chunked(msg, reply, edit_message=placeholder)
        used, fill = _context_fill(chat_id)
        if fill >= CONTEXT_WARN_THRESHOLD:
            pct = fill * 100
            warn = f"Context at {pct:.0f}% ({used:,} / {_CONTEXT_WINDOW_TOKENS:,} tokens). Consider /reset soon."
            await msg.reply_text(warn)
    except Exception as e:
        logger.exception("Error handling message: %s", e)
        await msg.reply_text(_classify_error(e))


@require_allowed
async def on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle message reactions — pass them to the agent as feedback."""
    reaction = update.message_reaction
    if not reaction:
        return
    chat_id = str(reaction.chat.id)

    new = reaction.new_reaction or []
    old = reaction.old_reaction or []

    # Only care about new reactions (not removals)
    added = [r for r in new if r not in old]
    if not added:
        return

    emojis = []
    for r in added:
        if hasattr(r, "emoji"):
            emojis.append(r.emoji)
        elif hasattr(r, "custom_emoji_id"):
            emojis.append(f"(custom:{r.custom_emoji_id})")

    if not emojis:
        return

    emoji_str = " ".join(emojis)
    agent_msg = f"[User reacted to a previous message with: {emoji_str}]"
    logger.info("Reaction [%s]: %s", chat_id, emoji_str)
    try:
        async with _TypingLoop(context.bot, chat_id):
            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
        if reply and reply != "(no response)":
            await context.bot.send_message(chat_id=chat_id, text=_to_telegram_md(reply), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Error handling reaction: %s", e)


async def _handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, agent_msg: str) -> None:
    """Shared handler for file/photo uploads: run agent and reply."""
    chat_id = str(update.effective_chat.id)
    try:
        placeholder = await update.message.reply_text("...")
        async with _TypingLoop(context.bot, chat_id):
            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
        await _reply_chunked(update.message, reply, edit_message=placeholder)
    except Exception as e:
        logger.exception("Error handling upload: %s", e)
        await update.message.reply_text(_classify_error(e))


@require_allowed
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle documents: download to uploads/, pass path to agent."""
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
    await _handle_upload(update, context, agent_msg)


@require_allowed
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos: save to uploads/, pass path to agent for native vision."""
    chat_id = str(update.effective_chat.id)
    caption = update.message.caption or ""
    try:
        photo = update.message.photo[-1]  # highest resolution
        file = await context.bot.get_file(photo.file_id)
        dest = workspace.UPLOADS_DIR / f"{photo.file_unique_id}.jpg"
        await file.download_to_drive(str(dest))
    except Exception as e:
        logger.exception("Error downloading photo: %s", e)
        await update.message.reply_text(_classify_error(e))
        return
    agent_msg = f"[chat_id={chat_id} message_id={update.message.message_id}]\n[User sent a photo. Saved to: {dest}]\n\n{caption}"
    await _handle_upload(update, context, agent_msg)
