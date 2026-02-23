"""Tool unit tests."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from smolclaw.tools import shell_exec, file_read, file_write


def test_shell_exec_echo():
    assert shell_exec("echo hello") == "hello"


def test_shell_exec_timeout():
    result = shell_exec("sleep 10", timeout=1)
    assert "Timeout" in result


def test_file_write_and_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    file_write("test.txt", "hello world")
    assert file_read("test.txt") == "hello world"


def test_file_read_missing():
    result = file_read("/nonexistent/path/file.txt")
    assert "Error" in result


def test_web_search_offline():
    with patch("smolclaw.tools.requests.get") as mock_get:
        mock_get.return_value = MagicMock(json=lambda: {"results": []})
        result = web_search("test query")
    assert result == "No results."


def web_search(query: str) -> str:
    from smolclaw.tools import web_search as _ws
    return _ws(query)


def test_handover_save_load_clear(tmp_path, monkeypatch):
    """Handover note survives save/load and is removed by clear."""
    import smolclaw.workspace as ws
    monkeypatch.setattr(ws, "HOME", tmp_path)
    monkeypatch.setattr(ws, "HANDOVER", tmp_path / "handover.md")

    from smolclaw import handover
    handover.save("working on fld — paused mid-implementation")
    text = handover.load()
    assert "fld" in text
    assert "Handover" in text

    handover.clear()
    assert handover.load() == ""


def test_custom_tool_loader(tmp_path):
    """Agent loads a valid custom tool and ignores invalid ones."""
    from smolclaw.tool_loader import load_custom_tools

    # Write a valid tool
    (tmp_path / "ping.py").write_text("""
SCHEMA = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Say pong.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

def execute() -> str:
    return "pong"
""")
    # Write an invalid tool (missing execute)
    (tmp_path / "broken.py").write_text("SCHEMA = {}")

    schemas, fn_map = load_custom_tools(tmp_path)
    assert len(schemas) == 1
    assert "ping" in fn_map
    assert fn_map["ping"]() == "pong"
