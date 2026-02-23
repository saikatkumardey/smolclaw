"""Telegram bot entrypoint."""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from rich.logging import RichHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from .agent import run
from .scheduler import setup_scheduler

load_dotenv()
logging.basicConfig(level=logging.INFO, handlers=[RichHandler()])
logger = logging.getLogger("smolclaw")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED = set(filter(None, os.getenv("ALLOWED_USER_IDS", "").split(",")))


def _allowed(update: Update) -> bool:
    return not ALLOWED or str(update.effective_chat.id) in ALLOWED


async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("SmolClaw online.")


async def on_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    model = os.getenv("LITELLM_MODEL", "not set")
    await update.message.reply_text(f"Model: {model}\nStatus: running")


async def on_reload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    # Skills and memory reload on every request already; this confirms it.
    await update.message.reply_text("Memory and skills reloaded.")


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        return
    chat_id = str(update.effective_chat.id)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        reply = run(chat_id=chat_id, user_message=update.message.text or "")
        for i in range(0, max(len(reply), 1), 4000):
            await update.message.reply_text(reply[i : i + 4000])
    except Exception as e:
        logger.error("Error: %s", e)
        await update.message.reply_text(f"Error: {e}")


def main() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("status", on_status))
    app.add_handler(CommandHandler("reload", on_reload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    setup_scheduler().start()
    logger.info("SmolClaw running.")
    app.run_polling()
