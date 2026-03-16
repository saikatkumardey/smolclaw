"""tools_sdk tests — auth guards, save_handover, update_config."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_workspace(tmp_path, monkeypatch):
    import smolclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")
    monkeypatch.setattr(ws, "CONFIG", tmp_path / "smolclaw.json")


async def _call_tool(tool_obj, args: dict) -> dict:
    """Call the underlying async function of an SdkMcpTool."""
    return await tool_obj.handler(args)


class TestTelegramSend:
    @pytest.mark.asyncio
    async def test_rejects_non_allowed_chat_id(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "111")
        from smolclaw.tools_sdk import telegram_send
        result = await _call_tool(telegram_send, {"chat_id": "999", "message": "hi"})
        text = result["content"][0]["text"]
        assert "not in ALLOWED_USER_IDS" in text

    @pytest.mark.asyncio
    async def test_allowed_chat_id_sends(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "111")
        from smolclaw.tools_sdk import telegram_send
        with patch("smolclaw.tools_sdk._send_telegram", return_value="Sent."):
            result = await _call_tool(telegram_send, {"chat_id": "111", "message": "hi"})
        assert result["content"][0]["text"] == "Sent."


class TestTelegramSendFile:
    @pytest.mark.asyncio
    async def test_rejects_non_allowed_chat_id(self, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "111")
        from smolclaw.tools_sdk import telegram_send_file
        result = await _call_tool(telegram_send_file, {"chat_id": "999", "file_path": "/tmp/x"})
        text = result["content"][0]["text"]
        assert "not in ALLOWED_USER_IDS" in text

    @pytest.mark.asyncio
    async def test_rejects_path_outside_workspace(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ALLOWED_USER_IDS", "111")
        _patch_workspace(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import telegram_send_file
        result = await _call_tool(telegram_send_file, {"chat_id": "111", "file_path": "/etc/passwd"})
        text = result["content"][0]["text"]
        assert "outside the workspace" in text


class TestSaveHandover:
    @pytest.mark.asyncio
    async def test_writes_handover_file(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import save_handover
        result = await _call_tool(save_handover, {"summary": "Test handover content"})
        text = result["content"][0]["text"]
        assert "saved" in text.lower()
        content = (tmp_path / "handover.md").read_text()
        assert "Test handover content" in content


class TestUpdateConfig:
    @pytest.mark.asyncio
    async def test_rejects_unknown_key(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import update_config
        result = await _call_tool(update_config, {"key": "nonexistent", "value": 42})
        text = result["content"][0]["text"]
        assert "Error" in text or "Cannot" in text

    @pytest.mark.asyncio
    async def test_rejects_model_key(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import update_config
        result = await _call_tool(update_config, {"key": "model", "value": 1})
        text = result["content"][0]["text"]
        assert "Cannot" in text or "models" in text.lower()

    @pytest.mark.asyncio
    async def test_sets_valid_key(self, monkeypatch, tmp_path):
        _patch_workspace(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import update_config
        result = await _call_tool(update_config, {"key": "max_turns", "value": 20})
        text = result["content"][0]["text"]
        assert "max_turns" in text
        assert "20" in text


# --- Staging tool constants ---

_VALID_STAGED_TOOL = '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo back input.",
        "parameters": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    },
}

def execute(msg: str = "") -> str:
    return f"echo: {msg}"
'''

_BAD_SCHEMA_TOOL = '''
SCHEMA = {"type": "function"}
def execute(): return "x"
'''

_MISSING_EXECUTE_TOOL = '''
SCHEMA = {
    "type": "function",
    "function": {"name": "bad", "description": "No execute."},
}
'''

_RAISING_TOOL = '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "boom",
        "description": "Always raises.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

def execute() -> str:
    raise ValueError("kaboom")
'''


def _setup_staging(tmp_path, monkeypatch):
    """Set up workspace paths with a staging directory."""
    import smolclaw.workspace as ws
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    staging = tools_dir / ".staging"
    staging.mkdir()
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "TOOLS_DIR", tools_dir)
    monkeypatch.setattr(ws, "TOOLS_STAGING", staging)
    return tools_dir, staging


class TestTestTool:
    @pytest.mark.asyncio
    async def test_valid_tool_passes(self, monkeypatch, tmp_path):
        _tools_dir, staging = _setup_staging(tmp_path, monkeypatch)
        (staging / "echo.py").write_text(_VALID_STAGED_TOOL)
        from smolclaw.tools_sdk import test_tool
        result = await _call_tool(test_tool, {"file_name": "echo.py"})
        assert "PASS" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_execute_fails(self, monkeypatch, tmp_path):
        _tools_dir, staging = _setup_staging(tmp_path, monkeypatch)
        (staging / "bad.py").write_text(_MISSING_EXECUTE_TOOL)
        from smolclaw.tools_sdk import test_tool
        result = await _call_tool(test_tool, {"file_name": "bad.py"})
        assert "FAIL" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_bad_schema_fails(self, monkeypatch, tmp_path):
        _tools_dir, staging = _setup_staging(tmp_path, monkeypatch)
        (staging / "bad.py").write_text(_BAD_SCHEMA_TOOL)
        from smolclaw.tools_sdk import test_tool
        result = await _call_tool(test_tool, {"file_name": "bad.py"})
        assert "FAIL" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_with_test_args(self, monkeypatch, tmp_path):
        _tools_dir, staging = _setup_staging(tmp_path, monkeypatch)
        (staging / "echo.py").write_text(_VALID_STAGED_TOOL)
        from smolclaw.tools_sdk import test_tool
        result = await _call_tool(test_tool, {"file_name": "echo.py", "test_args": '{"msg": "hi"}'})
        text = result["content"][0]["text"]
        assert "PASS" in text
        assert "echo: hi" in text

    @pytest.mark.asyncio
    async def test_execute_exception_reported(self, monkeypatch, tmp_path):
        _tools_dir, staging = _setup_staging(tmp_path, monkeypatch)
        (staging / "boom.py").write_text(_RAISING_TOOL)
        from smolclaw.tools_sdk import test_tool
        result = await _call_tool(test_tool, {"file_name": "boom.py", "test_args": "{}"})
        text = result["content"][0]["text"]
        assert "PASS" in text  # validation passes
        assert "kaboom" in text  # but execute() error is reported

    @pytest.mark.asyncio
    async def test_missing_file(self, monkeypatch, tmp_path):
        _setup_staging(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import test_tool
        result = await _call_tool(test_tool, {"file_name": "nope.py"})
        assert "FAIL" in result["content"][0]["text"]


class TestDeployTool:
    @pytest.mark.asyncio
    async def test_moves_file(self, monkeypatch, tmp_path):
        tools_dir, staging = _setup_staging(tmp_path, monkeypatch)
        (staging / "echo.py").write_text(_VALID_STAGED_TOOL)
        from smolclaw.tools_sdk import deploy_tool
        result = await _call_tool(deploy_tool, {"file_name": "echo.py"})
        text = result["content"][0]["text"]
        assert "Deployed" in text
        assert (tools_dir / "echo.py").exists()
        assert not (staging / "echo.py").exists()

    @pytest.mark.asyncio
    async def test_refuses_invalid(self, monkeypatch, tmp_path):
        _tools_dir, staging = _setup_staging(tmp_path, monkeypatch)
        (staging / "bad.py").write_text(_BAD_SCHEMA_TOOL)
        from smolclaw.tools_sdk import deploy_tool
        result = await _call_tool(deploy_tool, {"file_name": "bad.py"})
        text = result["content"][0]["text"]
        assert "Refused" in text
        assert (staging / "bad.py").exists()  # not moved

    @pytest.mark.asyncio
    async def test_missing_file(self, monkeypatch, tmp_path):
        _setup_staging(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import deploy_tool
        result = await _call_tool(deploy_tool, {"file_name": "nope.py"})
        assert "not found" in result["content"][0]["text"]


class TestDisableTool:
    @pytest.mark.asyncio
    async def test_renames_to_disabled(self, monkeypatch, tmp_path):
        tools_dir, _staging = _setup_staging(tmp_path, monkeypatch)
        (tools_dir / "echo.py").write_text(_VALID_STAGED_TOOL)
        from smolclaw.tools_sdk import disable_tool
        result = await _call_tool(disable_tool, {"tool_name": "echo.py"})
        text = result["content"][0]["text"]
        assert "Disabled" in text
        assert (tools_dir / "echo.py.disabled").exists()
        assert not (tools_dir / "echo.py").exists()

    @pytest.mark.asyncio
    async def test_accepts_stem_without_py(self, monkeypatch, tmp_path):
        tools_dir, _staging = _setup_staging(tmp_path, monkeypatch)
        (tools_dir / "echo.py").write_text(_VALID_STAGED_TOOL)
        from smolclaw.tools_sdk import disable_tool
        result = await _call_tool(disable_tool, {"tool_name": "echo"})
        assert "Disabled" in result["content"][0]["text"]
        assert (tools_dir / "echo.py.disabled").exists()

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, monkeypatch, tmp_path):
        _setup_staging(tmp_path, monkeypatch)
        from smolclaw.tools_sdk import disable_tool
        result = await _call_tool(disable_tool, {"tool_name": "nope"})
        assert "not found" in result["content"][0]["text"]
