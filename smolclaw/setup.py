"""Interactive setup wizard. Run: smolclaw setup"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import questionary
import requests
import yaml
from dotenv import dotenv_values
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()

# ── ASCII Banner ──────────────────────────────────────────────────────────────

BANNER = r"""
 ____                  _  ____  _
/ ___|| _ __ ___   ___ | |/ ___|| | __ _ __      __
\___ \| '_ ` _ \ / _ \| | |    | |/ _` |\ \ /\ / /
 ___) | | | | | | (_) | | |___ | | (_| | \ V  V /
|____/|_| |_| |_|\___/|_|\____||_|\__,_|  \_/\_/
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(dotenv_values(path))


def _write_env(path: Path, data: dict[str, str]) -> None:
    lines: list[str] = []
    for k, v in data.items():
        escaped = v.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{k}="{escaped}"')
    path.write_text("\n".join(lines) + "\n")


def _step_header(num: int, title: str, total: int = 3) -> None:
    console.print()
    console.print(Rule(
        f"[bold blue]  Step {num}/{total} — {title}  [/bold blue]",
        style="blue",
        align="left",
    ))
    console.print()


def _success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green]  {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow]  {msg}")


def _error(msg: str) -> None:
    console.print(f"  [bold red]✗[/bold red]  {msg}")


def _info(msg: str) -> None:
    console.print(f"  [dim]→[/dim]  {msg}")


def _already(label: str, value: str) -> None:
    console.print(
        Panel(
            f"[green]Already configured:[/green]  {value}",
            title=f"[dim]{label}[/dim]",
            border_style="dim green",
            padding=(0, 2),
        )
    )


def _validate_token(token: str) -> tuple[bool, Optional[str]]:
    """Call Telegram getMe. Returns (ok, bot_username_or_error)."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            username = data["result"].get("username", "?")
            return True, f"@{username}"
        return False, data.get("description", "Invalid token")
    except requests.exceptions.ConnectionError:
        return False, "network_error"
    except Exception as e:  # noqa: BLE001
        return False, f"error: {e}"


# ── Step Implementations ──────────────────────────────────────────────────────

def step_telegram_bot(env: dict[str, str]) -> dict[str, str]:
    _step_header(1, "Telegram Bot Token")

    existing = env.get("TELEGRAM_BOT_TOKEN", "")
    if existing:
        # Try to get the bot name for existing token
        bot_display = f"{existing[:10]}..."
        _already("TELEGRAM_BOT_TOKEN", bot_display)
        if not Confirm.ask("  Change this token?", default=False):
            return env

    console.print(Panel(
        "[bold]To create a Telegram bot:[/bold]\n\n"
        "  1. Open Telegram and message [bold cyan]@BotFather[/bold cyan]\n"
        "  2. Send [bold]/newbot[/bold] and follow the prompts\n"
        "  3. Copy the [bold]API token[/bold] it gives you\n\n"
        "[dim]The token looks like: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz[/dim]",
        border_style="blue",
        title="[blue]Instructions[/blue]",
        padding=(1, 2),
    ))

    for attempt in range(1, 4):
        if attempt > 1:
            _warn(f"Attempt {attempt}/3")
        try:
            token = getpass.getpass("  Paste your bot token (hidden): ").strip()
        except KeyboardInterrupt:
            console.print()
            raise

        if not token:
            _error("Token cannot be empty.")
            continue

        # Validate with spinner
        ok = False
        result_label = ""
        with Progress(
            SpinnerColumn(spinner_name="dots", style="blue"),
            TextColumn("[blue]Validating token with Telegram…[/blue]"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("validate", total=None)
            ok, result_label = _validate_token(token)

        if result_label == "network_error":
            _warn("Could not reach Telegram (network error). Skipping validation.")
            if Confirm.ask("  Save token anyway?", default=True):
                env["TELEGRAM_BOT_TOKEN"] = token
                _success("Token saved (unvalidated)")
                return env
            continue

        if ok:
            _success(f"Bot validated: [bold green]{result_label}[/bold green]")
            env["TELEGRAM_BOT_TOKEN"] = token
            return env
        else:
            _error(f"Invalid token: {result_label}")
            if attempt == 3:
                _warn("Max attempts reached. Skipping bot token setup.")
                return env

    return env


def step_telegram_id(env: dict[str, str]) -> dict[str, str]:
    _step_header(2, "Your Telegram User ID")

    existing = env.get("ALLOWED_USER_IDS", "")
    if existing:
        _already("ALLOWED_USER_IDS", existing)
        if not Confirm.ask("  Change your Telegram ID?", default=False):
            return env

    console.print(Panel(
        "[bold]How to find your Telegram user ID:[/bold]\n\n"
        "  1. Message [bold cyan]@userinfobot[/bold cyan] on Telegram\n"
        "  2. It will reply with your numeric user ID\n\n"
        "[dim]Example: 123456789[/dim]",
        border_style="blue",
        title="[blue]Instructions[/blue]",
        padding=(1, 2),
    ))

    while True:
        try:
            user_id = Prompt.ask("  Your Telegram user ID").strip()
        except KeyboardInterrupt:
            console.print()
            raise

        if not user_id:
            _error("User ID cannot be empty.")
            continue
        if not user_id.lstrip("-").isdigit():
            _error("User ID must be numeric (digits only).")
            continue

        env["ALLOWED_USER_IDS"] = user_id
        _success(f"User ID set: [bold green]{user_id}[/bold green]")
        return env


def step_claude_auth(env: dict[str, str]) -> dict[str, str]:
    _step_header(3, "Claude Authentication")

    existing = env.get("ANTHROPIC_API_KEY", "")
    if existing:
        _already("ANTHROPIC_API_KEY", existing[:12] + "…")
        if not Confirm.ask("  Change this API key?", default=False):
            return env

    console.print(Panel(
        "[bold]How to authenticate with Claude:[/bold]\n\n"
        "  [bold cyan]1. API key[/bold cyan]  — Paste an Anthropic API key\n"
        "              [dim](get one at console.anthropic.com/settings/keys)[/dim]\n\n"
        "  [bold cyan]2. Login[/bold cyan]    — Sign in with your Claude.ai account\n"
        "              [dim](Claude Pro / Max / Team subscription)[/dim]",
        border_style="blue",
        title="[blue]Instructions[/blue]",
        padding=(1, 2),
    ))

    try:
        choice = questionary.select(
            "How would you like to authenticate?",
            choices=[
                questionary.Choice("Paste an API key", value="key"),
                questionary.Choice("Login with Claude account (opens browser)", value="login"),
            ],
            style=questionary.Style([
                ("selected", "fg:cyan bold"),
                ("pointer", "fg:cyan bold"),
                ("question", "fg:blue bold"),
            ]),
        ).ask()
    except KeyboardInterrupt:
        console.print()
        raise

    if choice is None:
        _warn("Skipping Claude authentication. Run 'smolclaw setup-token' later.")
        return env

    if choice == "key":
        try:
            api_key = getpass.getpass("  Paste your ANTHROPIC_API_KEY (hidden): ").strip()
        except KeyboardInterrupt:
            console.print()
            raise

        if not api_key:
            _warn("No key entered. Run 'smolclaw setup-token' to authenticate later.")
            return env

        env["ANTHROPIC_API_KEY"] = api_key
        _success("API key saved.")

    else:  # login
        console.print()
        _info("Opening browser for Claude.ai login…")
        try:
            subprocess.run(["claude", "auth", "login"], check=True)
            _success("Logged in with Claude account.")
        except FileNotFoundError:
            _error("[bold]claude[/bold] CLI not found. The SDK should have bundled it.")
            _warn("Run 'smolclaw setup-token' after installation to retry.")
        except subprocess.CalledProcessError:
            _error("Login failed or was cancelled.")
            _warn("Run 'smolclaw setup-token' to retry.")

    return env


# ── Summary Panel ─────────────────────────────────────────────────────────────

def _print_summary(env: dict[str, str], workspace_home: Path) -> None:
    console.print()
    console.print(Rule(style="green"))
    console.print()

    summary = Table(
        show_header=False,
        border_style="dim green",
        box=None,
        padding=(0, 2),
    )
    summary.add_column("Key", style="dim", min_width=24)
    summary.add_column("Value", style="bold")

    def _mask(v: str, n: int = 10) -> str:
        if len(v) <= n:
            return "***"
        return v[:n] + "…"

    secret_keys = {
        "TELEGRAM_BOT_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "LITELLM_API_KEY",
    }

    display_order = [
        "TELEGRAM_BOT_TOKEN",
        "ALLOWED_USER_IDS",
        "LITELLM_MODEL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "LITELLM_API_KEY",
        "OLLAMA_BASE_URL",
    ]

    shown = set()
    for key in display_order:
        if key in env:
            val = _mask(env[key]) if key in secret_keys else env[key]
            summary.add_row(key, f"[green]{val}[/green]")
            shown.add(key)

    # Any extra keys not in our list
    for key, val in env.items():
        if key not in shown:
            display_val = _mask(val) if key in secret_keys else val
            summary.add_row(key, f"[green]{display_val}[/green]")

    console.print(Panel(
        summary,
        title="[bold green]✓  Setup Complete[/bold green]",
        subtitle=f"[dim]{workspace_home / '.env'}[/dim]",
        border_style="green",
        padding=(1, 2),
    ))
    console.print()
    console.print(
        Panel(
            "Start your agent:\n\n"
            "  [bold cyan]smolclaw start[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()


# ── Systemd Service Generation ────────────────────────────────────────────────

_SERVICE_TEMPLATE = """\
[Unit]
Description=SmolClaw Telegram AI Agent
After=network.target

[Service]
Type=simple
User={current_user}
WorkingDirectory={workspace_home}
EnvironmentFile={workspace_home}/.env
ExecStart={smolclaw_binary} start --foreground
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

_SERVICE_PATH = Path("/etc/systemd/system/smolclaw.service")
_SYSTEMD_RUNTIME = Path("/run/systemd/system")


def _install_systemd_service(workspace_home: Path) -> None:
    """Generate and install the systemd service file if systemd is available."""
    if not _SYSTEMD_RUNTIME.exists():
        return  # Not a systemd system — skip silently

    current_user = getpass.getuser()
    smolclaw_binary = shutil.which("smolclaw") or sys.executable

    service_content = _SERVICE_TEMPLATE.format(
        current_user=current_user,
        workspace_home=workspace_home,
        smolclaw_binary=smolclaw_binary,
    )

    try:
        _SERVICE_PATH.write_text(service_content)
        subprocess.run(
            ["systemctl", "daemon-reload"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["systemctl", "enable", "smolclaw"],
            check=True,
            capture_output=True,
        )
        _success("Systemd service installed. Run 'systemctl start smolclaw' to start.")
    except (PermissionError, OSError, subprocess.CalledProcessError) as exc:
        _warn(f"Could not install systemd service automatically ({exc}).")
        _warn("To install manually, save the following to /etc/systemd/system/smolclaw.service")
        _warn("then run: systemctl daemon-reload && systemctl enable smolclaw")
        console.print()
        console.print(Panel(
            service_content,
            title="[dim]smolclaw.service[/dim]",
            border_style="dim yellow",
            padding=(0, 2),
        ))


# ── Watchdog Installation ─────────────────────────────────────────────────────

_WATCHDOG_DEST = Path("/usr/local/bin/smolclaw-watchdog")
_WATCHDOG_CRON = "*/10 * * * * /usr/local/bin/smolclaw-watchdog >> ~/.smolclaw/watchdog.log 2>&1"


def _install_watchdog(workspace_home: Path) -> None:  # noqa: ARG001
    """Copy watchdog.sh to /usr/local/bin and add a system cron entry."""
    # Locate the bundled watchdog script (next to this file)
    watchdog_src = Path(__file__).parent / "watchdog.sh"
    if not watchdog_src.exists():
        _warn("watchdog.sh not found in package — skipping watchdog installation.")
        return

    # ── Copy to /usr/local/bin ────────────────────────────────────────────
    try:
        shutil.copy2(watchdog_src, _WATCHDOG_DEST)
        _WATCHDOG_DEST.chmod(0o755)
    except (PermissionError, OSError) as exc:
        _warn(f"Could not install watchdog script ({exc}). Try: sudo cp {watchdog_src} {_WATCHDOG_DEST} && sudo chmod +x {_WATCHDOG_DEST}")
        return

    # ── Install cron entry (idempotent) ───────────────────────────────────
    try:
        existing = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
        )
        # Tolerate "no crontab for user" (exit code 1, specific stderr)
        current_crontab = existing.stdout if existing.returncode == 0 else ""

        # Strip any existing smolclaw-watchdog lines, then append fresh entry
        filtered = "\n".join(
            line for line in current_crontab.splitlines()
            if "smolclaw-watchdog" not in line
        )
        new_crontab = (filtered.rstrip("\n") + "\n" + _WATCHDOG_CRON + "\n").lstrip("\n")

        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, "crontab -", proc.stderr)

        _success("Watchdog installed (system cron, every 10 min)")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        _warn(f"Could not install cron entry ({exc}).")
        _warn(f"Add manually: {_WATCHDOG_CRON}")


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run() -> None:
    from . import workspace  # noqa: PLC0415

    # ── Banner ────────────────────────────────────────────────────────────
    console.print(Panel(
        Text(BANNER.strip(), style="bold cyan", justify="center"),
        subtitle="[dim]Personal AI Agent Setup Wizard[/dim]",
        border_style="cyan",
        padding=(0, 4),
    ))

    console.print()
    console.print(
        "  Welcome! This wizard will configure your SmolClaw agent.\n"
        "  [dim]Press Ctrl+C at any time — partial progress will be saved.[/dim]"
    )
    console.print()

    # ── Bootstrap workspace ───────────────────────────────────────────────
    workspace.init()
    _info(f"Workspace: [bold]{workspace.HOME}[/bold]")

    env_path = workspace.HOME / ".env"
    env = _read_env(env_path)

    # ── Run steps ─────────────────────────────────────────────────────────
    steps = [
        step_telegram_bot,
        step_telegram_id,
        step_claude_auth,
    ]

    completed = 0
    try:
        for step_fn in steps:
            env = step_fn(env)
            completed += 1
            # Write after each step so partial progress is always saved
            _write_env(env_path, env)
    except KeyboardInterrupt:
        console.print()
        _warn("Setup interrupted. Saving partial configuration…")
        _write_env(env_path, env)
        console.print(f"  [dim]Saved to {env_path}. Run [bold]smolclaw setup[/bold] to continue.[/dim]")
        console.print()
        sys.exit(0)

    # ── Patch crons.yaml: fill in deliver_to for jobs with empty deliver_to ──
    user_id = env.get("ALLOWED_USER_IDS", "").split(",")[0].strip()
    if user_id and workspace.CRONS.exists():
        try:
            crons_data = yaml.safe_load(workspace.CRONS.read_text()) or {}
            patched = False
            for job in crons_data.get("jobs", []):
                if not job.get("deliver_to"):
                    job["deliver_to"] = user_id
                    patched = True
            if patched:
                workspace.CRONS.write_text(yaml.dump(crons_data, default_flow_style=False, allow_unicode=True))
        except Exception:
            pass  # Non-fatal — user can edit crons.yaml manually

    # ── Final summary ─────────────────────────────────────────────────────
    _print_summary(env, workspace.HOME)

    # ── Systemd service installation ──────────────────────────────────────
    _install_systemd_service(workspace.HOME)

    # ── Watchdog installation ──────────────────────────────────────────────
    _install_watchdog(workspace.HOME)
