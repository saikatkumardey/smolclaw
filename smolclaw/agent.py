"""litellm tool-call loop."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import litellm

from .history import append as history_append, load as history_load
from .skills import load_skills
from .tools import TOOL_MAP, TOOLS

logger = logging.getLogger("smolclaw.agent")
MODEL = os.getenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-6")
MAX_STEPS = 10


def _system_prompt() -> str:
    parts = [f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    for fname in ("SOUL.md", "USER.md", "MEMORY.md"):
        p = Path(fname)
        if p.exists():
            parts.append(f"=== {fname} ===\n{p.read_text().strip()}")
    if skills := load_skills():
        parts.append(f"=== AVAILABLE SKILLS ===\n{skills}")
    return "\n\n".join(parts)


def run(chat_id: str, user_message: str) -> str:
    history = history_load(chat_id)
    messages = [{"role": "system", "content": _system_prompt()}, *history, {"role": "user", "content": user_message}]
    history_append(chat_id, "user", user_message)

    for _ in range(MAX_STEPS):
        response = litellm.completion(model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto")
        msg = response.choices[0].message
        finish = response.choices[0].finish_reason

        if finish == "stop" or not msg.tool_calls:
            reply = msg.content or ""
            history_append(chat_id, "assistant", reply)
            return reply

        messages.append(msg.model_dump(exclude_unset=True))
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            logger.info("Tool: %s(%s)", tc.function.name, args)
            result = TOOL_MAP.get(tc.function.name, lambda **_: "Unknown tool")(**args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    return "Max steps reached."
