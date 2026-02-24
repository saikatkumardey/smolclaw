"""Workspace tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_workspace_init_creates_directories(tmp_path, monkeypatch):
    """workspace.init() should create HOME, skills/, and tools/ dirs."""
    import smolclaw.workspace as ws

    fake_home = tmp_path / "smolclaw_home"

    monkeypatch.setattr(ws, "HOME", fake_home)
    monkeypatch.setattr(ws, "SOUL", fake_home / "SOUL.md")
    monkeypatch.setattr(ws, "USER", fake_home / "USER.md")
    monkeypatch.setattr(ws, "MEMORY", fake_home / "MEMORY.md")
    monkeypatch.setattr(ws, "CRONS", fake_home / "crons.yaml")
    monkeypatch.setattr(ws, "SKILLS_DIR", fake_home / "skills")
    monkeypatch.setattr(ws, "TOOLS_DIR", fake_home / "tools")

    ws.init()

    assert fake_home.exists()
    assert (fake_home / "skills").is_dir()
    assert (fake_home / "tools").is_dir()


def test_workspace_init_copies_templates(tmp_path, monkeypatch):
    """workspace.init() should copy template files if they exist."""
    import smolclaw.workspace as ws

    fake_home = tmp_path / "smolclaw_home"

    monkeypatch.setattr(ws, "HOME", fake_home)
    monkeypatch.setattr(ws, "SOUL", fake_home / "SOUL.md")
    monkeypatch.setattr(ws, "USER", fake_home / "USER.md")
    monkeypatch.setattr(ws, "MEMORY", fake_home / "MEMORY.md")
    monkeypatch.setattr(ws, "CRONS", fake_home / "crons.yaml")
    monkeypatch.setattr(ws, "SKILLS_DIR", fake_home / "skills")
    monkeypatch.setattr(ws, "TOOLS_DIR", fake_home / "tools")

    # Build a fake templates dir so init() has something to copy
    fake_templates = tmp_path / "templates"
    fake_templates.mkdir()
    (fake_templates / "SOUL.md").write_text("# Soul template")
    (fake_templates / "USER.md").write_text("# User template")

    monkeypatch.setattr(ws, "_TEMPLATES", fake_templates)

    ws.init()

    assert (fake_home / "SOUL.md").exists()
    assert (fake_home / "SOUL.md").read_text() == "# Soul template"
    assert (fake_home / "USER.md").exists()


def test_workspace_read_existing_file(tmp_path):
    """workspace.read() should return file contents."""
    from smolclaw.workspace import read
    f = tmp_path / "test.md"
    f.write_text("hello")
    assert read(f) == "hello"


def test_workspace_read_missing_file(tmp_path):
    """workspace.read() should return default string for missing file."""
    from smolclaw.workspace import read
    missing = tmp_path / "does_not_exist.md"
    assert read(missing) == ""
    assert read(missing, default="fallback") == "fallback"
