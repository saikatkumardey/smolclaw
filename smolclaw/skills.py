"""Scan skills/*/SKILL.md and build context block."""
from __future__ import annotations

from pathlib import Path


def load_skills(skills_dir: Path = Path("skills")) -> str:
    if not skills_dir.exists():
        return ""
    sections = [
        f"=== SKILL: {md.parent.name} ===\n{md.read_text().strip()}"
        for md in sorted(skills_dir.glob("*/SKILL.md"))
    ]
    return "\n\n".join(sections)
