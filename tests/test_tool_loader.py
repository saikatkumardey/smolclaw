"""Dynamic tool loader tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


VALID_TOOL_SRC = '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Return pong.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

def execute() -> str:
    return "pong"
'''

TOOL_WITH_PARAM_SRC = '''
SCHEMA = {
    "type": "function",
    "function": {
        "name": "greet",
        "description": "Greet someone.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name to greet"},
            },
            "required": ["name"],
        },
    },
}

def execute(name: str) -> str:
    return f"Hello, {name}!"
'''

BROKEN_TOOL_SRC = "SCHEMA = {}"  # missing execute


def test_load_custom_tools_returns_tool(tmp_path):
    """A valid tool file should be loaded as a smolagents Tool."""
    from smolclaw.tool_loader import load_custom_tools
    from smolagents import Tool

    (tmp_path / "ping.py").write_text(VALID_TOOL_SRC)
    tools = load_custom_tools(tmp_path)

    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, Tool)
    assert tool.name == "ping"


def test_load_custom_tools_forward_works(tmp_path):
    """Loaded tool's forward() should call execute() and return output."""
    from smolclaw.tool_loader import load_custom_tools

    (tmp_path / "ping.py").write_text(VALID_TOOL_SRC)
    tools = load_custom_tools(tmp_path)
    assert tools[0].forward() == "pong"


def test_load_custom_tools_with_param(tmp_path):
    """Tool with parameters should pass them through correctly."""
    from smolclaw.tool_loader import load_custom_tools

    (tmp_path / "greet.py").write_text(TOOL_WITH_PARAM_SRC)
    tools = load_custom_tools(tmp_path)
    assert tools[0].forward(name="Alice") == "Hello, Alice!"


def test_load_custom_tools_skips_broken(tmp_path):
    """Files missing execute() should be skipped gracefully."""
    from smolclaw.tool_loader import load_custom_tools

    (tmp_path / "broken.py").write_text(BROKEN_TOOL_SRC)
    (tmp_path / "ping.py").write_text(VALID_TOOL_SRC)

    tools = load_custom_tools(tmp_path)
    # Only the valid tool should load
    assert len(tools) == 1
    assert tools[0].name == "ping"


def test_load_custom_tools_empty_dir(tmp_path):
    """Empty directory should return an empty list."""
    from smolclaw.tool_loader import load_custom_tools

    tools = load_custom_tools(tmp_path)
    assert tools == []


def test_load_custom_tools_nonexistent_dir(tmp_path):
    """Non-existent directory should return an empty list."""
    from smolclaw.tool_loader import load_custom_tools

    tools = load_custom_tools(tmp_path / "no_such_dir")
    assert tools == []
