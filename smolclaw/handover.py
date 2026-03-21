from __future__ import annotations

from datetime import datetime, timezone

from . import workspace

_MAX_HANDOVER_CHARS = 4000


def save(summary: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    trimmed = summary.strip()[:_MAX_HANDOVER_CHARS]
    workspace.HANDOVER.write_text(f"# Handover — {ts}\n\n{trimmed}\n")


def load() -> str:
    return workspace.read(workspace.HANDOVER)


def clear() -> None:
    workspace.HANDOVER.unlink(missing_ok=True)


def exists() -> bool:
    return workspace.HANDOVER.exists()
