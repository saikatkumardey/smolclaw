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
