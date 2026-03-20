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


# ---------------------------------------------------------------------------
# Subconscious gets slim options
# ---------------------------------------------------------------------------

def test_subconscious_options_slim_tools(tmp_path, monkeypatch):
    """Subconscious should get only telegram_send, update_subconscious, reflect — not the full tool set."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    opts = ag._make_options("cron:subconscious")
    tool_names = opts.allowed_tools
    # Must have the three slim tools
    assert "mcp__smolclaw__telegram_send" in tool_names
    assert "mcp__smolclaw__update_subconscious" in tool_names
    assert "mcp__smolclaw__reflect" in tool_names
    # Must NOT have browser, deploy, etc.
    assert "mcp__smolclaw__browse" not in tool_names
    assert "mcp__smolclaw__browser_click" not in tool_names
    assert "mcp__smolclaw__deploy_tool" not in tool_names


def test_subconscious_options_max_turns_capped(tmp_path, monkeypatch):
    """Subconscious should get max_turns=3, not the default 10."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    opts = ag._make_options("cron:subconscious")
    assert opts.max_turns == 3


def test_subconscious_skips_dynamic_mcp(tmp_path, monkeypatch):
    """Subconscious should not include dynamic MCP server even when tools exist."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    fake_mcp = MagicMock()
    opts = ag._make_options("cron:subconscious", dynamic_mcp_server=fake_mcp)
    # dynamic server should be excluded
    assert "dynamic" not in opts.mcp_servers
    assert "mcp__dynamic__*" not in opts.allowed_tools


def test_regular_cron_still_gets_full_tools(tmp_path, monkeypatch):
    """Regular cron jobs should still get the full CUSTOM_TOOLS set."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    opts = ag._make_options("cron:some-other-job")
    tool_names = opts.allowed_tools
    assert "mcp__smolclaw__browse" in tool_names


# ---------------------------------------------------------------------------
# Heartbeat gets slim options
# ---------------------------------------------------------------------------

def test_heartbeat_options_slim_tools(tmp_path, monkeypatch):
    """Heartbeat should only get telegram_send."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    opts = ag._make_options("cron:heartbeat")
    tool_names = opts.allowed_tools
    assert "mcp__smolclaw__telegram_send" in tool_names
    assert "mcp__smolclaw__browse" not in tool_names
    assert "mcp__smolclaw__update_subconscious" not in tool_names


def test_heartbeat_max_turns_capped(tmp_path, monkeypatch):
    """Heartbeat should get max_turns=2."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    opts = ag._make_options("cron:heartbeat")
    assert opts.max_turns == 2


# ---------------------------------------------------------------------------
# Session lock cleanup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _build_auto_handover — streaming, no OOM
# ---------------------------------------------------------------------------

def test_build_auto_handover_streams_large_file(tmp_path, monkeypatch):
    """_build_auto_handover should not load entire file into memory."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    # Write a large-ish JSONL file (many lines, only last few matter)
    import json
    log_file = sessions_dir / "2026-03-19.jsonl"
    with open(log_file, "w") as f:
        # 500 filler lines for a different chat
        for i in range(500):
            f.write(json.dumps({"chat_id": "other", "role": "user", "content": f"msg {i}", "ts": "2026-03-19T00:00:00"}) + "\n")
        # 5 lines for our chat
        for i in range(5):
            f.write(json.dumps({"chat_id": "my-chat", "role": "user", "content": f"important msg {i}", "ts": "2026-03-19T01:00:00"}) + "\n")

    result = ag._build_auto_handover("my-chat")
    assert "important msg" in result
    assert len(result) < 4000


def test_build_auto_handover_skips_huge_files(tmp_path, monkeypatch):
    """Files over the size cap should be skipped to avoid OOM."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    # Create a file that exceeds the size cap (we'll set a low cap for testing)
    log_file = sessions_dir / "2026-03-19.jsonl"
    import json
    with open(log_file, "w") as f:
        for i in range(5):
            f.write(json.dumps({"chat_id": "test", "role": "user", "content": f"msg {i}", "ts": "2026-03-19"}) + "\n")

    # Should work for normal-sized files
    result = ag._build_auto_handover("test")
    assert "msg" in result


# ---------------------------------------------------------------------------
# Session lock cleanup
# ---------------------------------------------------------------------------

def test_prune_stale_locks_removes_orphaned_locks():
    """Locks without a corresponding session should be pruned."""
    import smolclaw.agent as ag

    # Create orphaned lock entry
    ag._session_locks["orphan-chat"] = asyncio.Lock()
    assert "orphan-chat" in ag._session_locks

    ag._prune_stale_locks()
    assert "orphan-chat" not in ag._session_locks


@pytest.mark.asyncio
async def test_cron_lock_cleaned_after_run(tmp_path, monkeypatch):
    """Cron job locks should be cleaned up after run() completes."""
    _patch_workspace(tmp_path, monkeypatch)
    import smolclaw.agent as ag

    mock_client = AsyncMock(spec=ag.ClaudeSDKClient)
    mock_client.receive_response.return_value = _make_fake_receive("done")()

    with patch("smolclaw.agent.load_custom_tools", return_value=[]), \
         patch("smolclaw.agent.ClaudeSDKClient", return_value=mock_client), \
         patch("smolclaw.agent._make_options", return_value=MagicMock()):
        try:
            await ag.run(chat_id="cron:test-cleanup", user_message="hi")
        finally:
            ag._sessions.pop("cron:test-cleanup", None)

    # Cron lock should be cleaned up since session was popped
    assert "cron:test-cleanup" not in ag._session_locks
