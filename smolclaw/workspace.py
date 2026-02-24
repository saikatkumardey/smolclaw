"""Workspace management. All agent data lives in ~/.smolclaw/"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

HOME = Path(os.getenv("SMOLCLAW_HOME", Path.home() / ".smolclaw"))

# Paths inside the workspace
SOUL       = HOME / "SOUL.md"
IDENTITY   = HOME / "IDENTITY.md"
USER       = HOME / "USER.md"
MEMORY     = HOME / "MEMORY.md"
AGENTS     = HOME / "AGENTS.md"
CRONS      = HOME / "crons.yaml"
SKILLS_DIR = HOME / "skills"
TOOLS_DIR  = HOME / "tools"
DB         = HOME / "smolclaw.db"
HANDOVER   = HOME / "handover.md"
HEARTBEAT  = HOME / "HEARTBEAT.md"

# Default templates shipped with the package
_TEMPLATES = Path(__file__).parent.parent / "templates"


def init() -> None:
    """Create ~/.smolclaw/ and populate with default templates if missing."""
    HOME.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(exist_ok=True)
    TOOLS_DIR.mkdir(exist_ok=True)

    for name in ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md", "AGENTS.md", "HEARTBEAT.md", "crons.yaml"):
        dest = HOME / name
        if not dest.exists():
            src = _TEMPLATES / name
            if src.exists():
                shutil.copy(src, dest)


def read(path: Path, default: str = "") -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return default
