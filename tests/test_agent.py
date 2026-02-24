"""Agent tests — mock LLM to avoid network calls."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_system_prompt_contains_soul(tmp_path, monkeypatch):
    """_system_prompt() should always reference SOUL.md in its output."""
    import smolclaw.workspace as ws

    # Redirect workspace to tmp so no real files needed
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "SOUL", tmp_path / "SOUL.md")
    monkeypatch.setattr(ws, "USER", tmp_path / "USER.md")
    monkeypatch.setattr(ws, "MEMORY", tmp_path / "MEMORY.md")
    monkeypatch.setattr(ws, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")

    # Write a minimal SOUL.md so workspace.read finds something
    (tmp_path / "SOUL.md").write_text("## Identity\nNot set yet")
    (tmp_path / "USER.md").write_text("Not set yet")

    from smolclaw.agent import _system_prompt
    prompt = _system_prompt()
    assert isinstance(prompt, str)
    assert "SOUL" in prompt


def test_create_agent_returns_tool_calling_agent(tmp_path, monkeypatch):
    """_create_agent() should return a ToolCallingAgent (with mocked model and agent)."""
    import smolclaw.workspace as ws

    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "SOUL", tmp_path / "SOUL.md")
    monkeypatch.setattr(ws, "USER", tmp_path / "USER.md")
    monkeypatch.setattr(ws, "MEMORY", tmp_path / "MEMORY.md")
    monkeypatch.setattr(ws, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")

    (tmp_path / "SOUL.md").write_text("## Identity\nNot set yet")
    (tmp_path / "USER.md").write_text("Not set yet")

    mock_model = MagicMock()
    mock_agent_instance = MagicMock()
    mock_agent_instance.tools = {}

    # Mock both LiteLLMModel and ToolCallingAgent so no real network/model needed
    with patch("smolclaw.agent.LiteLLMModel", return_value=mock_model), \
         patch("smolclaw.agent.ToolCallingAgent", return_value=mock_agent_instance) as mock_tca:
        from smolclaw.agent import _create_agent

        result = _create_agent([])
        # Verify the factory was called and returned our mock
        assert mock_tca.called
        assert result is mock_agent_instance


def test_run_returns_string(tmp_path, monkeypatch):
    """run() should return a string (mock the entire agent.run)."""
    import smolclaw.workspace as ws
    import smolclaw.agent as ag

    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "SOUL", tmp_path / "SOUL.md")
    monkeypatch.setattr(ws, "USER", tmp_path / "USER.md")
    monkeypatch.setattr(ws, "MEMORY", tmp_path / "MEMORY.md")
    monkeypatch.setattr(ws, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")

    (tmp_path / "SOUL.md").write_text("## Identity\nNot set yet")
    (tmp_path / "USER.md").write_text("Not set yet")

    # Pre-populate _agents with a mock agent so we skip _create_agent
    mock_agent = MagicMock()
    mock_agent.run.return_value = "Hello, world!"
    mock_agent.tools = {}
    ag._agents["test-chat"] = mock_agent

    try:
        result = ag.run(chat_id="test-chat", user_message="hi")
        assert isinstance(result, str)
        assert "Hello" in result
    finally:
        ag._agents.pop("test-chat", None)
