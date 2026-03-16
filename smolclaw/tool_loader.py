"""Dynamic tool loader — wraps tools/*.py files as claude-agent-sdk @tool functions."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool, SdkMcpTool

from loguru import logger

_known_tool_files: set[str] = set()

# Per-file cache: path -> (mtime, SdkMcpTool)
_tool_cache: dict[str, tuple[float, SdkMcpTool]] = {}

# Directory-level cache: (dir_mtime, result_list) — skips glob+stat when dir unchanged
_dir_cache: tuple[float, list[SdkMcpTool]] | None = None


def _make_sdk_tool(name: str, desc: str, properties: dict, required: list, execute_fn) -> SdkMcpTool:
    """
    Build a claude-agent-sdk @tool-decorated async function wrapping a sync execute_fn.
    """
    # Build input_schema as {param: str} dict (SDK resolves types from annotations)
    input_schema: dict[str, Any] = {k: str for k in properties}

    @tool(name, desc, input_schema)
    async def _dyn_tool(args: dict) -> dict:
        kwargs = {k: args.get(k) for k in properties}
        result = await asyncio.to_thread(execute_fn, **kwargs)
        return {"content": [{"type": "text", "text": str(result)}]}

    return _dyn_tool


def load_custom_tools(tools_dir: Path | None = None) -> list[SdkMcpTool]:
    """
    Scan tools/*.py for user-defined tools.

    Convention — each file must export:
        SCHEMA: dict   — OpenAI-style function schema
        execute        — callable(**kwargs) -> str

    Wraps each as an SDK @tool function and returns the list.
    Modules are cached by mtime and only reloaded when the file changes.
    """
    if tools_dir is None:
        from . import workspace
        tools_dir = workspace.TOOLS_DIR

    global _dir_cache

    tool_list: list[SdkMcpTool] = []
    if not tools_dir.exists():
        return tool_list

    try:
        dir_mtime = tools_dir.stat().st_mtime
    except OSError:
        return tool_list

    # Fast path: directory unchanged — return cached list
    if _dir_cache is not None and _dir_cache[0] == dir_mtime:
        return list(_dir_cache[1])

    for path in sorted(tools_dir.glob("*.py")):
        str_path = str(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue

        # Return cached tool if file hasn't changed
        if str_path in _tool_cache:
            cached_mtime, cached_tool = _tool_cache[str_path]
            if mtime == cached_mtime:
                tool_list.append(cached_tool)
                continue

        if path.name not in _known_tool_files:
            logger.warning("New tool file detected: {} — loaded without integrity check", path.name)
            _known_tool_files.add(path.name)
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if not (hasattr(mod, "SCHEMA") and hasattr(mod, "execute")):
                logger.warning("Skipping {} — missing SCHEMA or execute()", path.name)
                continue

            fn_def = mod.SCHEMA["function"]
            tool_name = fn_def["name"]
            tool_desc = fn_def.get("description", "")
            params = fn_def.get("parameters", {})
            properties = params.get("properties", {})
            required_params = params.get("required", [])

            sdk_tool = _make_sdk_tool(tool_name, tool_desc, properties, required_params, mod.execute)
            _tool_cache[str_path] = (mtime, sdk_tool)
            tool_list.append(sdk_tool)
            logger.info("Loaded custom tool: {}", tool_name)

        except Exception as e:
            logger.error("Failed to load tool {}: {}", path.name, e)

    _dir_cache = (dir_mtime, tool_list)
    return tool_list


