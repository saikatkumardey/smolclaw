#!/usr/bin/env python3
"""
demo_setup.py  —  Animated demo of the smolclaw setup wizard.
Self-contained (only rich + stdlib). Run with: uv run python demo_setup.py
"""
from __future__ import annotations

import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console(width=88, force_terminal=True, highlight=False)

# ── Fake data ─────────────────────────────────────────────────────────────────

FAKE_TOKEN        = "7123456789:AADemoTokenXxxxxxxxxxxxxxxxxxx"
FAKE_TOKEN_MASKED = "7123456789…"
FAKE_BOT_NAME     = "@smolclaw_demo_bot"
FAKE_USER_ID      = "987654321"
FAKE_MODEL        = "claude-sonnet-4-6"
FAKE_PROVIDER     = "Anthropic"
FAKE_API_KEY      = "sk-ant-api03-DemoKeyXxxxxxxxxxxxxxxxxxx"
FAKE_API_MASKED   = "sk-ant-api…"

# ── Timing helpers ────────────────────────────────────────────────────────────

def p(t: float = 0.08) -> None:
    """Short pause."""
    time.sleep(t)

def slow(lines: list[str], delay: float = 0.09) -> None:
    """Print lines with a small delay between each."""
    for line in lines:
        console.print(line, markup=True)
        p(delay)

# ── Visual helpers (match setup.py exactly) ───────────────────────────────────

BANNER = r"""
 ____                  _  ____  _
/ ___|| _ __ ___   ___ | |/ ___|| | __ _ __      __
\___ \| '_ ` _ \ / _ \| | |    | |/ _` |\ \ /\ / /
 ___) | | | | | | (_) | | |___ | | (_| | \ V  V /
|____/|_| |_| |_|\___/|_|\____||_|\__,_|  \_/\_/
"""

def _step_header(num: int, title: str, total: int = 5) -> None:
    console.print()
    console.print(Rule(
        f"[bold blue]  Step {num}/{total} — {title}  [/bold blue]",
        style="blue",
        align="left",
    ))
    console.print()
    p(0.1)

def _success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green]  {msg}", markup=True)
    p(0.07)

def _warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow]  {msg}", markup=True)
    p(0.07)

def _info(msg: str) -> None:
    console.print(f"  [dim]→[/dim]  {msg}", markup=True)
    p(0.07)

# ── Simulated typing ──────────────────────────────────────────────────────────

def _type_line(prompt: str, value: str, hidden: bool = False, delay: float = 0.06) -> None:
    """Simulate a user typing a value at a prompt."""
    display_value = "*" * len(value) if hidden else value
    # Print prompt first
    console.print(f"  [bold]{prompt}[/bold]: ", markup=True, end="")
    sys.stdout.flush()
    p(0.3)  # pause before typing
    # "Type" each character
    for ch in display_value:
        console.print(ch, end="", markup=False)
        sys.stdout.flush()
        p(delay)
    console.print()  # newline after typing
    p(0.15)

# ── Step 1: Telegram bot token ────────────────────────────────────────────────

def step1_telegram_bot() -> None:
    _step_header(1, "Telegram Bot Token")

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
    p(0.4)

    # Simulate typing the token (hidden)
    console.print("  Paste your bot token (hidden): ", markup=True, end="")
    sys.stdout.flush()
    p(0.4)
    for _ in FAKE_TOKEN:
        console.print("*", end="", markup=False)
        sys.stdout.flush()
        p(0.025)
    console.print()
    p(0.2)

    # Spinner
    with Progress(
        SpinnerColumn(spinner_name="dots", style="blue"),
        TextColumn("[blue]Validating token with Telegram…[/blue]"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("validate", total=None)
        time.sleep(1.8)   # fake network call

    _success(f"Bot validated: [bold green]{FAKE_BOT_NAME}[/bold green]")

# ── Step 2: Telegram user ID ──────────────────────────────────────────────────

def step2_telegram_id() -> None:
    _step_header(2, "Your Telegram User ID")

    console.print(Panel(
        "[bold]How to find your Telegram user ID:[/bold]\n\n"
        "  1. Message [bold cyan]@userinfobot[/bold cyan] on Telegram\n"
        "  2. It will reply with your numeric user ID\n\n"
        "[dim]Example: 123456789[/dim]",
        border_style="blue",
        title="[blue]Instructions[/blue]",
        padding=(1, 2),
    ))
    p(0.4)

    # Simulate typing user ID
    console.print("  Your Telegram user ID: ", markup=True, end="")
    sys.stdout.flush()
    p(0.4)
    for ch in FAKE_USER_ID:
        console.print(ch, end="", markup=False)
        sys.stdout.flush()
        p(0.08)
    console.print()
    p(0.2)

    _success(f"User ID set: [bold green]{FAKE_USER_ID}[/bold green]")

# ── Step 3: AI model & provider ───────────────────────────────────────────────

MODELS = [
    ("1", "Anthropic",  "claude-sonnet-4-6",       "Recommended"),
    ("2", "Anthropic",  "claude-haiku-3-5",         "Fast & cheap"),
    ("3", "Google",     "gemini/gemini-2.0-flash",  "Free tier available"),
    ("4", "OpenAI",     "gpt-4o-mini",              "Affordable"),
    ("5", "OpenAI",     "gpt-4o",                   "Most capable"),
    ("6", "Groq",       "groq/llama-3.3-70b",       "Fast, free tier"),
    ("7", "Ollama",     "ollama/llama3.2",           "Local, private"),
    ("8", "Custom",     "(enter manually)",          ""),
]

def step3_ai_model() -> None:
    _step_header(3, "AI Model & Provider")

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

    for choice, provider, model_id, notes in MODELS:
        style = "bold" if choice == "1" else ""
        note_text = f"[green]{notes}[/green]" if notes == "Recommended" else notes
        table.add_row(choice, provider, model_id, note_text, style=style)

    console.print(table)
    console.print()
    p(0.5)

    # Simulate choosing option 1
    console.print("  Choose a model [1/2/3/4/5/6/7/8] (1): ", markup=True, end="")
    sys.stdout.flush()
    p(0.6)
    console.print("1")
    p(0.2)

    _success(f"Model set to: [bold green]{FAKE_MODEL}[/bold green]")
    p(0.2)

    # Simulate pasting API key
    console.print(
        "\n  [dim]Get your API key at:[/dim] "
        "[link=https://console.anthropic.com/settings/keys]"
        "https://console.anthropic.com/settings/keys[/link]",
        markup=True,
    )
    p(0.3)
    console.print("  Paste your ANTHROPIC_API_KEY (hidden): ", markup=True, end="")
    sys.stdout.flush()
    p(0.5)
    for _ in FAKE_API_KEY:
        console.print("*", end="", markup=False)
        sys.stdout.flush()
        p(0.022)
    console.print()
    p(0.2)

    _success("ANTHROPIC_API_KEY saved.")

# ── Step 4: MCP servers ───────────────────────────────────────────────────────

def step4_mcp_servers() -> None:
    _step_header(4, "MCP Servers (Optional)")

    console.print(Panel(
        "[bold]What are MCP servers?[/bold]\n\n"
        "MCP (Model Context Protocol) servers give your agent access to\n"
        "external tools: filesystems, databases, GitHub, and more.\n\n"
        "[dim]If you don't need external tools yet, just skip this step.[/dim]",
        border_style="blue",
        title="[blue]About MCP[/blue]",
        padding=(1, 2),
    ))
    p(0.4)

    console.print("  Add an MCP server? [y/n] (n): ", markup=True, end="")
    sys.stdout.flush()
    p(0.5)
    console.print("n")
    p(0.15)

    console.print("  [dim]Skipped MCP server configuration.[/dim]", markup=True)

# ── Step 5: Web search ────────────────────────────────────────────────────────

def step5_web_search() -> None:
    _step_header(5, "Web Search (Optional)")

    console.print(Panel(
        "[bold]Web search backend[/bold]\n\n"
        "By default SmolClaw uses [cyan]DuckDuckGo[/cyan] (no setup required).\n\n"
        "If you run a local [cyan]SearXNG[/cyan] instance you can point SmolClaw\n"
        "at it for more powerful, privacy-friendly search.",
        border_style="blue",
        title="[blue]About Web Search[/blue]",
        padding=(1, 2),
    ))
    p(0.4)

    console.print("  Use local SearXNG for web search? [y/n] (n): ", markup=True, end="")
    sys.stdout.flush()
    p(0.5)
    console.print("n")
    p(0.15)

    console.print("  [dim]Using DuckDuckGo (default).[/dim]", markup=True)

# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary() -> None:
    console.print()
    console.print(Rule(style="green"))
    console.print()
    p(0.2)

    summary = Table(
        show_header=False,
        border_style="dim green",
        box=None,
        padding=(0, 2),
    )
    summary.add_column("Key", style="dim", min_width=24)
    summary.add_column("Value", style="bold")

    rows = [
        ("TELEGRAM_BOT_TOKEN", FAKE_TOKEN_MASKED),
        ("ALLOWED_USER_IDS",   FAKE_USER_ID),
        ("LITELLM_MODEL",      FAKE_MODEL),
        ("ANTHROPIC_API_KEY",  FAKE_API_MASKED),
    ]
    for k, v in rows:
        summary.add_row(k, f"[green]{v}[/green]")
        p(0.07)

    console.print(Panel(
        summary,
        title="[bold green]✓  Setup Complete[/bold green]",
        subtitle="[dim]~/.smolclaw/.env[/dim]",
        border_style="green",
        padding=(1, 2),
    ))
    p(0.2)

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
    p(0.5)

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p(0.3)

    # Banner
    console.print(Panel(
        Text(BANNER.strip(), style="bold cyan", justify="center"),
        subtitle="[dim]Personal AI Agent Setup Wizard[/dim]",
        border_style="cyan",
        padding=(0, 4),
    ))
    p(0.3)

    slow([
        "  Welcome! This wizard will configure your SmolClaw agent.",
        "  [dim]Press Ctrl+C at any time — partial progress will be saved.[/dim]",
        "",
    ], delay=0.1)

    _info("Workspace: [bold]~/.smolclaw[/bold]")
    p(0.4)

    step1_telegram_bot()
    p(0.25)
    step2_telegram_id()
    p(0.25)
    step3_ai_model()
    p(0.25)
    step4_mcp_servers()
    p(0.25)
    step5_web_search()
    p(0.25)

    print_summary()

if __name__ == "__main__":
    main()
