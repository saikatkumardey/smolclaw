"""Tool unit tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# TelegramSender (scheduler use)
# ---------------------------------------------------------------------------

def test_telegram_sender_success():
    from smolclaw.tools import TelegramSender
    sender = TelegramSender()
    mock_response = MagicMock()
    mock_response.ok = True

    with patch("smolclaw.tools.requests.post", return_value=mock_response) as mock_post:
        result = sender.send(chat_id="123456", message="hello")

    assert result == "Sent."
    mock_post.assert_called_once()


def test_telegram_sender_failure():
    from smolclaw.tools import TelegramSender
    sender = TelegramSender()
    mock_response = MagicMock()
    mock_response.ok = False
    mock_response.text = "Unauthorized"

    with patch("smolclaw.tools.requests.post", return_value=mock_response):
        result = sender.send(chat_id="123456", message="hello")

    assert "Failed" in result


# ---------------------------------------------------------------------------
# save_handover SDK tool
# ---------------------------------------------------------------------------

def test_save_handover_writes_file(tmp_path, monkeypatch):
    import smolclaw.workspace as ws

    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")

    from smolclaw.tools_sdk import save_handover

    # SdkMcpTool stores the async function in .handler
    result = asyncio.run(save_handover.handler({"summary": "working on feature X"}))

    assert result["content"][0]["text"] == "Handover saved."
    handover_file = tmp_path / "handover.md"
    assert handover_file.exists()
    content = handover_file.read_text()
    assert "working on feature X" in content


# ---------------------------------------------------------------------------
# telegram_send SDK tool
# ---------------------------------------------------------------------------

def test_telegram_send_sdk_success():
    from smolclaw.tools_sdk import telegram_send
    mock_response = MagicMock()
    mock_response.ok = True

    with patch("smolclaw.tools.requests.post", return_value=mock_response):
        result = asyncio.run(telegram_send.handler({"chat_id": "123", "message": "hi"}))

    assert result["content"][0]["text"] == "Sent."


def test_telegram_send_sdk_failure():
    from smolclaw.tools_sdk import telegram_send
    mock_response = MagicMock()
    mock_response.ok = False
    mock_response.text = "Unauthorized"

    with patch("smolclaw.tools.requests.post", return_value=mock_response):
        result = asyncio.run(telegram_send.handler({"chat_id": "123", "message": "hi"}))

    assert "Failed" in result["content"][0]["text"]
