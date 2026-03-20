"""Workspace management. All agent data lives in ~/.smolclaw/"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

HOME = Path(os.getenv("SMOLCLAW_HOME", Path.home() / ".smolclaw"))

# Paths inside the workspace
SOUL       = HOME / "SOUL.md"
AGENT      = HOME / "AGENT.md"
USER       = HOME / "USER.md"
MEMORY     = HOME / "MEMORY.md"
CRONS      = HOME / "crons.yaml"
SKILLS_DIR   = HOME / "skills"
TOOLS_DIR    = HOME / "tools"
TOOLS_STAGING = HOME / "tools" / ".staging"
UPLOADS_DIR  = HOME / "uploads"
HANDOVER       = HOME / "handover.md"
SUBCONSCIOUS   = HOME / "subconscious.yaml"
CONFIG        = HOME / "smolclaw.json"
SESSION_STATE = HOME / "session_state.json"
PID_FILE      = HOME / ".pid"
LOG_FILE      = HOME / "smolclaw.log"

# Default templates shipped with the package
_TEMPLATES = Path(__file__).parent.parent / "templates"


def init() -> None:
    """Create ~/.smolclaw/ and populate with default templates if missing."""
    HOME.mkdir(parents=True, exist_ok=True)
    HOME.chmod(0o700)
    SKILLS_DIR.mkdir(exist_ok=True)
    TOOLS_DIR.mkdir(exist_ok=True)
    TOOLS_STAGING.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.chmod(0o700)
    sessions_dir = HOME / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    sessions_dir.chmod(0o700)

    for name in ("SOUL.md", "AGENT.md", "USER.md", "MEMORY.md", "HEARTBEAT.md", "crons.yaml"):
        dest = HOME / name
        if not dest.exists():
            src = _TEMPLATES / name
            if src.exists():
                shutil.copy(src, dest)

    # Initialize subconscious.yaml with empty threads if missing
    if not SUBCONSCIOUS.exists():
        SUBCONSCIOUS.write_text("threads: []\n")

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


def read_template(name: str) -> str:
    """Read a template file by name from the shipped templates directory."""
    path = _TEMPLATES / name
    return path.read_text()


def read(path: Path, default: str = "") -> str:
    """Read a text file, returning default if the file does not exist."""
    try:
        return path.read_text()
    except FileNotFoundError:
        return default


def read_json(path: Path) -> dict:
    """Read a JSON file. Returns empty dict if missing or invalid."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_json(path: Path, data: dict) -> None:
    """Atomic write: write to unique temp file then rename."""
    fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
