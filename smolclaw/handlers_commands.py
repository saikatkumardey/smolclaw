"""Command handlers extracted from handlers.py — /help, /status, /crons, /tasks, /context, /model, /models, /effort, /restart, /update, /btw."""
from __future__ import annotations

import asyncio
import logging
import os

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
    list_tasks,
    set_effort,
    set_model,
)
from .auth import require_allowed
from .session_state import SessionState
from .tool_loader import load_custom_tools
from .tools_sdk import CUSTOM_TOOLS
from .version import check_remote_version as _check_remote_version
from .version import get_update_summary as _get_update_summary
from .version import local_version as _local_version

logger = logging.getLogger(__name__)

CONTEXT_WARN_THRESHOLD = 0.80


def _format_last_turn(result) -> str:
    """Format cost/usage info for the last turn."""
    if not result:
        return ""
    usage = result.usage or {}
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    cache_str = f" | cache down {cache_read} up {cache_write}" if (cache_read or cache_write) else ""
    return f"\nLast turn: {inp}in/{out}out | {result.num_turns} turns | {result.duration_ms}ms{cache_str}"


def _context_fill(chat_id: str) -> tuple[int, float]:
    """Return (used_tokens, fill_fraction) from last result for a chat."""
    result = get_last_result(chat_id)
    if not result:
        return 0, 0.0
    usage = result.usage or {}
    used = usage.get("cache_read_input_tokens", 0) + usage.get("input_tokens", 0)
    return used, used / _CONTEXT_WINDOW_TOKENS


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
    skills_dir = workspace.SKILLS_DIR
    skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir()) if skills_dir.exists() else 0
    try:
        memory_lines = len(workspace.MEMORY.read_text().splitlines())
    except FileNotFoundError:
        memory_lines = 0
    dynamic_names = ", ".join(t.name for t in dynamic_tools) if dynamic_tools else "none"
    cost_line = _format_last_turn(get_last_result(str(update.effective_chat.id)))
    usage_today = SessionState.load().get_usage_today()
    today_line = f"\nToday: {usage_today['input_tokens']}in/{usage_today['output_tokens']}out | {usage_today['turns']} turns"
    from .browser import BrowserManager
    text = (
        f"Model: {get_current_model()}\n"
        f"Effort: {get_current_effort()}\n"
        f"Workspace: {workspace.HOME}\n"
        f"Browser: {BrowserManager.get().backend}\n"
        f"Built-in tools: 5\n"
        f"Custom SDK tools: {len(CUSTOM_TOOLS) + 1}\n"
        f"Dynamic tools: {len(dynamic_tools)} ({dynamic_names})\n"
        f"Skills: {skill_count}\n"
        f"Memory: {memory_lines} lines"
        f"{cost_line}"
        f"{today_line}"
    )
    await update.message.reply_text(text)


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


async def on_model_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    from .auth import is_allowed
    cb = update.callback_query
    await cb.answer()
    if not (cb.data or "").startswith("model:"):
        return
    if not is_allowed(update.effective_chat.id):
        await cb.edit_message_text("Not authorised.")
        return
    selected = cb.data[len("model:"):]
    if selected not in {mid for mid, _ in AVAILABLE_MODELS}:
        await cb.edit_message_text("Unknown model.")
        return
    await set_model(selected)
    label = next(lbl for mid, lbl in AVAILABLE_MODELS if mid == selected)
    await cb.edit_message_text(
        f"✓ Switched to *{label}*\n`{selected}`\n\nAll sessions reset — next message uses the new model.",
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
    cb = update.callback_query
    await cb.answer()
    if not (cb.data or "").startswith("effort:"):
        return
    if not is_allowed(update.effective_chat.id):
        await cb.edit_message_text("Not authorised.")
        return
    selected = cb.data[len("effort:"):]
    if selected not in {eid for eid, _ in AVAILABLE_EFFORTS}:
        await cb.edit_message_text("Unknown effort.")
        return
    await set_effort(selected)
    label = next(lbl for eid, lbl in AVAILABLE_EFFORTS if eid == selected)
    await cb.edit_message_text(
        f"✓ Effort set to *{label}*\n\nAll sessions reset — next message uses the new effort level.",
        parse_mode="Markdown",
    )


@require_allowed
async def on_restart(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    import signal
    await update.message.reply_text("Restarting…")
    try:
        from .handover import save
        save("Process restarting via /restart command.")
    except Exception:
        logger.debug("failed to save handover before restart", exc_info=True)
    os.kill(os.getpid(), signal.SIGTERM)


@require_allowed
async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import signal
    import subprocess as _subprocess

    from .handover import save as save_handover

    old_version = _local_version()
    source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")

    placeholder = await update.message.reply_text("Checking for updates…")

    async def _edit(text: str) -> None:
        try:
            await placeholder.edit_text(text)
        except Exception:
            logger.debug("failed to edit placeholder text", exc_info=True)

    remote = await asyncio.to_thread(_check_remote_version, source)
    if remote and remote == old_version:
        await _edit(f"Already on latest (v{old_version}).")
        return

    await _edit("Update available — installing…")
    try:
        result = await asyncio.to_thread(
            _subprocess.run,
            ["uv", "tool", "install", "--upgrade", source],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        await _edit(f"Update failed: {e}")
        return

    if result.returncode != 0:
        await _edit(f"Update failed:\n{result.stderr[:500]}")
        return

    summary = await asyncio.to_thread(_get_update_summary, source, old_version)

    try:
        save_handover(f"Updated via /update command.\n\n{summary}\n\nPENDING: none")
    except Exception as e:
        logger.warning("Handover save failed: %s", e)

    await _edit(f"Updated. Restarting…\n\n{summary}")
    os.kill(os.getpid(), signal.SIGTERM)
