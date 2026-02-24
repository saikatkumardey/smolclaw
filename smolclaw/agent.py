"""smolagents ToolCallingAgent loop."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from smolagents import ToolCallingAgent, LiteLLMModel
from smolagents import Tool

from .history import append as history_append
from .skills import load_skills
from .tool_loader import load_custom_tools
from .tools import TOOLS_LIST
from . import workspace
from .handover import load as handover_load, clear as handover_clear

logger = logging.getLogger("smolclaw.agent")
MODEL = os.getenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-6")
MAX_STEPS = 10

# One agent per chat_id, cached in memory for multi-turn
_agents: dict[str, ToolCallingAgent] = {}



def _onboarding_block() -> str:
    return f"""
## Onboarding Protocol

If USER.md contains "Not set yet" for the user's name, you are meeting this person for the first time.

Introduce yourself warmly. Tell them you're a personal AI agent, that you don't have a name yet either, and that you'd like to learn about them so you can serve them better. Then ask:
1. What their name is and how they'd like to be addressed
2. Their timezone
3. What they'd like help with (goals, projects, recurring tasks)
4. Any preferences (communication style, things to avoid, etc.)
5. What name they'd like to give you

Once you have enough to go on, write what you've learned using file_write with these exact absolute paths:
- {workspace.USER} — their name, how to address them, timezone, preferences, goals
- {workspace.SOUL} — update the Identity section with your new name and emoji
- {workspace.MEMORY} — add a "First session" note with the date and key facts

You don't have to ask all questions at once. Have a natural conversation. But do write what you learn before the session ends — use the absolute paths above, not relative filenames.

After onboarding is complete, you are no longer a blank slate. You have an identity and a user. Act like it.
"""


def _workspace_context() -> str:
    return (
        f"## Workspace\n"
        f"Your workspace directory: {workspace.HOME}\n"
        f"Always use these absolute paths when writing agent data files:\n"
        f"- SOUL.md:    {workspace.SOUL}  (identity + operating instructions)\n"
        f"- USER.md:    {workspace.USER}\n"
        f"- MEMORY.md:  {workspace.MEMORY}\n"
        f"- crons.yaml: {workspace.CRONS}\n"
        f"- skills/:    {workspace.SKILLS_DIR}/<name>/SKILL.md\n"
        f"- tools/:     {workspace.TOOLS_DIR}/<name>.py\n"
        f"Never use bare filenames like 'SOUL.md' — always the full path above."
    )


def _system_prompt() -> str:
    parts = [
        _workspace_context(),
    ]

    for path, name in (
        (workspace.SOUL,   "SOUL.md"),
        (workspace.USER,   "USER.md"),
        (workspace.MEMORY, "MEMORY.md"),
    ):
        content = workspace.read(path)
        if content:
            parts.append(f"=== {name} ===\n{content.strip()}")

    if skills := load_skills(workspace.SKILLS_DIR):
        parts.append(f"=== AVAILABLE SKILLS ===\n{skills}")

    # Inject handover note if one exists (cleared immediately after reading)
    if handover := handover_load():
        parts.append(
            f"=== HANDOVER NOTE (read-only context) ===\n"
            f"The following is history from the previous session. "
            f"Do NOT re-execute any actions described here. Only resume tasks listed under PENDING.\n\n"
            f"{handover.strip()}"
        )
        handover_clear()

    # Inject onboarding instructions if user is not yet known
    if "Not set yet" in workspace.read(workspace.USER):
        parts.append(_onboarding_block())

    return "\n\n".join(parts)


def _create_agent(dynamic_tools: list[Tool]) -> ToolCallingAgent:
    """Create a new ToolCallingAgent with the current system prompt and all tools."""
    model_kwargs: dict = {}
    if "anthropic" in MODEL or "claude" in MODEL:
        model_kwargs["extra_headers"] = {"anthropic-beta": "prompt-caching-2024-07-31"}
    try:
        model = LiteLLMModel(model_id=MODEL, **model_kwargs)
    except TypeError:
        # LiteLLMModel doesn't accept extra_headers — fall back gracefully
        model = LiteLLMModel(model_id=MODEL)

    tools = TOOLS_LIST + dynamic_tools
    system_prompt = _system_prompt()
    logger.info("System prompt: ~%d tokens", len(system_prompt) // 4)
    agent = ToolCallingAgent(
        tools=tools,
        model=model,
        system_prompt=system_prompt,
        max_steps=MAX_STEPS,
    )
    return agent


def run(chat_id: str, user_message: str) -> str:
    """Run one turn of conversation. Multi-turn via cached agent per chat_id."""
    # Load dynamic tools on every call (no restart needed when new tools added)
    dynamic_tools = load_custom_tools()

    # Get or create agent for this chat
    if chat_id not in _agents:
        _agents[chat_id] = _create_agent(dynamic_tools)
    else:
        # Refresh dynamic tools on existing agent
        agent = _agents[chat_id]
        # Update toolbox with any new dynamic tools
        for tool in dynamic_tools:
            if tool.name not in agent.tools:
                agent.tools[tool.name] = tool

    agent = _agents[chat_id]

    # Prepend current time to the user message (keeps system prompt stable for caching)
    timestamped_message = f"[Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}]\n\n{user_message}"

    # Audit log — write-only
    history_append(chat_id, "user", user_message)

    try:
        result = agent.run(timestamped_message, reset=False)
        reply = str(result)
    except Exception as e:
        logger.error("Agent error: %s", e, exc_info=True)
        reply = f"Error: {e}"

    history_append(chat_id, "assistant", reply)
    return reply
