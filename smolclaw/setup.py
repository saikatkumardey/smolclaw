"""Interactive setup wizard. Run: smolclaw setup"""
from __future__ import annotations

import getpass
import os
import sys
import time
from pathlib import Path
from typing import Optional

import questionary
import requests
import yaml
from dotenv import dotenv_values
from rich.columns import Columns
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

# ── Model Definitions ─────────────────────────────────────────────────────────

MODELS = [
    # (choice, provider, model_id, env_key, key_url, notes)
    ("1", "Anthropic",  "claude-sonnet-4-6",       "ANTHROPIC_API_KEY", "https://console.anthropic.com/settings/keys",  "Recommended"),
    ("2", "Anthropic",  "claude-haiku-3-5",         "ANTHROPIC_API_KEY", "https://console.anthropic.com/settings/keys",  "Fast & cheap"),
    ("3", "OpenAI",     "gpt-4o-mini",              "OPENAI_API_KEY",    "https://platform.openai.com/api-keys",          "Affordable"),
    ("4", "OpenAI",     "gpt-4o",                   "OPENAI_API_KEY",    "https://platform.openai.com/api-keys",          "Most capable"),
    ("5", "Groq",       "groq/llama-3.3-70b",       "GROQ_API_KEY",      "https://console.groq.com/keys",                 "Fast, free tier"),
    ("6", "Ollama",     "ollama/llama3.2",           None,                None,                                            "Local, private"),
    ("7", "Custom",     "(enter manually)",          None,                None,                                            ""),
]

MODEL_BY_CHOICE = {m[0]: m for m in MODELS}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(dotenv_values(path))


def _write_env(path: Path, data: dict[str, str]) -> None:
    lines: list[str] = []
    for k, v in data.items():
        lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n")


def _step_header(num: int, title: str, total: int = 4) -> None:
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


def step_ai_model(env: dict[str, str]) -> dict[str, str]:
    _step_header(3, "AI Model & Provider")

    existing_model = env.get("LITELLM_MODEL", "")
    if existing_model:
        _already("LITELLM_MODEL", existing_model)
        if not Confirm.ask("  Change AI model?", default=False):
            return env

    # Build model table
    table = Table(
        show_header=True,
        header_style="bold blue",
        border_style="dim",
        padding=(0, 1),
        title="[blue]Available Models[/blue]",
    )
    table.add_column("#", style="bold", width=3, justify="right")
    table.add_column("Provider", style="cyan", min_width=10)
    table.add_column("Model", min_width=28)
    table.add_column("Notes", style="dim", min_width=20)

    for choice, provider, model_id, _key, _url, notes in MODELS:
        style = "bold" if choice == "1" else ""
        note_text = f"[green]{notes}[/green]" if notes == "Recommended" else notes
        table.add_row(choice, provider, model_id, note_text, style=style)

    console.print(table)
    console.print()

    q_choices = [
        questionary.Choice(
            title=f"{provider:<10}  {model_id:<30}  {notes}",
            value=num,
        )
        for num, provider, model_id, _key, _url, notes in MODELS
    ]
    try:
        choice = questionary.select(
            "Choose a model (↑↓ to navigate, Enter to confirm):",
            choices=q_choices,
            default=q_choices[0],
            style=questionary.Style([
                ("selected", "fg:cyan bold"),
                ("pointer",  "fg:cyan bold"),
                ("question", "fg:blue bold"),
            ]),
        ).ask()
    except KeyboardInterrupt:
        console.print()
        raise
    if choice is None:
        raise KeyboardInterrupt

    _, provider, model_id, key_env, key_url, _ = MODEL_BY_CHOICE[choice]

    # Handle custom model
    if choice == "7":
        try:
            model_id = Prompt.ask("  Enter model ID (LiteLLM format, e.g. provider/model-name)").strip()
        except KeyboardInterrupt:
            console.print()
            raise
        if not model_id:
            _warn("No model ID entered, keeping existing.")
            return env
        key_env = "LITELLM_API_KEY"
        key_url = None

    env["LITELLM_MODEL"] = model_id
    _success(f"Model set to: [bold green]{model_id}[/bold green]")

    # Ollama — no API key, just base URL
    if choice == "6":
        try:
            base_url = Prompt.ask(
                "  Ollama base URL",
                default="http://localhost:11434",
            ).strip()
        except KeyboardInterrupt:
            console.print()
            raise
        env["OLLAMA_BASE_URL"] = base_url
        _success(f"Ollama URL: [bold green]{base_url}[/bold green]")
        return env

    # All other providers — API key
    if key_url:
        console.print(f"\n  [dim]Get your API key at:[/dim] [link={key_url}]{key_url}[/link]")

    existing_key = env.get(key_env, "") if key_env else ""
    if existing_key:
        masked = existing_key[:8] + "..." if len(existing_key) > 8 else "***"
        _already(key_env, masked)
        if not Confirm.ask(f"  Update {key_env}?", default=False):
            return env

    try:
        api_key = getpass.getpass(f"  Paste your {key_env} (hidden): ").strip()
    except KeyboardInterrupt:
        console.print()
        raise

    if api_key and key_env:
        env[key_env] = api_key
        _success(f"{key_env} saved.")
    elif not api_key:
        _warn("No API key entered. You can set it later in ~/.smolclaw/.env")

    return env
def step_web_search(env: dict[str, str]) -> dict[str, str]:
    _step_header(4, "Web Search (Optional)")

    console.print(Panel(
        "[bold]Web search backend[/bold]\n\n"
        "By default SmolClaw uses [cyan]DuckDuckGo[/cyan] (no setup required).\n\n"
        "If you run a local [cyan]SearXNG[/cyan] instance you can point SmolClaw\n"
        "at it for more powerful, privacy-friendly search.",
        border_style="blue",
        title="[blue]About Web Search[/blue]",
        padding=(1, 2),
    ))

    existing = env.get("SEARXNG_URL", "")
    if existing:
        _already("SEARXNG_URL", existing)
        try:
            change = Confirm.ask("  Change SearXNG URL?", default=False)
        except KeyboardInterrupt:
            console.print()
            raise
        if not change:
            return env

    try:
        use_searx = Confirm.ask("  Use local SearXNG for web search?", default=False)
    except KeyboardInterrupt:
        console.print()
        raise

    if use_searx:
        try:
            url = Prompt.ask("  SearXNG URL", default="http://127.0.0.1:8888").strip()
        except KeyboardInterrupt:
            console.print()
            raise
        env["SEARXNG_URL"] = url
        _success(f"SearXNG URL set: [bold green]{url}[/bold green]")
    else:
        console.print("  [dim]Using DuckDuckGo (default).[/dim]")
        env.pop("SEARXNG_URL", None)

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
        "SEARXNG_URL",
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
            "[bold]Run your agent:[/bold]\n\n"
            "  [bold cyan]smolclaw start[/bold cyan]\n\n"
            "[dim]Your Telegram bot will be ready to receive messages.[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()


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
        step_ai_model,
        step_web_search,
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
