"""CLI entrypoint. smolclaw setup | smolclaw start"""
from __future__ import annotations

import asyncio
import base64
import os
import signal
import sys
import tempfile

import typer
from dotenv import load_dotenv

app = typer.Typer(name="smolclaw", help="Your personal AI agent.", add_completion=False)


# ---------------------------------------------------------------------------
# --version callback (Item 5)
# ---------------------------------------------------------------------------

def version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version
        typer.echo(f"smolclaw {version('smolclaw')}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        callback=version_callback,
        is_eager=True,
        help="Show version.",
    ),
) -> None:
    pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def setup() -> None:
    """Interactive setup wizard. Run this first."""
    from .setup import run
    run()


@app.command()
def update() -> None:
    """Pull latest smolclaw from GitHub and reinstall."""
    source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")
    typer.echo(f"Updating from {source}...")
    import subprocess
    result = subprocess.run(["uv", "tool", "install", "--upgrade", source], text=True)
    if result.returncode != 0:
        typer.echo("Update failed.")
        raise typer.Exit(1)
    typer.echo("Updated. Run 'smolclaw start' to restart.")


@app.command()
def start() -> None:
    """Start the Telegram bot."""
    from . import workspace
    workspace.init()
    load_dotenv(workspace.HOME / ".env")
    from loguru import logger
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )

    # ---------------------------------------------------------------------------
    # Item 8: Pre-flight checks
    # ---------------------------------------------------------------------------
    env_path = workspace.HOME / ".env"
    if not env_path.exists():
        typer.echo("No .env found. Run `smolclaw setup` first.")
        raise typer.Exit(1)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        typer.echo("No Telegram bot token. Run `smolclaw setup` to configure.")
        raise typer.Exit(1)

    model = os.getenv("LITELLM_MODEL", "")
    if not model:
        typer.echo("No AI model configured. Run `smolclaw setup` to configure.")
        raise typer.Exit(1)

    from telegram import Update
    from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

    from .agent import run as agent_run, _agents, session_log
    from .scheduler import setup_scheduler

    ALLOWED = set(filter(None, os.getenv("ALLOWED_USER_IDS", "").split(",")))

    def _allowed(update: Update) -> bool:
        return not ALLOWED or str(update.effective_chat.id) in ALLOWED

    # ---------------------------------------------------------------------------
    # Item 10: Bot command handlers
    # ---------------------------------------------------------------------------

    async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("SmolClaw online. Say hello.")

    async def on_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "SmolClaw — your personal AI agent.\n\n"
            "I can run shell commands, read/write files, search the web, run Python, "
            "and learn any CLI tool you point me at.\n\n"
            "Commands:\n"
            "/help — this message\n"
            "/status — current config and stats\n"
            "/reset — clear my memory for this chat\n\n"
            "Or just talk to me."
        )
        await update.message.reply_text(text)

    async def on_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        from . import workspace as ws
        from .tools import TOOLS_LIST
        model_name = os.getenv("LITELLM_MODEL", "not set")
        tool_count = len(TOOLS_LIST)
        skills_dir = ws.SKILLS_DIR
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir()) if skills_dir.exists() else 0
        memory_path = ws.MEMORY
        try:
            memory_lines = len(memory_path.read_text().splitlines())
        except FileNotFoundError:
            memory_lines = 0
        text = (
            f"Model: {model_name}\n"
            f"Workspace: {ws.HOME}\n"
            f"Tools: {tool_count}\n"
            f"Skills: {skill_count}\n"
            f"Memory: {memory_lines} lines"
        )
        await update.message.reply_text(text)

    async def on_reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        chat_id = str(update.effective_chat.id)
        session_log(chat_id, "system", "SESSION_RESET")
        if chat_id in _agents:
            del _agents[chat_id]
        await update.message.reply_text("Memory cleared. Starting fresh.")

    async def on_reload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("Memory and skills reloaded.")

    async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        await update.message.reply_text("Saving handover and updating...")
        chat_id = str(update.effective_chat.id)
        await asyncio.to_thread(
            agent_run,
            chat_id=chat_id,
            user_message="Save a handover note summarising current context, then call self_update.",
        )

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            logger.warning("Rejected message from {}", update.effective_chat.id)
            return
        chat_id = str(update.effective_chat.id)
        text = update.message.text or ""
        logger.info("Incoming [{}]: {}", chat_id, text[:80])
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            reply = await asyncio.to_thread(agent_run, chat_id=chat_id, user_message=text)
            logger.info("Reply [{}]: {}", chat_id, reply[:80])
            for i in range(0, max(len(reply), 1), 4000):
                await update.message.reply_text(reply[i : i + 4000])
        except Exception as e:
            logger.exception("Error handling message: {}", e)
            await update.message.reply_text(f"Error: {e}")

    async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photos: download, describe via vision model, pass to agent."""
        if not _allowed(update):
            return
        chat_id = str(update.effective_chat.id)
        caption = update.message.caption or "User sent a photo."
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            photo = update.message.photo[-1]  # highest resolution
            file = await context.bot.get_file(photo.file_id)

            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=str(workspace.HOME)) as tmp:
                await file.download_to_drive(tmp.name)
                image_path = tmp.name

            # Try vision call via litellm
            try:
                import litellm
                with open(image_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()

                vision_model = os.getenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-6")
                resp = litellm.completion(
                    model=vision_model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": f"Describe this image concisely. User's message: {caption}"},
                        ],
                    }],
                    max_tokens=300,
                )
                description = resp.choices[0].message.content
                agent_msg = f"[User sent a photo: {description}]\n\n{caption}"
            except Exception as ve:
                logger.warning("Vision call failed: {}", ve)
                agent_msg = f"[User sent a photo saved at {image_path}. Vision not available.]\n\n{caption}"

            reply = await asyncio.to_thread(agent_run, chat_id=chat_id, user_message=agent_msg)
            for i in range(0, max(len(reply), 1), 4000):
                await update.message.reply_text(reply[i : i + 4000])
        except Exception as e:
            logger.exception("Error handling photo: {}", e)
            await update.message.reply_text(f"Error processing photo: {e}")

    # ---------------------------------------------------------------------------
    # Build and run the bot (Item 8: wrap in try/except)
    # ---------------------------------------------------------------------------
    try:
        bot = ApplicationBuilder().token(token).build()
        bot.add_handler(CommandHandler("start", on_start))
        bot.add_handler(CommandHandler("help", on_help))
        bot.add_handler(CommandHandler("status", on_status))
        bot.add_handler(CommandHandler("reset", on_reset))
        bot.add_handler(CommandHandler("reload", on_reload))
        bot.add_handler(CommandHandler("update", on_update))
        bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
        bot.add_handler(MessageHandler(filters.PHOTO, on_photo))

        scheduler = setup_scheduler()
        scheduler.start()

        # Graceful shutdown handler
        def _shutdown(signum, frame):
            logger.info("Shutdown signal received. Saving handover...")
            try:
                from .handover import save
                save("Process shutting down. No pending tasks.")
            except Exception:
                pass
            scheduler.shutdown(wait=False)
            logger.info("SmolClaw stopped.")
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        logger.info("SmolClaw running.")
        bot.run_polling()
    except Exception as e:
        err = str(e).lower()
        if "unauthorized" in err or "token" in err:
            typer.echo("Bot token rejected by Telegram. Check TELEGRAM_BOT_TOKEN in your .env.")
        elif "network" in err or "connect" in err or "timeout" in err:
            typer.echo(f"Network error connecting to Telegram: {e}\nCheck your internet connection and try again.")
        else:
            typer.echo(f"Failed to start bot: {e}")
        raise typer.Exit(1)


def main() -> None:
    app()
