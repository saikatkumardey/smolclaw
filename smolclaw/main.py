"""CLI entrypoint. smolclaw setup | smolclaw start"""
from __future__ import annotations

import logging
import os

import typer
from dotenv import load_dotenv
from rich.logging import RichHandler

app = typer.Typer(name="smolclaw", help="Your personal AI agent.", add_completion=False)


@app.command()
def setup() -> None:
    """Interactive setup wizard. Run this first."""
    from .setup import run
    run()


@app.command()
def start() -> None:
    """Start the Telegram bot."""
    from . import workspace
    workspace.init()
    load_dotenv(workspace.HOME / ".env")
    logging.basicConfig(level=logging.INFO, handlers=[RichHandler()])
    logger = logging.getLogger("smolclaw")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        typer.echo("No TELEGRAM_BOT_TOKEN found. Run: smolclaw setup")
        raise typer.Exit(1)

    from telegram import Update
    from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

    from .agent import run as agent_run
    from .scheduler import setup_scheduler

    ALLOWED = set(filter(None, os.getenv("ALLOWED_USER_IDS", "").split(",")))

    def _allowed(update: Update) -> bool:
        return not ALLOWED or str(update.effective_chat.id) in ALLOWED

    async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("SmolClaw online. Say hello.")

    async def on_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        model = os.getenv("LITELLM_MODEL", "not set")
        await update.message.reply_text(f"Model: {model}\nStatus: running")

    async def on_reload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Memory and skills reloaded.")

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        chat_id = str(update.effective_chat.id)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            reply = agent_run(chat_id=chat_id, user_message=update.message.text or "")
            for i in range(0, max(len(reply), 1), 4000):
                await update.message.reply_text(reply[i : i + 4000])
        except Exception as e:
            logger.error("Error: %s", e)
            await update.message.reply_text(f"Error: {e}")

    bot = ApplicationBuilder().token(token).build()
    bot.add_handler(CommandHandler("start", on_start))
    bot.add_handler(CommandHandler("status", on_status))
    bot.add_handler(CommandHandler("reload", on_reload))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    setup_scheduler().start()
    logger.info("SmolClaw running.")
    bot.run_polling()


def main() -> None:
    app()
