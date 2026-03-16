"""Agent tests — mock SDK client to avoid network calls."""
from __future__ import annotations

import asyncio
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
    assert isinstance(prompt, str)
    assert "SOUL" in prompt


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
            assert isinstance(result, str)
            assert "Hello" in result
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


# ---------------------------------------------------------------------------
# Phase 1.1: Session lock race condition
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_run_same_chat_no_duplicate_sessions(tmp_path, monkeypatch):
    """Two concurrent run() calls for a new chat_id should not create duplicate sessions."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    connect_count = 0

    async def tracked_connect():
        nonlocal connect_count
        connect_count += 1
        await asyncio.sleep(0.05)  # simulate latency

    mock_client = AsyncMock(spec=ag.ClaudeSDKClient)
    mock_client.connect = tracked_connect
    mock_client.receive_response.return_value = _make_fake_receive("ok")()

    with patch("smolclaw.agent.load_custom_tools", return_value=[]), \
         patch("smolclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("smolclaw.agent._make_options", return_value=MagicMock()):
        try:
            _r1, _r2 = await asyncio.gather(
                ag.run(chat_id="race-test", user_message="a"),
                ag.run(chat_id="race-test", user_message="b"),
            )
            # Lock ensures only one session creation
            assert connect_count == 1
        finally:
            ag._sessions.pop("race-test", None)
            ag._session_locks.pop("race-test", None)


# ---------------------------------------------------------------------------
# Phase 1.2: Handover lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handover_cleared_even_on_agent_error(tmp_path, monkeypatch):
    """Handover is always cleared after first turn — even on error, it's already in the system prompt."""
    _patch_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("smolclaw.workspace.HANDOVER", tmp_path / "handover.md")
    (tmp_path / "handover.md").write_text("# Important handover")

    import smolclaw.agent as ag

    mock_client = AsyncMock(spec=ag.ClaudeSDKClient)
    mock_client.query.side_effect = RuntimeError("agent crash")

    with patch("smolclaw.agent.load_custom_tools", return_value=[]), \
         patch("smolclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("smolclaw.agent._make_options", return_value=MagicMock()):
        try:
            result = await ag.run(chat_id="handover-err", user_message="hi")
            assert "wrong" in result.lower()  # sanitized error
            assert not (tmp_path / "handover.md").exists()  # cleared in finally block
        finally:
            ag._sessions.pop("handover-err", None)
            ag._session_locks.pop("handover-err", None)


@pytest.mark.asyncio
async def test_handover_deleted_after_success(tmp_path, monkeypatch):
    """After a successful run, the handover file should be cleared."""
    _patch_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("smolclaw.workspace.HANDOVER", tmp_path / "handover.md")
    (tmp_path / "handover.md").write_text("# Handover to clear")

    import smolclaw.agent as ag

    mock_client = AsyncMock(spec=ag.ClaudeSDKClient)
    mock_client.receive_response.return_value = _make_fake_receive("Success!")()

    with patch("smolclaw.agent.load_custom_tools", return_value=[]), \
         patch("smolclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("smolclaw.agent._make_options", return_value=MagicMock()):
        try:
            result = await ag.run(chat_id="handover-ok", user_message="hi")
            assert "Success" in result
            assert not (tmp_path / "handover.md").exists()  # deleted
        finally:
            ag._sessions.pop("handover-ok", None)
            ag._session_locks.pop("handover-ok", None)


# ---------------------------------------------------------------------------
# Phase 1.5: Handover size cap
# ---------------------------------------------------------------------------

def test_oversized_handover_truncated_in_system_prompt(tmp_path, monkeypatch):
    _patch_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("smolclaw.workspace.HANDOVER", tmp_path / "handover.md")
    big_content = "x" * 8000
    (tmp_path / "handover.md").write_text(big_content)

    from smolclaw.agent import _system_prompt
    prompt = _system_prompt()
    # The handover section should be truncated to 4000 chars
    # Find the handover content in the prompt
    assert "x" * 4000 in prompt
    assert "x" * 4001 not in prompt


# ---------------------------------------------------------------------------
# Phase 1.6: Error sanitization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_returns_generic_error_not_traceback(tmp_path, monkeypatch):
    """run() should return a generic error message, not the raw exception."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    mock_client = AsyncMock(spec=ag.ClaudeSDKClient)
    mock_client.query.side_effect = ValueError("secret internal detail")

    with patch("smolclaw.agent.load_custom_tools", return_value=[]), \
         patch("smolclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("smolclaw.agent._make_options", return_value=MagicMock()):
        try:
            result = await ag.run(chat_id="error-test", user_message="hi")
            assert "secret internal detail" not in result
            assert "wrong" in result.lower() or "try again" in result.lower()
        finally:
            ag._sessions.pop("error-test", None)
            ag._session_locks.pop("error-test", None)


# ---------------------------------------------------------------------------
# spawn_task sub-agent initialization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_task_passes_model_and_cwd_to_subagent(tmp_path, monkeypatch):
    """spawn_task must pass model and cwd to the sub-agent ClaudeAgentOptions."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag
    from smolclaw.config import Config

    cfg = Config.load()
    spawn_tool = ag._make_spawn_task_tool("test-chat", cfg)

    captured_opts = {}

    async def fake_query(prompt, options):
        captured_opts["model"] = options.model
        captured_opts["cwd"] = options.cwd
        # Yield nothing — sub-agent produces no output
        return
        yield  # make it an async generator

    with patch("smolclaw.agent.query", fake_query), \
         patch("smolclaw.tools._send_telegram"):
        await spawn_tool.handler({"task": "say hello"})
        # Let the background task run
        await asyncio.sleep(0.1)

    assert captured_opts.get("model") == cfg.get("model"), \
        f"Sub-agent model should be {cfg.get('model')!r}, got {captured_opts.get('model')!r}"
    assert captured_opts.get("cwd") == str(tmp_path), \
        f"Sub-agent cwd should be {str(tmp_path)!r}, got {captured_opts.get('cwd')!r}"


# ---------------------------------------------------------------------------
# Task registry cleanup
# ---------------------------------------------------------------------------

def test_list_tasks_excludes_old_completed():
    """Completed tasks older than 1 hour should be pruned from the registry."""
    import time

    import smolclaw.agent as ag

    # Clear registry
    ag._task_registry.clear()

    # Add a done task from 2 hours ago
    mock_task = MagicMock()
    mock_task.done.return_value = True
    ag._task_registry["old-done"] = {
        "task": mock_task,
        "description": "old task",
        "started_at": time.time() - 7200,
        "status": "done",
    }

    # Add a recent running task
    mock_running = MagicMock()
    mock_running.done.return_value = False
    ag._task_registry["still-running"] = {
        "task": mock_running,
        "description": "active task",
        "started_at": time.time() - 60,
        "status": "running",
    }

    tasks = ag.list_tasks()
    task_ids = [t["id"] for t in tasks]
    assert "still-running" in task_ids
    assert "old-done" not in task_ids

    # Registry should have been pruned
    assert "old-done" not in ag._task_registry
    ag._task_registry.clear()
