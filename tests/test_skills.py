"""Skills loader tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_load_skills_returns_content(tmp_path):
    """load_skills() should return SKILL.md content from each skill subdir."""
    from smolclaw.skills import load_skills

    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("## My Skill\nDo this and that.")

    result = load_skills(tmp_path)
    assert "my_skill" in result
    assert "Do this and that." in result


def test_load_skills_multiple(tmp_path):
    """Multiple skill directories should all appear in output."""
    from smolclaw.skills import load_skills

    for name, content in [("alpha", "Alpha skill"), ("beta", "Beta skill")]:
        d = tmp_path / name
        d.mkdir()
        (d / "SKILL.md").write_text(content)

    result = load_skills(tmp_path)
    assert "alpha" in result
    assert "Alpha skill" in result
    assert "beta" in result
    assert "Beta skill" in result


def test_load_skills_empty_dir(tmp_path):
    """No skills = empty string returned."""
    from smolclaw.skills import load_skills

    result = load_skills(tmp_path)
    assert result == ""


def test_load_skills_missing_dir(tmp_path):
    """Missing skills directory returns empty string."""
    from smolclaw.skills import load_skills

    result = load_skills(tmp_path / "no_such_dir")
    assert result == ""


def test_load_skills_ignores_dirs_without_skill_md(tmp_path):
    """Dirs without SKILL.md are silently ignored."""
    from smolclaw.skills import load_skills

    # A dir with a different file
    other = tmp_path / "other"
    other.mkdir()
    (other / "README.md").write_text("not a skill")

    # A proper skill dir
    good = tmp_path / "real_skill"
    good.mkdir()
    (good / "SKILL.md").write_text("Real skill content")

    result = load_skills(tmp_path)
    assert "real_skill" in result
    assert "not a skill" not in result
