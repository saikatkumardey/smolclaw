"""Dynamic tool loader. Scans tools/*.py and merges into the tool registry."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger("smolclaw.tool_loader")


def load_custom_tools(tools_dir: Path | None = None) -> tuple[list[dict], dict]:
    if tools_dir is None:
        from . import workspace
        tools_dir = workspace.TOOLS_DIR
    """
    Scan tools/*.py for user-defined tools.

    Convention — each file must export:
        SCHEMA: dict   — OpenAI-style function schema
        execute        — callable(**kwargs) -> str

    Returns (schemas, name→callable map).
    """
    schemas, fn_map = [], {}
    if not tools_dir.exists():
        return schemas, fn_map

    for path in sorted(tools_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not (hasattr(mod, "SCHEMA") and hasattr(mod, "execute")):
                logger.warning("Skipping %s — missing SCHEMA or execute()", path.name)
                continue
            name = mod.SCHEMA["function"]["name"]
            schemas.append(mod.SCHEMA)
            fn_map[name] = mod.execute
            logger.info("Loaded custom tool: %s", name)
        except Exception as e:
            logger.error("Failed to load tool %s: %s", path.name, e)

    return schemas, fn_map
