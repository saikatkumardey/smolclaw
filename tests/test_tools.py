"""Tool unit tests — smolagents Tool subclass API."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from smolclaw.tools import (
    FileReadTool,
    FileWriteTool,
    SaveHandoverTool,
    ShellExecTool,
    TelegramSendTool,
)


# ---------------------------------------------------------------------------
# ShellExecTool
# ---------------------------------------------------------------------------

def test_shell_exec_echo():
    tool = ShellExecTool()
    result = tool.forward(command="echo hello")
    assert result == "hello"


def test_shell_exec_timeout():
    tool = ShellExecTool()
    result = tool.forward(command="sleep 10", timeout=1)
    assert "Timeout" in result


# ---------------------------------------------------------------------------
# FileWriteTool + FileReadTool
# ---------------------------------------------------------------------------

def test_file_write_and_read_roundtrip(tmp_path):
    write_tool = FileWriteTool()
    read_tool = FileReadTool()
    target = str(tmp_path / "hello.txt")

    write_result = write_tool.forward(path=target, content="hello world")
    assert "Written" in write_result

    read_result = read_tool.forward(path=target)
    assert read_result == "hello world"


def test_file_read_missing_file():
    read_tool = FileReadTool()
    result = read_tool.forward(path="/nonexistent/path/does_not_exist.txt")
    assert "Error" in result


# ---------------------------------------------------------------------------
# TelegramSendTool
# ---------------------------------------------------------------------------

def test_telegram_send_success():
    tool = TelegramSendTool()
    mock_response = MagicMock()
    mock_response.ok = True

    with patch("smolclaw.tools.requests.post", return_value=mock_response) as mock_post:
        result = tool.forward(chat_id="123456", message="hello")

    assert result == "Sent."
    mock_post.assert_called_once()


def test_telegram_send_failure():
    tool = TelegramSendTool()
    mock_response = MagicMock()
    mock_response.ok = False
    mock_response.text = "Unauthorized"

    with patch("smolclaw.tools.requests.post", return_value=mock_response):
        result = tool.forward(chat_id="123456", message="hello")

    assert "Failed" in result


# ---------------------------------------------------------------------------
# SaveHandoverTool
# ---------------------------------------------------------------------------

def test_save_handover_writes_file(tmp_path, monkeypatch):
    import smolclaw.workspace as ws

    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")

    tool = SaveHandoverTool()
    result = tool.forward(summary="working on feature X")

    assert "Handover saved" in result
    handover_file = tmp_path / "handover.md"
    assert handover_file.exists()
    content = handover_file.read_text()
    assert "working on feature X" in content
