"""Interactive setup wizard. Run: smolclaw setup"""
from __future__ import annotations

import getpass
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

console = Console()

MODELS = {
    "1": ("anthropic/claude-sonnet-4-6", "ANTHROPIC_API_KEY", "https://console.anthropic.com/settings/keys"),
    "2": ("gemini/gemini-2.0-flash", "GEMINI_API_KEY", "https://aistudio.google.com/app/apikey"),
    "3": ("openai/gpt-4o-mini", "OPENAI_API_KEY", "https://platform.openai.com/api-keys"),
}


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def _write_env(path: Path, data: dict[str, str]) -> None:
    path.write_text("\n".join(f"{k}={v}" for k, v in data.items()) + "\n")


def run() -> None:
    from . import workspace

    console.print(Panel.fit(
        "[bold]Welcome to SmolClaw[/bold]\n"
        "Your personal AI agent. Let's get you set up.\n"
        "This takes about 2 minutes.",
        border_style="blue",
    ))

    # Bootstrap ~/.smolclaw/ with default templates
    workspace.init()
    console.print(f"\n  Workspace: [bold]{workspace.HOME}[/bold]")

    env_path = workspace.HOME / ".env"
    env = _read_env(env_path)

    # ── Step 1: Telegram bot ──────────────────────────────────────────────
    console.print("\n[bold blue]Step 1 — Telegram Bot[/bold blue]")
    if "TELEGRAM_BOT_TOKEN" not in env:
        console.print("  1. Open Telegram and message [bold]@BotFather[/bold]")
        console.print("  2. Send [bold]/newbot[/bold] and follow the prompts")
        console.print("  3. Copy the token it gives you\n")
        token = getpass.getpass("  Paste your bot token: ").strip()
        env["TELEGRAM_BOT_TOKEN"] = token
    else:
        console.print(f"  [green]Already set[/green] ({env['TELEGRAM_BOT_TOKEN'][:8]}...)")

    # ── Step 2: Your Telegram ID ──────────────────────────────────────────
    console.print("\n[bold blue]Step 2 — Your Telegram ID[/bold blue]")
    if "ALLOWED_USER_IDS" not in env:
        console.print("  1. Message [bold]@userinfobot[/bold] on Telegram")
        console.print("  2. It will reply with your numeric ID\n")
        user_id = Prompt.ask("  Paste your Telegram user ID").strip()
        env["ALLOWED_USER_IDS"] = user_id
    else:
        console.print(f"  [green]Already set[/green] ({env['ALLOWED_USER_IDS']})")

    # ── Step 3: AI model ──────────────────────────────────────────────────
    console.print("\n[bold blue]Step 3 — AI Model[/bold blue]")
    current_model = env.get("LITELLM_MODEL", "")
    if not current_model:
        console.print("  [1] Claude Sonnet  (Anthropic) — recommended")
        console.print("  [2] Gemini Flash   (Google)")
        console.print("  [3] GPT-4o mini    (OpenAI)\n")
        choice = Prompt.ask("  Choose", choices=["1", "2", "3"], default="1")
        model_id, key_name, key_url = MODELS[choice]
        env["LITELLM_MODEL"] = model_id

        console.print(f"\n  Get your API key at: [link={key_url}]{key_url}[/link]\n")
        api_key = getpass.getpass(f"  Paste your {key_name}: ").strip()
        env[key_name] = api_key
    else:
        console.print(f"  [green]Already set[/green] ({current_model})")

    # ── Step 4: Optional config ───────────────────────────────────────────
    console.print("\n[bold blue]Step 4 — Optional[/bold blue]")
    if Confirm.ask("  Use a local SearXNG instance for web search?", default=False):
        url = Prompt.ask("  SearXNG URL", default="http://127.0.0.1:8888")
        env["SEARXNG_URL"] = url

    # ── Write .env ────────────────────────────────────────────────────────
    _write_env(env_path, env)

    console.print(Panel.fit(
        f"[bold green]Setup complete.[/bold green]\n\n"
        f"Workspace: [bold]{workspace.HOME}[/bold]\n\n"
        "Run [bold]smolclaw start[/bold] to launch your agent.\n"
        "Your bot will be waiting for you on Telegram.",
        border_style="green",
    ))
