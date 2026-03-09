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
