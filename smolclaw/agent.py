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
from .tool_loader import load_custom_tools
from .tools import TOOL_MAP, TOOLS

logger = logging.getLogger("smolclaw.agent")
MODEL = os.getenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-6")
MAX_STEPS = 10

_CLI_PROTOCOL = """
## CLI Tool Learning Protocol

You can learn to use any CLI tool by pointing at its GitHub repo. No MCP servers. No connectors. Just CLIs.

When the user says "learn to use <repo>" or "install <url>":

### Step 1 — Clone and inspect
```
shell_exec("git clone <url> /tmp/<name> --depth 1")
shell_exec("cat /tmp/<name>/README.md")
shell_exec("cat /tmp/<name>/README* /tmp/<name>/docs/*.md 2>/dev/null | head -200")
```

### Step 2 — Figure out how to install
Check pyproject.toml / setup.py → use `uv tool install` or `uv pip install`
Check Cargo.toml → use `cargo install`
Check go.mod → use `go install`
Check package.json → use `npm install -g`
Binary releases → download from GitHub releases
When in doubt: `shell_exec("cd /tmp/<name> && cat pyproject.toml setup.py Makefile 2>/dev/null")`

### Step 3 — Install it
```
shell_exec("uv tool install /tmp/<name>")   # Python
shell_exec("cargo install --path /tmp/<name>")  # Rust
```

### Step 4 — Verify and explore
```
shell_exec("<tool> --help")
shell_exec("<tool> <subcommand> --help")
```

### Step 5 — Write a skill
Create `skills/<tool-name>/SKILL.md` with:
- What the tool does (1 sentence)
- How to install (exact command)
- Key commands with examples
- Common flags and options
- Any gotchas or prerequisites

### Step 6 — Confirm to the user
Tell them: tool installed, skill written, ready to use.

This works for ANY CLI — Python, Rust, Go, Node, shell scripts. The skill persists across sessions so you never have to re-learn it.
"""

_TOOLS_GUIDE = """
## Building Custom Tools

You can build new tools by writing Python files to the `tools/` directory.

Convention — every tool file must have:
1. `SCHEMA` — an OpenAI-style function schema dict
2. `execute(**kwargs) -> str` — the implementation

Example (`tools/get_weather.py`):

```python
SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"}
            },
            "required": ["city"]
        }
    }
}

def execute(city: str) -> str:
    import requests
    r = requests.get(f"https://wttr.in/{city}?format=3", timeout=5)
    return r.text if r.ok else f"Error: {r.status_code}"
```

Tools are loaded on every message — no restart needed.
Use `shell_exec` to install any required packages first (e.g. `uv pip install requests`).
Tell the user what tool you built and how to use it.
"""

_SKILLS_GUIDE = """
## Skills System

Skills live in `skills/<name>/SKILL.md`. They are loaded into your context every session.

You can create new skills when the user asks. For example:
- "Teach yourself how to check my server uptime" → create `skills/uptime/SKILL.md`
- "Remember how to log my meals" → create `skills/meal-logger/SKILL.md`
- "Build a skill for my morning briefing" → create `skills/morning-briefing/SKILL.md`

A good SKILL.md contains: what the skill does, step-by-step instructions, example commands, and any tool invocations needed.

When you create a skill, tell the user what you wrote so they can verify it.
Skills are permanent — they persist across all future sessions.
"""

_ONBOARDING = """
## Onboarding Protocol

If USER.md contains "Not set yet" for the user's name, you are meeting this person for the first time.

Introduce yourself warmly. Tell them you're a personal AI agent, that you don't have a name yet either, and that you'd like to learn about them so you can serve them better. Then ask:
1. What their name is and how they'd like to be addressed
2. Their timezone
3. What they'd like help with (goals, projects, recurring tasks)
4. Any preferences (communication style, things to avoid, etc.)
5. What name they'd like to give you

Once you have enough to go on, use file_write to update:
- USER.md — their name, how to address them, timezone, preferences, goals
- IDENTITY.md — your new name, personality notes, their user info
- MEMORY.md — add a "First session" note with the date and key facts

You don't have to ask all questions at once. Have a natural conversation. But do write what you learn before the session ends.

After onboarding is complete, you are no longer a blank slate. You have an identity and a user. Act like it.
"""


def _system_prompt() -> str:
    parts = [f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"]

    for fname in ("SOUL.md", "IDENTITY.md", "USER.md", "MEMORY.md"):
        p = Path(fname)
        if p.exists():
            parts.append(f"=== {fname} ===\n{p.read_text().strip()}")

    if skills := load_skills():
        parts.append(f"=== AVAILABLE SKILLS ===\n{skills}")

    parts.append(_CLI_PROTOCOL)
    parts.append(_TOOLS_GUIDE)
    parts.append(_SKILLS_GUIDE)

    # Inject onboarding instructions if user is not yet known
    user_md = Path("USER.md").read_text() if Path("USER.md").exists() else ""
    if "Not set yet" in user_md:
        parts.append(_ONBOARDING)

    return "\n\n".join(parts)


def run(chat_id: str, user_message: str) -> str:
    # Merge built-in + user-defined tools on every call
    custom_schemas, custom_map = load_custom_tools()
    tools = TOOLS + custom_schemas
    tool_map = {**TOOL_MAP, **custom_map}

    history = history_load(chat_id)
    messages = [
        {"role": "system", "content": _system_prompt()},
        *history,
        {"role": "user", "content": user_message},
    ]
    history_append(chat_id, "user", user_message)

    for _ in range(MAX_STEPS):
        response = litellm.completion(model=MODEL, messages=messages, tools=tools, tool_choice="auto")
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
            result = tool_map.get(tc.function.name, lambda **_: "Unknown tool")(**args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})

    return "Max steps reached."
