"""Dynamic tool loader — wraps tools/*.py files as smolagents Tool subclasses."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from smolagents import Tool

from loguru import logger


def _make_tool_class(tool_name: str, tool_desc: str, inputs: dict, execute_fn) -> type:
    """
    Dynamically build a Tool subclass with a properly-typed forward() method
    so smolagents' signature inspection is satisfied.
    """
    param_names = list(inputs.keys())
    required = [k for k, v in inputs.items() if not v.get("nullable", False)]

    # Build the parameter list string for def forward(self, ...)
    # Required params first, nullable ones get default=None
    sig_parts = []
    call_parts = []
    for name in param_names:
        if name in required or not inputs[name].get("nullable", False):
            sig_parts.append(name)
        else:
            sig_parts.append(f"{name}=None")
        call_parts.append(f"{name}={name}")

    sig_str = ", ".join(sig_parts)
    call_str = ", ".join(call_parts)

    if sig_str:
        func_src = f"def forward(self, {sig_str}):\n    return str(_execute({call_str}))\n"
    else:
        func_src = "def forward(self):\n    return str(_execute())\n"

    # Execute the function definition in a namespace that captures _execute
    namespace = {"_execute": execute_fn}
    exec(func_src, namespace)
    forward_fn = namespace["forward"]

    klass = type(
        f"DynTool_{tool_name}",
        (Tool,),
        {
            "name": tool_name,
            "description": tool_desc,
            "inputs": inputs,
            "output_type": "string",
            "forward": forward_fn,
        },
    )
    return klass


def load_custom_tools(tools_dir: Path | None = None) -> list[Tool]:
    """
    Scan tools/*.py for user-defined tools.

    Convention — each file must export:
        SCHEMA: dict   — OpenAI-style function schema
        execute        — callable(**kwargs) -> str

    Wraps each as a smolagents Tool subclass and returns the list.
    """
    if tools_dir is None:
        from . import workspace
        tools_dir = workspace.TOOLS_DIR

    tool_list: list[Tool] = []
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

            # Build smolagents inputs dict
            inputs: dict = {}
            for param_name, param_info in properties.items():
                inputs[param_name] = {
                    "type": param_info.get("type", "string"),
                    "description": param_info.get("description", param_name),
                }
                if param_name not in required_params:
                    inputs[param_name]["nullable"] = True

            DynTool = _make_tool_class(tool_name, tool_desc, inputs, mod.execute)
            tool_list.append(DynTool())
            logger.info("Loaded custom tool: %s", tool_name)

        except Exception as e:
            logger.error("Failed to load tool %s: %s", path.name, e)

    return tool_list
