"""Dynamic tool loader — wraps tools/*.py files as claude-agent-sdk @tool functions."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server, SdkMcpTool
from claude_agent_sdk.types import McpSdkServerConfig

from loguru import logger


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
    """
    if tools_dir is None:
        from . import workspace
        tools_dir = workspace.TOOLS_DIR

    tool_list: list[SdkMcpTool] = []
    if not tools_dir.exists():
        return tool_list

    for path in sorted(tools_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            if not (hasattr(mod, "SCHEMA") and hasattr(mod, "execute")):
                logger.warning("Skipping %s — missing SCHEMA or execute()", path.name)
                continue

            fn_def = mod.SCHEMA["function"]
            tool_name = fn_def["name"]
            tool_desc = fn_def.get("description", "")
            params = fn_def.get("parameters", {})
            properties = params.get("properties", {})
            required_params = params.get("required", [])

            sdk_tool = _make_sdk_tool(tool_name, tool_desc, properties, required_params, mod.execute)
            tool_list.append(sdk_tool)
            logger.info("Loaded custom tool: %s", tool_name)

        except Exception as e:
            logger.error("Failed to load tool %s: %s", path.name, e)

    return tool_list


def build_dynamic_mcp_server(tools_dir: Path | None = None) -> McpSdkServerConfig | None:
    """
    Load custom tools and wrap them into an in-process MCP server.
    Returns None if no tools are found.
    """
    tools = load_custom_tools(tools_dir)
    if not tools:
        return None
    return create_sdk_mcp_server(name="dynamic", version="1.0.0", tools=tools)
