"""Agent tests — mock SDK client to avoid network calls."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_workspace(tmp_path, monkeypatch):
    import smolclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    for name, attr in [("SOUL.md", "SOUL"), ("USER.md", "USER"),
                        ("MEMORY.md", "MEMORY"), ("skills", "SKILLS_DIR"),
                        ("handover.md", "HANDOVER")]:
        monkeypatch.setattr(ws, attr, tmp_path / name)
    (tmp_path / "sessions").mkdir(exist_ok=True)
    (tmp_path / "SOUL.md").write_text("## Identity\nNot set yet")
    (tmp_path / "USER.md").write_text("Not set yet")


def _make_fake_receive(text="OK"):
    from claude_agent_sdk import AssistantMessage, TextBlock
    async def _recv():
        msg = MagicMock(spec=AssistantMessage)
        block = MagicMock(spec=TextBlock)
        block.text = text
        msg.content = [block]
        yield msg
    return _recv


def test_system_prompt_contains_soul(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    from smolclaw.agent import _system_prompt
    prompt = _system_prompt()
    assert isinstance(prompt, str) and "SOUL" in prompt


@pytest.mark.asyncio
async def test_run_returns_string(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    mock_client = AsyncMock(spec=ag.ClaudeSDKClient)
    mock_client.receive_response.return_value = _make_fake_receive("Hello, world!")()
    ag._sessions["test-chat"] = ag._Session(client=mock_client, dynamic_tool_names=frozenset())

    with patch("smolclaw.agent.load_custom_tools", return_value=[]):
        try:
            result = await ag.run(chat_id="test-chat", user_message="hi")
            assert isinstance(result, str) and "Hello" in result
        finally:
            ag._sessions.pop("test-chat", None)


@pytest.mark.asyncio
async def test_dynamic_tool_change_triggers_reconnect(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    old_client = AsyncMock(spec=ag.ClaudeSDKClient)
    old_client.receive_response.return_value = _make_fake_receive()()
    ag._sessions["reconnect-test"] = ag._Session(client=old_client, dynamic_tool_names=frozenset({"old_tool"}))

    new_mock_tool = MagicMock()
    new_mock_tool.name = "new_tool"
    new_client = AsyncMock(spec=ag.ClaudeSDKClient)
    new_client.receive_response.return_value = _make_fake_receive()()

    with patch("smolclaw.agent.load_custom_tools", return_value=[new_mock_tool]), \
         patch("smolclaw.agent.create_sdk_mcp_server", return_value=MagicMock()), \
         patch("smolclaw.agent.ClaudeSDKClient", return_value=new_client), \
         patch("smolclaw.agent._make_options", return_value=MagicMock()):
        try:
            result = await ag.run(chat_id="reconnect-test", user_message="hi")
            old_client.disconnect.assert_awaited_once()
            new_client.connect.assert_awaited_once()
            assert isinstance(result, str)
        finally:
            ag._sessions.pop("reconnect-test", None)
