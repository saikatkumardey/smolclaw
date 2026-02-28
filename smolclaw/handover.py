"""Handover: persist agent state across restarts and updates."""
from __future__ import annotations

from datetime import datetime, timezone

from . import workspace


def save(summary: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    workspace.HANDOVER.write_text(f"# Handover — {ts}\n\n{summary.strip()}\n")


def load() -> str:
    return workspace.read(workspace.HANDOVER)


def clear() -> None:
    workspace.HANDOVER.unlink(missing_ok=True)
