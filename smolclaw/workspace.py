"""Workspace management. All agent data lives in ~/.smolclaw/"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

HOME = Path(os.getenv("SMOLCLAW_HOME", Path.home() / ".smolclaw"))

# Paths inside the workspace
SOUL       = HOME / "SOUL.md"
USER       = HOME / "USER.md"
MEMORY     = HOME / "MEMORY.md"
CRONS      = HOME / "crons.yaml"
SKILLS_DIR   = HOME / "skills"
TOOLS_DIR    = HOME / "tools"
UPLOADS_DIR  = HOME / "uploads"
HANDOVER   = HOME / "handover.md"

# Default templates shipped with the package
_TEMPLATES = Path(__file__).parent.parent / "templates"


def init() -> None:
    """Create ~/.smolclaw/ and populate with default templates if missing."""
    HOME.mkdir(parents=True, exist_ok=True)
    HOME.chmod(0o700)
    SKILLS_DIR.mkdir(exist_ok=True)
    TOOLS_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.chmod(0o700)
    sessions_dir = HOME / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    sessions_dir.chmod(0o700)

    for name in ("SOUL.md", "USER.md", "MEMORY.md", "HEARTBEAT.md", "crons.yaml"):
        dest = HOME / name
        if not dest.exists():
            src = _TEMPLATES / name
            if src.exists():
                shutil.copy(src, dest)

    # Copy skill templates
    src_skills = _TEMPLATES / "skills"
    if src_skills.is_dir():
        for skill_dir in src_skills.iterdir():
            if skill_dir.is_dir():
                dest_skill = SKILLS_DIR / skill_dir.name
                dest_skill.mkdir(exist_ok=True)
                for f in skill_dir.iterdir():
                    dest = dest_skill / f.name
                    if not dest.exists():
                        shutil.copy(f, dest)


def read(path: Path, default: str = "") -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return default
