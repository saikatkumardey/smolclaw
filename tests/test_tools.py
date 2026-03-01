"""Tool unit tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_post(ok=True, text="Unauthorized"):
    resp = MagicMock()
    resp.ok = ok
    resp.text = text
    return resp


def test_telegram_sender_success():
    from smolclaw.tools import TelegramSender
    with patch("smolclaw.tools.requests.post", return_value=_mock_post(ok=True)):
        assert TelegramSender().send(chat_id="123", message="hi") == "Sent."


def test_telegram_sender_failure():
    from smolclaw.tools import TelegramSender
    with patch("smolclaw.tools.requests.post", return_value=_mock_post(ok=False)):
        assert "Failed" in TelegramSender().send(chat_id="123", message="hi")


def test_save_handover_writes_file(tmp_path, monkeypatch):
    import smolclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")
    from smolclaw.tools_sdk import save_handover
    result = asyncio.run(save_handover.handler({"summary": "working on feature X"}))
    assert result["content"][0]["text"] == "Handover saved."
    assert "working on feature X" in (tmp_path / "handover.md").read_text()


def test_telegram_send_sdk_success(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "123")
    from smolclaw.tools_sdk import telegram_send
    with patch("smolclaw.tools.requests.post", return_value=_mock_post(ok=True)):
        result = asyncio.run(telegram_send.handler({"chat_id": "123", "message": "hi"}))
    assert result["content"][0]["text"] == "Sent."


def test_telegram_send_sdk_failure(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "123")
    from smolclaw.tools_sdk import telegram_send
    with patch("smolclaw.tools.requests.post", return_value=_mock_post(ok=False)):
        result = asyncio.run(telegram_send.handler({"chat_id": "123", "message": "hi"}))
    assert "Failed" in result["content"][0]["text"]
