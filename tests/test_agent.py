"""Agent tests — mock SDK client to avoid network calls."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_workspace(tmp_path, monkeypatch):
    import smolclaw.workspace as ws
    import smolclaw.agent as ag
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "SOUL", tmp_path / "SOUL.md")
    monkeypatch.setattr(ws, "USER", tmp_path / "USER.md")
    monkeypatch.setattr(ws, "MEMORY", tmp_path / "MEMORY.md")
    monkeypatch.setattr(ws, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(ag, "SESSIONS_DIR", sessions_dir)
    (tmp_path / "SOUL.md").write_text("## Identity\nNot set yet")
    (tmp_path / "USER.md").write_text("Not set yet")


def test_system_prompt_contains_soul(tmp_path, monkeypatch):
    """_system_prompt() should always reference SOUL.md in its output."""
    _patch_workspace(tmp_path, monkeypatch)

    from smolclaw.agent import _system_prompt
    prompt = _system_prompt()
    assert isinstance(prompt, str)
    assert "SOUL" in prompt


@pytest.mark.asyncio
async def test_run_returns_string(tmp_path, monkeypatch):
    """run() should return a string (mock the entire ClaudeSDKClient)."""
    _patch_workspace(tmp_path, monkeypatch)

    import smolclaw.agent as ag

    # Build a mock async generator that yields an AssistantMessage with TextBlock
    from claude_agent_sdk import AssistantMessage, TextBlock

    async def _fake_receive():
        msg = MagicMock(spec=AssistantMessage)
        block = MagicMock(spec=TextBlock)
        block.text = "Hello, world!"
        msg.content = [block]
        yield msg

    mock_client = AsyncMock(spec=ag.ClaudeSDKClient)
    mock_client.receive_response.return_value = _fake_receive()

    # Pre-populate _sessions so we skip creation
    ag._sessions["test-chat"] = ag._Session(client=mock_client, dynamic_tool_names=frozenset())

    # Patch load_custom_tools to return empty (no dynamic tools)
    with patch("smolclaw.agent.load_custom_tools", return_value=[]):
        try:
            result = await ag.run(chat_id="test-chat", user_message="hi")
            assert isinstance(result, str)
            assert "Hello" in result
        finally:
            ag._sessions.pop("test-chat", None)


@pytest.mark.asyncio
async def test_dynamic_tool_change_triggers_reconnect(tmp_path, monkeypatch):
    """When dynamic tools change, old client should be disconnected and new one created."""
    _patch_workspace(tmp_path, monkeypatch)

    import smolclaw.agent as ag
    from claude_agent_sdk import AssistantMessage, TextBlock

    async def _fake_receive():
        msg = MagicMock(spec=AssistantMessage)
        block = MagicMock(spec=TextBlock)
        block.text = "OK"
        msg.content = [block]
        yield msg

    old_client = AsyncMock(spec=ag.ClaudeSDKClient)
    old_client.receive_response.return_value = _fake_receive()

    # Pre-populate with old client that has a different tool set
    ag._sessions["reconnect-test"] = ag._Session(client=old_client, dynamic_tool_names=frozenset({"old_tool"}))

    # Mock a new dynamic tool
    new_mock_tool = MagicMock()
    new_mock_tool.name = "new_tool"

    new_client = AsyncMock(spec=ag.ClaudeSDKClient)
    new_client.receive_response.return_value = _fake_receive()

    with patch("smolclaw.agent.load_custom_tools", return_value=[new_mock_tool]), \
         patch("smolclaw.agent.create_sdk_mcp_server", return_value=MagicMock()), \
         patch("smolclaw.agent.ClaudeSDKClient", return_value=new_client), \
         patch("smolclaw.agent._make_options", return_value=MagicMock()):
        try:
            result = await ag.run(chat_id="reconnect-test", user_message="hi")
            # Old client should have been disconnected
            old_client.disconnect.assert_awaited_once()
            # New client should have been connected
            new_client.connect.assert_awaited_once()
            assert isinstance(result, str)
        finally:
            ag._sessions.pop("reconnect-test", None)
