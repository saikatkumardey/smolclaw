"""Tool unit tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _mock_httpx_client(ok=True, text="Unauthorized"):
    """Return a mock httpx.Client class simulating a context manager."""
    resp = MagicMock()
    resp.is_success = ok
    resp.text = text

    client = MagicMock()
    client.post.return_value = resp
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    return MagicMock(return_value=client)


def test_send_telegram_success():
    from smolclaw.tools import _send_telegram
    with patch("smolclaw.tools.httpx.Client", _mock_httpx_client(ok=True)):
        result = _send_telegram(chat_id="123", message="hi")
        assert result.startswith("Sent.")


def test_send_telegram_failure():
    from smolclaw.tools import _send_telegram
    with patch("smolclaw.tools.httpx.Client", _mock_httpx_client(ok=False)):
        assert "Failed" in _send_telegram(chat_id="123", message="hi")


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
    with patch("smolclaw.tools.httpx.Client", _mock_httpx_client(ok=True)):
        result = asyncio.run(telegram_send.handler({"chat_id": "123", "message": "hi"}))
    assert result["content"][0]["text"].startswith("Sent.")


def test_telegram_send_sdk_failure(monkeypatch):
    monkeypatch.setenv("ALLOWED_USER_IDS", "123")
    from smolclaw.tools_sdk import telegram_send
    with patch("smolclaw.tools.httpx.Client", _mock_httpx_client(ok=False)):
        result = asyncio.run(telegram_send.handler({"chat_id": "123", "message": "hi"}))
    assert "Failed" in result["content"][0]["text"]
