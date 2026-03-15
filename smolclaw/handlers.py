"""handlers.py — Telegram bot command and message handlers."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from pathlib import Path

import yaml
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from . import workspace
from .tool_loader import load_custom_tools
from .tools import MAX_TG_MSG
from .tools_sdk import CUSTOM_TOOLS
from .session_state import SessionState
from .agent import (
    AVAILABLE_MODELS,
    AVAILABLE_EFFORTS,
    get_current_model,
    get_current_effort,
    get_last_result,
    interrupt_session,
    list_tasks,
    reset_session,
    run as agent_run,
    session_log,
    set_model,
    set_effort,
)
from .auth import require_allowed

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


def _get_update_summary(source: str, old_version: str) -> str:
    """Get version and changelog after a successful update."""
    import re
    import subprocess as _subprocess

    # Get new version from the freshly installed binary
    new_version = None
    try:
        ver_result = _subprocess.run(
            ["uv", "tool", "run", "smolclaw", "--", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if ver_result.returncode == 0:
            # Output is "smolclaw X.Y.Z"
            m = re.search(r"(\d+\.\d+\.\d+)", ver_result.stdout)
            if m:
                new_version = m.group(1)
    except Exception:
        pass

    if not new_version:
        try:
            list_result = _subprocess.run(
                ["uv", "tool", "list"], capture_output=True, text=True, timeout=10,
            )
            for line in list_result.stdout.splitlines():
                if "smolclaw" in line.lower():
                    m = re.search(r"(\d+\.\d+\.\d+)", line)
                    if m:
                        new_version = m.group(1)
                    break
        except Exception:
            pass

    new_version = new_version or "unknown"

    # Get recent commits from GitHub
    changes = []
    try:
        repo_match = re.search(r"github\.com/([^/]+/[^/.\s]+)", source)
        if repo_match:
            repo = repo_match.group(1).rstrip(".git")
            import requests as _req
            resp = _req.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"per_page": "10"},
                timeout=10,
            )
            if resp.status_code == 200:
                for commit in resp.json():
                    msg = commit.get("commit", {}).get("message", "").split("\n")[0]
                    if msg and not msg.startswith("bump version"):
                        changes.append(f"- {msg}")
                        if len(changes) >= 5:
                            break
    except Exception:
        pass

    parts = [f"{old_version} -> {new_version}"]
    if changes:
        parts.append("\nRecent changes:\n" + "\n".join(changes))
    return "\n".join(parts)


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


async def _react(message, emoji: str = "\U0001f440") -> None:
    """React to a message with an emoji. Silently ignores failures."""
    try:
        from telegram import ReactionTypeEmoji
        await message.set_reaction(ReactionTypeEmoji(emoji))
    except Exception:
        pass


async def _react_done(message, reply: str) -> None:
    """React with a contextual emoji based on the reply content."""
    reply_lower = reply.lower()
    if any(w in reply_lower for w in ("error", "failed", "traceback", "exception", "broke")):
        emoji = "\U0001f480"  # 💀
    elif any(w in reply_lower for w in ("deploy", "push", "ship", "launch", "live")):
        emoji = "\U0001f525"  # 🔥
    elif any(w in reply_lower for w in ("done", "fixed", "complete", "success", "saved", "logged")):
        emoji = "\u2705"  # ✅
    else:
        emoji = "\U0001fae1"  # 🫡
    await _react(message, emoji)


async def _reply_chunked(message, text: str) -> None:
    """Send text in ≤MAX_TG_MSG-char chunks with Markdown, falling back to plain text."""
    formatted = _to_telegram_md(text)
    for i in range(0, max(len(formatted), 1), MAX_TG_MSG):
        chunk = formatted[i : i + MAX_TG_MSG]
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
        "/reload — reload skills and memory\n"
        "/restart — restart the bot process\n"
        "/update — update smolclaw and restart\n"
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
    text = (
        f"Model: {current_model}\n"
        f"Workspace: {workspace.HOME}\n"
        f"Built-in tools: {builtin_count}\n"
        f"Custom SDK tools: {custom_sdk_count}\n"
        f"Dynamic tools: {len(dynamic_tools)}\n"
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

CONTEXT_WINDOW_TOKENS = 200_000
CONTEXT_WARN_THRESHOLD = 0.80


def _context_fill(chat_id: str) -> tuple[int, float]:
    """Return (used_tokens, fill_fraction) from last result for a chat."""
    result = get_last_result(chat_id)
    if not result:
        return 0, 0.0
    usage = result.usage or {}
    used = usage.get("cache_read_input_tokens", 0) + usage.get("input_tokens", 0)
    return used, used / CONTEXT_WINDOW_TOKENS


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
        f"{used:,} / {CONTEXT_WINDOW_TOKENS:,} tokens\n"
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


async def on_model_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    from .auth import is_allowed
    query = update.callback_query
    await query.answer()
    if not (query.data or "").startswith("model:"):
        return
    if not is_allowed(update.effective_chat.id):
        await query.edit_message_text("Not authorised.")
        return
    model_id = query.data[len("model:"):]
    valid = {mid for mid, _ in AVAILABLE_MODELS}
    if model_id not in valid:
        await query.edit_message_text("Unknown model.")
        return
    await set_model(model_id)
    label = next(lbl for mid, lbl in AVAILABLE_MODELS if mid == model_id)
    await query.edit_message_text(
        f"✓ Switched to *{label}*\n`{model_id}`\n\nAll sessions reset — next message uses the new model.",
        parse_mode="Markdown",
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
    from .auth import is_allowed
    query = update.callback_query
    await query.answer()
    if not (query.data or "").startswith("effort:"):
        return
    if not is_allowed(update.effective_chat.id):
        await query.edit_message_text("Not authorised.")
        return
    effort_id = query.data[len("effort:"):]
    valid = {eid for eid, _ in AVAILABLE_EFFORTS}
    if effort_id not in valid:
        await query.edit_message_text("Unknown effort level.")
        return
    await set_effort(effort_id)
    label = next(lbl for eid, lbl in AVAILABLE_EFFORTS if eid == effort_id)
    await query.edit_message_text(
        f"✓ Effort set to *{label}*\n\nAll sessions reset — next message uses the new effort level.",
        parse_mode="Markdown",
    )


@require_allowed
async def on_restart(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Restarting…")
    try:
        from .handover import save
        save("Process restarting via /restart command.")
    except Exception:
        pass
    try:
        from .scheduler import scheduler as _sched
        _sched.shutdown(wait=False)
    except Exception:
        pass
    exe = shutil.which("smolclaw") or sys.argv[0]
    argv = [exe, "start"] if len(sys.argv) < 2 else [exe] + sys.argv[1:]
    os.execv(exe, argv)


@require_allowed
async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import importlib.metadata
    import subprocess as _subprocess

    from .handover import save as save_handover

    await update.message.reply_text("Saving handover and updating…")

    # Capture current version
    try:
        old_version = importlib.metadata.version("smolclaw")
    except Exception:
        old_version = "unknown"

    source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")
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

    # Get new version + changelog
    summary = await asyncio.to_thread(_get_update_summary, source, old_version)

    try:
        save_handover(f"Updated via /update command.\n\n{summary}\n\nPENDING: none")
    except Exception as e:
        logger.warning("Handover save failed: %s", e)

    await update.message.reply_text(f"Updated. Restarting…\n\n{summary}")

    try:
        from .scheduler import scheduler as _sched
        _sched.shutdown(wait=False)
    except Exception:
        pass

    exe = shutil.which("smolclaw") or sys.argv[0]
    argv = [exe, "start"] if len(sys.argv) < 2 else [exe] + sys.argv[1:]
    os.execv(exe, argv)


@require_allowed
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.edited_message or update.message
    chat_id = str(update.effective_chat.id)
    text = msg.text or ""
    is_edit = update.edited_message is not None
    logger.info("%s [%s]: %s", "Edit" if is_edit else "Incoming", chat_id, text[:80])
    await _react(msg)
    try:
        async with _TypingLoop(context.bot, chat_id):
            reply = await agent_run(chat_id=chat_id, user_message=text)
        logger.info("Reply [%s]: %s", chat_id, reply[:80])
        await _react_done(msg, reply)
        await _reply_chunked(msg, reply)
        used, fill = _context_fill(chat_id)
        if fill >= CONTEXT_WARN_THRESHOLD:
            pct = fill * 100
            warn = f"Context at {pct:.0f}% ({used:,} / {CONTEXT_WINDOW_TOKENS:,} tokens). Consider /reset soon."
            await msg.reply_text(warn)
    except Exception as e:
        logger.exception("Error handling message: %s", e)
        await msg.reply_text(_classify_error(e))


@require_allowed
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle documents: download to uploads/, pass path to agent."""
    chat_id = str(update.effective_chat.id)
    doc = update.message.document
    caption = update.message.caption or ""
    await _react(update.message)
    try:
        file = await context.bot.get_file(doc.file_id)
        raw_name = doc.file_name or f"{doc.file_unique_id}.bin"
        safe_name = Path(raw_name).name
        dest = workspace.UPLOADS_DIR / safe_name
        await file.download_to_drive(str(dest))
        mime = doc.mime_type or "application/octet-stream"
        agent_msg = f"[User sent file '{safe_name}' ({mime}). Saved to: {dest}]\n\n{caption}"
        async with _TypingLoop(context.bot, chat_id):
            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
        await _react_done(update.message, reply)
        await _reply_chunked(update.message, reply)
    except Exception as e:
        logger.exception("Error handling document: %s", e)
        await update.message.reply_text(_classify_error(e))


@require_allowed
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photos: save to uploads/, pass path to agent for native vision."""
    chat_id = str(update.effective_chat.id)
    caption = update.message.caption or ""
    await _react(update.message)
    try:
        photo = update.message.photo[-1]  # highest resolution
        file = await context.bot.get_file(photo.file_id)
        dest = workspace.UPLOADS_DIR / f"{photo.file_unique_id}.jpg"
        await file.download_to_drive(str(dest))
        agent_msg = f"[User sent a photo. Saved to: {dest}]\n\n{caption}"
        async with _TypingLoop(context.bot, chat_id):
            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
        await _react_done(update.message, reply)
        await _reply_chunked(update.message, reply)
    except Exception as e:
        logger.exception("Error handling photo: %s", e)
        await update.message.reply_text(_classify_error(e))
