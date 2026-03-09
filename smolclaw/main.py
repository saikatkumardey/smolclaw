"""CLI entrypoint. smolclaw setup | smolclaw start"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv



async def _reply_chunked(message, text: str) -> None:
    """Send text in ≤4000-char chunks with Markdown, falling back to plain text."""
    formatted = _to_telegram_md(text)
    for i in range(0, max(len(formatted), 1), 4000):
        chunk = formatted[i : i + 4000]
        try:
            await message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await message.reply_text(chunk)


def _to_telegram_md(text: str) -> str:
    """Convert CommonMark bold/italic to Telegram Markdown v1 format."""
    # **bold** → *bold*  (Telegram v1 uses single asterisk)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # ### headings → *Heading* (bold, since Telegram has no headings)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    return text

app = typer.Typer(name="smolclaw", help="Your personal AI agent.", add_completion=False)


# ---------------------------------------------------------------------------
# --version callback
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


@app.command(name="setup-token")
def setup_token() -> None:
    """Configure Claude authentication (API key or Claude.ai subscription login)."""
    import getpass
    import subprocess
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    import questionary

    from . import workspace
    from .setup import _read_env, _write_env

    workspace.init()
    console = Console()
    env_path = workspace.HOME / ".env"
    env = _read_env(env_path)

    console.print()
    console.print(Panel(
        "[bold]SmolClaw uses [cyan]claude-agent-sdk[/cyan] to power your AI agent.[/bold]\n\n"
        "You need to authenticate with Claude. Choose how:\n\n"
        "  [bold cyan]1. API key[/bold cyan]   — Paste an [link=https://console.anthropic.com/settings/keys]Anthropic API key[/link] "
        "(works with any Anthropic account)\n"
        "  [bold cyan]2. Login[/bold cyan]     — Sign in with your Claude.ai account "
        "[dim](Claude Pro / Max / Team subscription)[/dim]",
        title="[blue]Claude Authentication[/blue]",
        border_style="blue",
        padding=(1, 2),
    ))
    console.print()

    try:
        choice = questionary.select(
            "How would you like to authenticate?",
            choices=[
                questionary.Choice("Paste an API key  (console.anthropic.com/settings/keys)", value="key"),
                questionary.Choice("Login with Claude account  (Claude Pro / Max subscription)", value="login"),
            ],
            style=questionary.Style([
                ("selected", "fg:cyan bold"),
                ("pointer", "fg:cyan bold"),
                ("question", "fg:blue bold"),
            ]),
        ).ask()
    except KeyboardInterrupt:
        console.print()
        raise typer.Exit(0)

    if choice is None:
        raise typer.Exit(0)

    if choice == "key":
        existing = env.get("ANTHROPIC_API_KEY", "")
        if existing:
            masked = existing[:12] + "…"
            console.print(f"\n  [dim]Current key:[/dim] [green]{masked}[/green]")
            try:
                overwrite = questionary.confirm("Replace it?", default=False).ask()
            except KeyboardInterrupt:
                raise typer.Exit(0)
            if not overwrite:
                console.print("  [dim]Keeping existing key.[/dim]")
                raise typer.Exit(0)

        console.print()
        try:
            api_key = getpass.getpass("  Paste your ANTHROPIC_API_KEY (hidden): ").strip()
        except KeyboardInterrupt:
            console.print()
            raise typer.Exit(0)

        if not api_key:
            console.print("  [yellow]No key entered — nothing saved.[/yellow]")
            raise typer.Exit(1)

        env["ANTHROPIC_API_KEY"] = api_key
        _write_env(env_path, env)
        console.print(f"\n  [bold green]✓[/bold green]  API key saved to [dim]{env_path}[/dim]")

    else:  # login
        console.print()
        console.print("  [dim]Opening browser for Claude.ai login…[/dim]\n")
        try:
            subprocess.run(["claude", "auth", "login"], check=True)
        except FileNotFoundError:
            console.print("  [red]✗[/red]  [bold]claude[/bold] CLI not found. The SDK should have bundled it.")
            console.print("  Try: [bold]pip install --upgrade claude-agent-sdk[/bold]")
            raise typer.Exit(1)
        except subprocess.CalledProcessError:
            console.print("  [red]✗[/red]  Login failed or was cancelled.")
            raise typer.Exit(1)
        console.print("\n  [bold green]✓[/bold green]  Logged in. SmolClaw will use your Claude subscription.")

    console.print()
    console.print(Rule(style="green"))
    console.print(
        "\n  [bold]Run your agent:[/bold]  [bold cyan]smolclaw start[/bold cyan]\n"
    )


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
def doctor() -> None:
    """Check workspace health: files, auth, tools, and state."""
    from .doctor import run
    raise typer.Exit(run())


@app.command()
def start() -> None:
    """Start the Telegram bot."""
    from . import workspace
    workspace.init()
    load_dotenv(workspace.HOME / ".env", override=True)
    from .config import Config
    cfg = Config.load()
    os.environ["SMOLCLAW_MODEL"] = cfg.get("model")
    from loguru import logger
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    )

    # ---------------------------------------------------------------------------
    # Pre-flight checks
    # ---------------------------------------------------------------------------
    env_path = workspace.HOME / ".env"
    if not env_path.exists():
        typer.echo("No .env found. Run `smolclaw setup` first.")
        raise typer.Exit(1)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        typer.echo("No Telegram bot token. Run `smolclaw setup` to configure.")
        raise typer.Exit(1)

    if not os.getenv("ALLOWED_USER_IDS", "").strip():
        typer.echo("ALLOWED_USER_IDS is not set. Run `smolclaw setup` to configure your Telegram user ID.")
        raise typer.Exit(1)

    from telegram import Update
    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

    from .agent import (
        run as agent_run,
        reset_session,
        interrupt_session,
        get_last_result,
        session_log,
        AVAILABLE_MODELS,
        get_current_model,
        set_model,
    )
    from .scheduler import setup_scheduler

    ALLOWED = set(filter(None, os.getenv("ALLOWED_USER_IDS", "").split(",")))

    def _allowed(update: Update) -> bool:
        return str(update.effective_chat.id) in ALLOWED

    # ---------------------------------------------------------------------------
    # Bot command handlers
    # ---------------------------------------------------------------------------

    async def on_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        await update.message.reply_text("SmolClaw online. Say hello.")

    async def on_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        text = (
            "SmolClaw — your personal AI agent.\n\n"
            "I can run shell commands, read/write files, search the web, "
            "and learn any CLI tool you point me at.\n\n"
            "Commands:\n"
            "/help — this message\n"
            "/status — current config and stats\n"
            "/model — show current Claude model\n"
            "/models — switch Claude model\n"
            "/reset — clear conversation history\n"
            "/restart — restart the bot process\n\n"
            "Or just talk to me."
        )
        await update.message.reply_text(text)

    async def on_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        from . import workspace as ws
        from .tool_loader import load_custom_tools
        from .tools_sdk import CUSTOM_TOOLS
        from .session_state import SessionState
        dynamic_tools = load_custom_tools()
        builtin_count = 5  # Bash, Read, Write, WebSearch, WebFetch
        custom_sdk_count = len(CUSTOM_TOOLS) + 1  # +1 for spawn_task
        skills_dir = ws.SKILLS_DIR
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir()) if skills_dir.exists() else 0
        memory_path = ws.MEMORY
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
        # Daily usage from session state
        usage_today = SessionState.load().get_usage_today()
        today_line = (
            f"\nToday: {usage_today['input_tokens']}in/{usage_today['output_tokens']}out | {usage_today['turns']} turns"
        )
        text = (
            f"Model: {current_model}\n"
            f"Workspace: {ws.HOME}\n"
            f"Built-in tools: {builtin_count}\n"
            f"Custom SDK tools: {custom_sdk_count}\n"
            f"Dynamic tools: {len(dynamic_tools)}\n"
            f"Skills: {skill_count}\n"
            f"Memory: {memory_lines} lines"
            f"{cost_line}"
            f"{today_line}"
        )
        await update.message.reply_text(text)

    async def on_reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        chat_id = str(update.effective_chat.id)
        session_log(chat_id, "system", "SESSION_RESET")
        await reset_session(chat_id)
        await update.message.reply_text("Memory cleared. Starting fresh.")

    async def on_cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        chat_id = str(update.effective_chat.id)
        interrupted = await interrupt_session(chat_id)
        await update.message.reply_text("Cancelled." if interrupted else "Nothing to cancel.")

    async def on_reload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        await update.message.reply_text("Memory and skills reloaded.")

    async def on_model(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        current = get_current_model()
        label = next((lbl for mid, lbl in AVAILABLE_MODELS if mid == current), current)
        await update.message.reply_text(
            f"Current model: *{label}*\n`{current}`",
            parse_mode="Markdown",
        )

    async def on_models(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
        query = update.callback_query
        await query.answer()
        if not (query.data or "").startswith("model:"):
            return
        if not _allowed(update):
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

    async def on_restart(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
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
        import shutil as _shutil
        exe = _shutil.which("smolclaw") or sys.argv[0]
        argv = [exe, "start"] if len(sys.argv) < 2 else [exe] + sys.argv[1:]
        os.execv(exe, argv)

    async def on_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        await update.message.reply_text("Saving handover and updating...")
        chat_id = str(update.effective_chat.id)
        await agent_run(
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
            # Send a placeholder; edit it in-place as partial text arrives
            sent = await update.message.reply_text("…")
            last_edit: list[float] = [0.0]  # mutable for closure

            async def on_partial(partial: str) -> None:
                now = asyncio.get_running_loop().time()
                if now - last_edit[0] < 1.5:
                    return
                last_edit[0] = now
                try:
                    await sent.edit_text(_to_telegram_md(partial[:4000]), parse_mode="Markdown")
                except Exception:
                    pass

            reply = await agent_run(chat_id=chat_id, user_message=text, on_partial=on_partial)
            logger.info("Reply [{}]: {}", chat_id, reply[:80])
            # Final edit of first chunk, then reply for overflow
            formatted = _to_telegram_md(reply)
            first, rest = formatted[:4000], formatted[4000:]
            try:
                await sent.edit_text(first, parse_mode="Markdown")
            except Exception:
                await sent.edit_text(first)
            for i in range(0, len(rest), 4000):
                chunk = rest[i : i + 4000]
                try:
                    await update.message.reply_text(chunk, parse_mode="Markdown")
                except Exception:
                    await update.message.reply_text(chunk)
        except Exception as e:
            logger.exception("Error handling message: {}", e)
            await update.message.reply_text("Something went wrong. Check the logs.")

    async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle documents: download to uploads/, pass path to agent. Vision for images."""
        if not _allowed(update):
            return
        chat_id = str(update.effective_chat.id)
        doc = update.message.document
        caption = update.message.caption or ""
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            file = await context.bot.get_file(doc.file_id)
            raw_name = doc.file_name or f"{doc.file_unique_id}.bin"
            safe_name = Path(raw_name).name
            dest = workspace.UPLOADS_DIR / safe_name
            await file.download_to_drive(str(dest))

            mime = doc.mime_type or "application/octet-stream"
            agent_msg = f"[User sent file '{safe_name}' ({mime}). Saved to: {dest}]\n\n{caption}"

            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
            await _reply_chunked(update.message, reply)
        except Exception as e:
            logger.exception("Error handling document: {}", e)
            await update.message.reply_text("Something went wrong. Check the logs.")

    async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photos: save to uploads/, pass path to agent for native vision."""
        if not _allowed(update):
            return
        chat_id = str(update.effective_chat.id)
        caption = update.message.caption or ""
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            photo = update.message.photo[-1]  # highest resolution
            file = await context.bot.get_file(photo.file_id)
            dest = workspace.UPLOADS_DIR / f"{photo.file_unique_id}.jpg"
            await file.download_to_drive(str(dest))
            agent_msg = f"[User sent a photo. Saved to: {dest}]\n\n{caption}"
            reply = await agent_run(chat_id=chat_id, user_message=agent_msg)
            await _reply_chunked(update.message, reply)
        except Exception as e:
            logger.exception("Error handling photo: {}", e)
            await update.message.reply_text("Something went wrong. Check the logs.")

    # ---------------------------------------------------------------------------
    # Build and run the bot
    # ---------------------------------------------------------------------------
    try:
        bot = ApplicationBuilder().token(token).build()
        bot.add_handler(CommandHandler("start", on_start))
        bot.add_handler(CommandHandler("help", on_help))
        bot.add_handler(CommandHandler("status", on_status))
        bot.add_handler(CommandHandler("model", on_model))
        bot.add_handler(CommandHandler("models", on_models))
        bot.add_handler(CommandHandler("reset", on_reset))
        bot.add_handler(CommandHandler("cancel", on_cancel))
        bot.add_handler(CommandHandler("reload", on_reload))
        bot.add_handler(CommandHandler("restart", on_restart))
        bot.add_handler(CommandHandler("update", on_update))
        bot.add_handler(CallbackQueryHandler(on_model_callback, pattern="^model:"))
        bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
        bot.add_handler(MessageHandler(filters.PHOTO, on_photo))
        bot.add_handler(MessageHandler(filters.Document.ALL, on_document))

        scheduler = setup_scheduler()

        # post_init runs inside run_polling()'s event loop — safe to use async bot calls here
        from telegram import BotCommand

        async def _post_init(app) -> None:
            await app.bot.set_my_commands([
                BotCommand("start",   "Wake the bot"),
                BotCommand("help",    "Show available commands"),
                BotCommand("status",  "Show model, workspace, tool counts"),
                BotCommand("model",   "Show current Claude model"),
                BotCommand("models",  "Switch Claude model"),
                BotCommand("reset",   "Clear conversation history"),
                BotCommand("cancel",  "Cancel the current running task"),
                BotCommand("reload",  "Reload skills and memory"),
                BotCommand("restart", "Restart the bot process"),
            ])
            scheduler.start()
            # Notify user on startup (confirms restart/update completed)
            default_chat = os.getenv("ALLOWED_USER_IDS", "").split(",")[0].strip()
            if default_chat:
                from .handover import exists as handover_exists
                msg = "Back online. Handover note loaded — resuming on your next message." if handover_exists() else "Online."
                await app.bot.send_message(chat_id=default_chat, text=msg)

        bot.post_init = _post_init

        # Graceful shutdown hook — runs after run_polling() exits cleanly
        async def _post_shutdown(app) -> None:
            logger.info("Shutdown: saving handover...")
            try:
                from .handover import save
                save("Process shutting down. No pending tasks.")
            except Exception:
                pass
            scheduler.shutdown(wait=False)
            logger.info("SmolClaw stopped.")

        bot.post_shutdown = _post_shutdown

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
