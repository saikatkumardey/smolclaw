"""CLI entrypoint. smolclaw setup | smolclaw start"""
from __future__ import annotations

import os
import shutil
import sys

# Clear CLAUDECODE early — if set, claude-agent-sdk subprocess refuses to start
# with "cannot be launched inside another Claude Code session".
os.environ.pop("CLAUDECODE", None)

import typer
from dotenv import load_dotenv

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

    import questionary
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule

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
def start(
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in foreground (blocking)."),
) -> None:
    """Start the Telegram bot (daemonizes by default)."""
    from . import workspace
    workspace.init()

    if not foreground:
        from .daemon import is_running, write_pid
        running, pid = is_running()
        if running:
            typer.echo(f"SmolClaw is already running (PID {pid}). Use 'smolclaw restart' to restart.")
            raise typer.Exit(1)

        import subprocess
        exe = shutil.which("smolclaw") or sys.argv[0]
        log_path = workspace.LOG_FILE
        with open(log_path, "a") as log_fh:
            proc = subprocess.Popen(
                [exe, "start", "--foreground"],
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,
            )
        write_pid(proc.pid)
        typer.echo(f"SmolClaw started (PID {proc.pid}). Run 'smolclaw logs' to view output.")
        return

    # Do NOT set SIGCHLD to SIG_IGN here. While it auto-reaps zombies, it also
    # discards exit status before asyncio's PidfdChildWatcher can call waitpid(),
    # causing every SDK subprocess to report returncode 255 (ProcessError).
    # The SDK already calls wait() on its child processes, so zombies are not
    # an issue for normal agent sessions.

    # Ensure user-local bin dirs are in PATH — systemd services get a minimal
    # PATH that misses ~/.npm-global/bin, ~/.local/bin, etc. SDK subprocesses
    # (claude, html2md) need these to resolve correctly.
    _user_bin_dirs = [
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/.local/bin"),
    ]
    current_path = os.environ.get("PATH", "")
    missing = [d for d in _user_bin_dirs if d not in current_path.split(os.pathsep)]
    if missing:
        os.environ["PATH"] = os.pathsep.join(missing) + os.pathsep + current_path

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
    logger.add(
        workspace.LOG_FILE,
        level="INFO",
        format="{time:HH:mm:ss} | {level:<7} | {name} - {message}",
        rotation="10 MB",
        retention=3,
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

    from telegram.ext import (
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        MessageReactionHandler,
        filters,
    )

    from . import handlers as h
    from .scheduler import setup_scheduler

    # ---------------------------------------------------------------------------
    # Build and run the bot
    # ---------------------------------------------------------------------------
    try:
        bot = ApplicationBuilder().token(token).concurrent_updates(True).build()
        bot.add_handler(CommandHandler("start", h.on_start))
        bot.add_handler(CommandHandler("help", h.on_help))
        bot.add_handler(CommandHandler("status", h.on_status))
        bot.add_handler(CommandHandler("model", h.on_model))
        bot.add_handler(CommandHandler("models", h.on_models))
        bot.add_handler(CommandHandler("reset", h.on_reset))
        bot.add_handler(CommandHandler("cancel", h.on_cancel))
        bot.add_handler(CommandHandler("tasks", h.on_tasks))
        bot.add_handler(CommandHandler("reload", h.on_reload))
        bot.add_handler(CommandHandler("restart", h.on_restart))
        bot.add_handler(CommandHandler("update", h.on_update))
        bot.add_handler(CommandHandler("btw", h.on_btw))
        bot.add_handler(CommandHandler("context", h.on_context))
        bot.add_handler(CommandHandler("effort", h.on_effort))
        bot.add_handler(CommandHandler("efforts", h.on_efforts))
        bot.add_handler(CallbackQueryHandler(h.on_model_callback, pattern="^model:"))
        bot.add_handler(CallbackQueryHandler(h.on_effort_callback, pattern="^effort:"))
        bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.on_message))
        bot.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT & ~filters.COMMAND, h.on_message))
        bot.add_handler(MessageHandler(filters.PHOTO, h.on_photo))
        bot.add_handler(MessageHandler(filters.Document.ALL, h.on_document))
        bot.add_handler(MessageReactionHandler(h.on_reaction))

        scheduler = setup_scheduler()

        # post_init runs inside the running event loop — safe for async calls
        async def _post_init(app) -> None:
            from telegram import BotCommand
            await app.bot.set_my_commands([
                BotCommand("start",   "Wake the bot"),
                BotCommand("help",    "Show available commands"),
                BotCommand("status",  "Show model, workspace, tool counts"),
                BotCommand("model",   "Show current Claude model"),
                BotCommand("models",  "Switch Claude model"),
                BotCommand("reset",   "Clear conversation history"),
                BotCommand("cancel",  "Cancel the current running task"),
                BotCommand("tasks",   "List background tasks"),
                BotCommand("reload",  "Reload skills and memory"),
                BotCommand("restart", "Restart the bot process"),
                BotCommand("update",  "Update smolclaw and restart"),
                BotCommand("btw",     "Ask a side question (no history)"),
                BotCommand("context", "Show context window usage"),
                BotCommand("effort",  "Set thinking effort level"),
            ])
            scheduler.start()
            from .auth import default_chat_id
            default_chat = default_chat_id()
            if default_chat:
                from importlib.metadata import version as pkg_version

                from .handover import exists as handover_exists
                from .handover import load as handover_load
                try:
                    ver = pkg_version("smolclaw")
                except Exception:
                    ver = "?"
                parts = [f"Back online. v{ver}"]
                if handover_exists():
                    handover = handover_load()
                    # Extract version line from handover if present
                    for line in handover.splitlines():
                        if "->" in line and any(c.isdigit() for c in line):
                            parts.append(line.strip())
                            break
                    parts.append("Handover loaded — picking up where I left off.")
                try:
                    await app.bot.send_message(chat_id=default_chat, text="\n".join(parts))
                except Exception:
                    pass

        bot.post_init = _post_init

        # Graceful shutdown hook — runs after run_polling() exits cleanly
        async def _post_shutdown(app) -> None:
            logger.info("Shutdown: saving handover...")
            try:
                from .handover import save
                save("Process shutting down. No pending tasks.")
            except Exception:
                pass
            try:
                from .browser import BrowserManager
                await BrowserManager.get().close_all()
            except Exception:
                pass
            scheduler.shutdown(wait=False)
            from .daemon import delete_pid
            delete_pid()
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


@app.command()
def stop() -> None:
    """Stop the running SmolClaw daemon."""
    import subprocess
    # If managed by systemd, delegate to systemctl to avoid Restart=always loop
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "smolclaw.service"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() in ("active", "activating"):
        subprocess.run(["systemctl", "--user", "stop", "smolclaw.service"], check=True)
        typer.echo("Stopped via systemd.")
        return

    from .daemon import read_pid, stop_daemon
    pid = read_pid()  # for display; stop_daemon re-checks liveness
    if stop_daemon():
        typer.echo(f"Stopped (PID {pid})." if pid else "Stopped.")
    else:
        typer.echo("SmolClaw is not running.")
        raise typer.Exit(1)


@app.command()
def restart() -> None:
    """Restart the SmolClaw daemon."""
    import subprocess
    # If managed by systemd, delegate to systemctl to avoid duplicate instances
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "smolclaw.service"],
        capture_output=True, text=True,
    )
    if result.stdout.strip() in ("active", "activating"):
        typer.echo("Detected systemd user service — delegating to systemctl --user restart...")
        subprocess.run(["systemctl", "--user", "restart", "smolclaw.service"], check=True)
        typer.echo("Restarted via systemd.")
        return

    from .daemon import is_running, stop_daemon
    running, pid = is_running()
    if running:
        typer.echo(f"Stopping SmolClaw (PID {pid})...")
        if not stop_daemon():
            typer.echo("Failed to stop SmolClaw. Aborting restart.")
            raise typer.Exit(1)
        typer.echo(f"Stopped (PID {pid}).")
    start(foreground=False)


@app.command()
def logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream log file live."),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show."),
) -> None:
    """Show SmolClaw logs."""
    from . import workspace
    log_path = workspace.LOG_FILE
    if not log_path.exists():
        typer.echo("No log file found. Has SmolClaw been started?")
        raise typer.Exit(1)

    if not follow:
        import collections
        with open(log_path) as f:
            last = collections.deque(f, maxlen=lines)
        typer.echo("".join(last), nl=False)
        return

    # Follow mode: seek to end then stream new lines
    import time as _time
    with open(log_path) as f:
        f.seek(0, 2)  # seek to end
        try:
            while True:
                line = f.readline()
                if line:
                    typer.echo(line, nl=False)
                else:
                    _time.sleep(0.2)
        except KeyboardInterrupt:
            pass


def main() -> None:
    app()
