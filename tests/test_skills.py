"""Skills loader tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_skill(base, name, content):
    d = base / name
    d.mkdir()
    (d / "SKILL.md").write_text(content)


def test_load_skills_returns_content(tmp_path):
    from smolclaw.skills import load_skills
    _make_skill(tmp_path, "my_skill", "Do this and that.")
    result = load_skills(tmp_path)
    assert "my_skill" in result and "Do this and that." in result


def test_load_skills_multiple(tmp_path):
    from smolclaw.skills import load_skills
    _make_skill(tmp_path, "alpha", "Alpha skill")
    _make_skill(tmp_path, "beta", "Beta skill")
    result = load_skills(tmp_path)
    assert "Alpha skill" in result and "Beta skill" in result


def test_load_skills_empty_dir(tmp_path):
    from smolclaw.skills import load_skills
    assert load_skills(tmp_path) == ""


def test_load_skills_missing_dir(tmp_path):
    from smolclaw.skills import load_skills
    assert load_skills(tmp_path / "no_such_dir") == ""


def test_load_skills_ignores_dirs_without_skill_md(tmp_path):
    from smolclaw.skills import load_skills
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "README.md").write_text("not a skill")
    _make_skill(tmp_path, "real_skill", "Real skill content")
    result = load_skills(tmp_path)
    assert "real_skill" in result and "not a skill" not in result
