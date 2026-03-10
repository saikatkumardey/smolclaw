"""Scan skills/*/SKILL.md and build context block."""
from __future__ import annotations

from pathlib import Path

# Cache: (skills_dir_mtime, {file_path: mtime}, combined_string)
_cache: tuple[float, dict[str, float], str] | None = None


def load_skills(skills_dir: Path = Path("skills")) -> str:
    global _cache
    if not skills_dir.exists():
        return ""

    try:
        dir_mtime = skills_dir.stat().st_mtime
    except OSError:
        return ""

    # Check if directory mtime changed (new/deleted skill dirs)
    if _cache is not None and _cache[0] == dir_mtime:
        # Check individual file mtimes for edits within existing dirs
        cached_file_mtimes = _cache[1]
        current_files = {str(md): md.stat().st_mtime for md in skills_dir.glob("*/SKILL.md") if md.exists()}
        if current_files == cached_file_mtimes:
            return _cache[2]

    # Rebuild
    skill_files = sorted(skills_dir.glob("*/SKILL.md"))
    file_mtimes: dict[str, float] = {}
    sections: list[str] = []
    for md in skill_files:
        try:
            file_mtimes[str(md)] = md.stat().st_mtime
            sections.append(f"=== SKILL: {md.parent.name} ===\n{md.read_text().strip()}")
        except OSError:
            continue

    result = "\n\n".join(sections)
    _cache = (dir_mtime, file_mtimes, result)
    return result
