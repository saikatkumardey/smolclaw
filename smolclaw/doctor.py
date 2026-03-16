"""Workspace health diagnostics. Run: smolclaw doctor"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import requests
import yaml
from dotenv import dotenv_values
from rich.console import Console

from . import workspace

# Duplicate to avoid importing agent.py (pulls in claude_agent_sdk)
_KNOWN_MODELS = {
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
}

_REQUIRED_SUBDIRS = ("skills", "tools", "uploads", "sessions")
_CORE_FILES = ("SOUL.md", "AGENT.md", "USER.md", "MEMORY.md", "HEARTBEAT.md", "crons.yaml")
_REQUIRED_ENV_VARS = ("TELEGRAM_BOT_TOKEN", "ALLOWED_USER_IDS")

_SESSION_LOG_WARN_BYTES = 100 * 1024 * 1024  # 100 MB
_UPLOADS_WARN_BYTES = 500 * 1024 * 1024  # 500 MB
_SESSION_STATE_WARN_ENTRIES = 50

console = Console()


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class CheckResult:
    status: Status
    message: str
    suggestion: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def _dir_size(path: Path) -> tuple[int, int]:
    """Return (total_bytes, file_count) for a directory."""
    total = 0
    count = 0
    if not path.exists():
        return 0, 0
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
            count += 1
    return total, count


# ---------------------------------------------------------------------------
# Workspace checks
# ---------------------------------------------------------------------------


def _check_workspace() -> list[CheckResult]:
    results: list[CheckResult] = []
    home = workspace.HOME

    # Home dir
    if not home.is_dir():
        results.append(CheckResult(
            Status.FAIL,
            f"Home directory missing ({home})",
            "Run: smolclaw setup",
        ))
        return results  # skip remaining checks

    results.append(CheckResult(Status.OK, f"Home directory exists ({home})"))

    # Required subdirs
    for name in _REQUIRED_SUBDIRS:
        d = home / name
        if d.is_dir():
            results.append(CheckResult(Status.OK, f"{name}/ exists"))
        else:
            results.append(CheckResult(
                Status.FAIL,
                f"{name}/ missing",
                "Run: smolclaw setup",
            ))

    # Core files exist + non-empty
    for name in _CORE_FILES:
        f = home / name
        if not f.exists():
            results.append(CheckResult(
                Status.FAIL,
                f"{name} missing",
                "Run: smolclaw setup",
            ))
        elif f.stat().st_size == 0:
            results.append(CheckResult(
                Status.WARN,
                f"{name} is empty (0 bytes) — may be corrupted",
                f"Delete and run: smolclaw setup",
            ))
        else:
            results.append(CheckResult(Status.OK, f"{name} present"))

    # crons.yaml valid YAML
    crons = home / "crons.yaml"
    if crons.exists() and crons.stat().st_size > 0:
        try:
            yaml.safe_load(crons.read_text())
            results.append(CheckResult(Status.OK, "crons.yaml is valid YAML"))
        except yaml.YAMLError as e:
            results.append(CheckResult(
                Status.FAIL,
                f"crons.yaml has invalid YAML: {e}",
                "Fix the syntax manually",
            ))

    # .env exists + required vars
    env_path = home / ".env"
    if not env_path.exists():
        results.append(CheckResult(
            Status.FAIL,
            ".env file missing",
            "Run: smolclaw setup",
        ))
    else:
        results.append(CheckResult(Status.OK, ".env file exists"))
        env = dotenv_values(env_path)
        for var in _REQUIRED_ENV_VARS:
            val = env.get(var, "")
            if val and val.strip():
                results.append(CheckResult(Status.OK, f".env has {var}"))
            else:
                results.append(CheckResult(
                    Status.FAIL,
                    f".env missing or empty: {var}",
                    "Run: smolclaw setup",
                ))

    # smolclaw.json (optional)
    config_path = workspace.CONFIG
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
            max_turns = data.get("max_turns")
            if isinstance(max_turns, int) and max_turns < 2:
                results.append(CheckResult(
                    Status.WARN,
                    f"smolclaw.json: max_turns={max_turns} — agent may be crippled",
                    "Increase max_turns or delete smolclaw.json to reset defaults",
                ))
            else:
                results.append(CheckResult(Status.OK, "smolclaw.json is valid"))
        except (json.JSONDecodeError, ValueError) as e:
            results.append(CheckResult(
                Status.FAIL,
                f"smolclaw.json has invalid JSON: {e}",
                "Delete to reset to defaults",
            ))

    # session_state.json (optional)
    ss_path = workspace.SESSION_STATE
    if ss_path.exists():
        try:
            json.loads(ss_path.read_text())
            results.append(CheckResult(Status.OK, "session_state.json is valid"))
        except (json.JSONDecodeError, ValueError) as e:
            results.append(CheckResult(
                Status.FAIL,
                f"session_state.json has invalid JSON: {e}",
                "Delete to reset",
            ))

    return results


# ---------------------------------------------------------------------------
# Runtime checks
# ---------------------------------------------------------------------------


def _check_runtime() -> list[CheckResult]:
    results: list[CheckResult] = []
    env_path = workspace.HOME / ".env"
    env = dotenv_values(env_path) if env_path.exists() else {}

    # Telegram token
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        results.append(CheckResult(
            Status.WARN,
            "No Telegram bot token — skipping token check",
            "Run: smolclaw setup",
        ))
    else:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getMe",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("result", {})
                username = data.get("username", "unknown")
                results.append(CheckResult(
                    Status.OK,
                    f"Telegram bot token valid (@{username})",
                ))
            else:
                results.append(CheckResult(
                    Status.FAIL,
                    f"Telegram bot token rejected (HTTP {resp.status_code})",
                    "Run: smolclaw setup",
                ))
        except Exception as e:
            results.append(CheckResult(
                Status.WARN,
                f"Could not verify Telegram token: {e}",
                "Check your network connection",
            ))

    # Claude auth
    api_key = env.get("ANTHROPIC_API_KEY", "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        results.append(CheckResult(Status.OK, "ANTHROPIC_API_KEY is set"))
    else:
        claude_cli = shutil.which("claude")
        if claude_cli:
            results.append(CheckResult(
                Status.WARN,
                "No API key — claude CLI found (login-based auth, unverifiable)",
            ))
        else:
            results.append(CheckResult(
                Status.FAIL,
                "No Claude authentication found",
                "Run: smolclaw setup-token",
            ))

    # Model
    model = env.get("SMOLCLAW_MODEL", "").strip() or os.environ.get("SMOLCLAW_MODEL", "").strip()
    # Also check smolclaw.json
    if not model:
        config_path = workspace.CONFIG
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text())
                model = data.get("model", "")
            except (json.JSONDecodeError, ValueError):
                pass
    if not model:
        model = "claude-sonnet-4-6"  # default

    if model in _KNOWN_MODELS:
        results.append(CheckResult(Status.OK, f"Model: {model}"))
    else:
        results.append(CheckResult(
            Status.WARN,
            f"Model '{model}' is not a known model ID (may still work)",
        ))

    # Custom tools
    tools_dir = workspace.TOOLS_DIR
    if tools_dir.is_dir():
        py_files = sorted(tools_dir.glob("*.py"))
        if not py_files:
            results.append(CheckResult(Status.OK, "No custom tools (tools/ empty)"))
        else:
            for path in py_files:
                try:
                    spec = importlib.util.spec_from_file_location(path.stem, path)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if not hasattr(mod, "SCHEMA"):
                        results.append(CheckResult(
                            Status.FAIL,
                            f"Tool {path.name}: missing SCHEMA",
                            "Add SCHEMA dict or remove the file",
                        ))
                        continue
                    if not hasattr(mod, "execute"):
                        results.append(CheckResult(
                            Status.FAIL,
                            f"Tool {path.name}: missing execute()",
                            "Add execute() function or remove the file",
                        ))
                        continue
                    fn_def = mod.SCHEMA.get("function", {})
                    name = fn_def.get("name", "?")
                    results.append(CheckResult(
                        Status.OK,
                        f"Tool {path.name} loads OK ({name})",
                    ))
                except Exception as e:
                    results.append(CheckResult(
                        Status.FAIL,
                        f"Tool {path.name} failed to load: {e}",
                        "Fix the error or remove the file",
                    ))

    # Playwright browser
    pw_check = shutil.which("playwright")
    if pw_check:
        pw_result = subprocess.run(
            ["playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=10,
        )
        # dry-run exits 0 if already installed
        if pw_result.returncode == 0:
            results.append(CheckResult(Status.OK, "Playwright Chromium installed"))
        else:
            results.append(CheckResult(
                Status.WARN,
                "Playwright Chromium not installed — browser tools won't work",
                "Run: playwright install chromium --with-deps",
            ))
    else:
        results.append(CheckResult(
            Status.WARN,
            "Playwright CLI not found — browser tools won't work",
            "Run: playwright install chromium --with-deps",
        ))

    # TTS dependencies (edge-tts + ffmpeg)
    edge_tts = shutil.which("edge-tts")
    ffmpeg = shutil.which("ffmpeg")
    if edge_tts and ffmpeg:
        results.append(CheckResult(Status.OK, "edge-tts and ffmpeg available (TTS ready)"))
    else:
        missing = []
        if not edge_tts:
            missing.append("edge-tts")
        if not ffmpeg:
            missing.append("ffmpeg")
        results.append(CheckResult(
            Status.WARN,
            f"Missing {', '.join(missing)} — voice messages (telegram_send_voice) won't work",
            "Install: pip install edge-tts && apt install ffmpeg",
        ))

    # Cron expressions
    crons_path = workspace.CRONS
    if crons_path.exists() and crons_path.stat().st_size > 0:
        try:
            data = yaml.safe_load(crons_path.read_text()) or {}
            jobs = data.get("jobs", [])
            for job in jobs:
                cron_expr = job.get("cron", "")
                job_id = job.get("id", "?")
                if not cron_expr:
                    continue
                try:
                    from apscheduler.triggers.cron import CronTrigger
                    CronTrigger.from_crontab(cron_expr)
                    results.append(CheckResult(
                        Status.OK,
                        f"Cron '{job_id}': expression valid ({cron_expr})",
                    ))
                except Exception as e:
                    results.append(CheckResult(
                        Status.FAIL,
                        f"Cron '{job_id}': invalid expression '{cron_expr}' — {e}",
                        "Fix the cron expression in crons.yaml",
                    ))
        except yaml.YAMLError:
            pass  # already reported in workspace checks

    return results


# ---------------------------------------------------------------------------
# State checks
# ---------------------------------------------------------------------------


def _check_state() -> list[CheckResult]:
    results: list[CheckResult] = []
    home = workspace.HOME

    # Session logs
    sessions_dir = home / "sessions"
    total_bytes, file_count = _dir_size(sessions_dir)
    size_str = _human_size(total_bytes)
    if total_bytes > _SESSION_LOG_WARN_BYTES:
        results.append(CheckResult(
            Status.WARN,
            f"Session logs: {size_str} across {file_count} files",
            f"Consider cleaning: rm {sessions_dir}/*.jsonl",
        ))
    else:
        results.append(CheckResult(
            Status.OK,
            f"Session logs: {size_str} ({file_count} files)",
        ))

    # Uploads
    uploads_dir = home / "uploads"
    total_bytes, file_count = _dir_size(uploads_dir)
    size_str = _human_size(total_bytes)
    if total_bytes > _UPLOADS_WARN_BYTES:
        results.append(CheckResult(
            Status.WARN,
            f"Uploads: {size_str} across {file_count} files",
            f"Consider cleaning: rm {uploads_dir}/*",
        ))
    else:
        results.append(CheckResult(
            Status.OK,
            f"Uploads: {size_str} ({file_count} files)",
        ))

    # Session state entries
    ss_path = workspace.SESSION_STATE
    if ss_path.exists():
        try:
            data = json.loads(ss_path.read_text())
            sessions = data.get("sessions", {})
            count = len(sessions)
            if count > _SESSION_STATE_WARN_ENTRIES:
                results.append(CheckResult(
                    Status.WARN,
                    f"Session state: {count} entries",
                    "Informational — old sessions accumulate over time",
                ))
            else:
                results.append(CheckResult(
                    Status.OK,
                    f"Session state: {count} entries",
                ))
        except (json.JSONDecodeError, ValueError):
            pass  # already reported in workspace checks

    # Stale handover
    handover = workspace.HANDOVER
    if handover.exists():
        results.append(CheckResult(
            Status.WARN,
            "handover.md exists — leftover from crash or restart",
            "Will be consumed on next bot start; delete manually if stale",
        ))
    else:
        results.append(CheckResult(Status.OK, "No stale handover.md"))

    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_ICONS = {
    Status.OK: "[bold green]✓[/bold green]",
    Status.WARN: "[bold yellow]![/bold yellow]",
    Status.FAIL: "[bold red]✗[/bold red]",
}


def _print_results(categories: dict[str, list[CheckResult]]) -> None:
    console.print()
    console.print("[bold]SmolClaw Doctor[/bold]")
    console.print("=" * 40)

    totals = {Status.OK: 0, Status.WARN: 0, Status.FAIL: 0}

    for title, checks in categories.items():
        console.print()
        console.print(f"[bold]{title}[/bold]")
        for c in checks:
            totals[c.status] += 1
            icon = _ICONS[c.status]
            console.print(f"  {icon} {c.message}")
            if c.suggestion:
                console.print(f"    [dim]→ {c.suggestion}[/dim]")

    console.print()
    ok, warn, fail = totals[Status.OK], totals[Status.WARN], totals[Status.FAIL]
    parts = []
    if ok:
        parts.append(f"[green]{ok} passed[/green]")
    if warn:
        parts.append(f"[yellow]{warn} warnings[/yellow]")
    if fail:
        parts.append(f"[red]{fail} failures[/red]")
    console.print(f"[bold]Summary:[/bold] {', '.join(parts)}")
    console.print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run() -> int:
    """Run all checks and print results. Returns exit code (0=ok, 1=failures)."""
    categories: dict[str, list[CheckResult]] = {}
    categories["Workspace"] = _check_workspace()
    categories["Runtime"] = _check_runtime()
    categories["State"] = _check_state()
    _print_results(categories)

    has_failures = any(
        c.status == Status.FAIL
        for checks in categories.values()
        for c in checks
    )
    return 1 if has_failures else 0
