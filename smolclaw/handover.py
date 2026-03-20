"""Handover: persist agent state across restarts and updates."""
from __future__ import annotations

from datetime import datetime, timezone

from . import workspace

_MAX_HANDOVER_CHARS = 4000


def save(summary: str) -> None:
    """Write a handover summary to disk for the next session."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    trimmed = summary.strip()[:_MAX_HANDOVER_CHARS]
    workspace.HANDOVER.write_text(f"# Handover — {ts}\n\n{trimmed}\n")


def load() -> str:
    """Read the current handover file contents, or empty string if missing."""
    return workspace.read(workspace.HANDOVER)


def clear() -> None:
    """Delete the handover file if it exists."""
    workspace.HANDOVER.unlink(missing_ok=True)


def exists() -> bool:
    """Return True if a handover file is present on disk."""
    return workspace.HANDOVER.exists()
