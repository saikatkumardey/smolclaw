"""claude-agent-sdk ClaudeSDKClient loop."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    query,
    create_sdk_mcp_server,
    tool,
)

from .skills import load_skills
from .tool_loader import load_custom_tools
from .tools_sdk import CUSTOM_TOOLS
from . import workspace
from .handover import load as handover_load, clear as handover_clear

from loguru import logger

MAX_TURNS = 10
DEFAULT_MODEL = "claude-sonnet-4-6"

# Available Claude models: (model_id, display_label)
AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("claude-opus-4-6",           "Opus 4.6 — Most capable"),
    ("claude-sonnet-4-6",         "Sonnet 4.6 — Balanced (default)"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 — Fastest"),
]


def get_current_model() -> str:
    return os.getenv("SMOLCLAW_MODEL", DEFAULT_MODEL)


async def set_model(model_id: str) -> None:
    """Persist the chosen model to .env and reset all sessions."""
    os.environ["SMOLCLAW_MODEL"] = model_id
    env_path = workspace.HOME / ".env"
    from .setup import _read_env, _write_env
    env = _read_env(env_path)
    env["SMOLCLAW_MODEL"] = model_id
    _write_env(env_path, env)
    for chat_id in list(_sessions.keys()):
        await reset_session(chat_id)


@dataclass
class _Session:
    client: ClaudeSDKClient
    dynamic_tool_names: frozenset[str] = field(default_factory=frozenset)


# One session per chat_id, cached in memory for multi-turn
_sessions: dict[str, _Session] = {}

SESSIONS_DIR = workspace.HOME / "sessions"


async def reset_session(chat_id: str) -> None:
    """Disconnect and remove the cached session for chat_id."""
    if session := _sessions.pop(chat_id, None):
        await session.client.disconnect()


def session_log(chat_id: str, role: str, content: str) -> None:
    """Append a line to today's session log. JSONL, one file per day."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = SESSIONS_DIR / f"{today}.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "role": role,
        "content": content,
    }
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


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

Once you have enough to go on, write what you've learned using the Write tool with these exact absolute paths:
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

    user_content = ""
    for path, name in (
        (workspace.SOUL,   "SOUL.md"),
        (workspace.USER,   "USER.md"),
        (workspace.MEMORY, "MEMORY.md"),
    ):
        content = workspace.read(path)
        if path == workspace.USER:
            user_content = content
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
    if "Not set yet" in user_content:
        parts.append(_onboarding_block())

    return "\n\n".join(parts)


def _make_spawn_task_tool(chat_id: str):
    """Build spawn_task as a fire-and-forget closure. Result delivered via telegram_send."""
    from .tools import _send_telegram

    @tool(
        "spawn_task",
        (
            "Run an isolated sub-agent task in the background. Returns immediately. "
            "Result is delivered to the user via Telegram when done. "
            "Use for any task requiring more than 3 tool calls."
        ),
        {"task": str},
    )
    async def spawn_task(args: dict) -> dict:
        timeout = int(os.getenv("SMOLCLAW_SUBAGENT_TIMEOUT", "120"))
        opts = ClaudeAgentOptions(
            allowed_tools=["Bash", "Read", "Write", "WebSearch", "WebFetch"],
            permission_mode="acceptEdits",
            max_turns=15,
        )

        async def _run() -> None:
            try:
                parts = []
                async with asyncio.timeout(timeout):
                    async for msg in query(prompt=args["task"], options=opts):
                        if isinstance(msg, AssistantMessage):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    parts.append(block.text)
                result = "\n".join(parts) or "(no output)"
            except asyncio.TimeoutError:
                result = "Task timed out."
            except Exception as e:
                result = f"Task failed: {e}"
            await asyncio.to_thread(_send_telegram, chat_id, result)

        asyncio.create_task(_run())
        return {"content": [{"type": "text", "text": "Task started in the background. I'll message you when it's done."}]}

    return spawn_task


def _make_options(chat_id: str, dynamic_mcp_server=None) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions with full tool set."""
    spawn_task = _make_spawn_task_tool(chat_id)
    smolclaw_tools = CUSTOM_TOOLS + [spawn_task]
    smolclaw_server = create_sdk_mcp_server(name="smolclaw", version="1.0.0", tools=smolclaw_tools)

    smolclaw_tool_names = [f"mcp__smolclaw__{t.name}" for t in smolclaw_tools]
    allowed = ["Bash", "Read", "Write", "WebSearch", "WebFetch"] + smolclaw_tool_names

    mcp_servers = {"smolclaw": smolclaw_server}

    if dynamic_mcp_server is not None:
        mcp_servers["dynamic"] = dynamic_mcp_server
        # Dynamic tool names can't be enumerated here without loading again;
        # the caller adds them to allowed_tools via the tool list.
        # We allow all mcp__dynamic__* by adding a wildcard entry.
        # Claude Code supports trailing-* wildcards in allowed_tools.
        allowed.append("mcp__dynamic__*")

    return ClaudeAgentOptions(
        model=get_current_model(),
        system_prompt=_system_prompt(),
        allowed_tools=allowed,
        mcp_servers=mcp_servers,
        permission_mode="acceptEdits",
        cwd=str(workspace.HOME),
        max_turns=MAX_TURNS,
    )


async def run(chat_id: str, user_message: str) -> str:
    """Run one turn of conversation. Multi-turn via cached client per chat_id."""
    # Load dynamic tools on every call (no restart needed when new tools added)
    dynamic_tools = load_custom_tools()
    current_tool_names = frozenset(t.name for t in dynamic_tools)

    # Build dynamic MCP server if any tools are present
    dynamic_mcp_server = None
    if dynamic_tools:
        dynamic_mcp_server = create_sdk_mcp_server(name="dynamic", version="1.0.0", tools=dynamic_tools)

    # Check if client needs creation or replacement
    existing = _sessions.get(chat_id)
    if existing is not None and existing.dynamic_tool_names != current_tool_names:
        logger.info("Dynamic tools changed for {}; resetting client", chat_id)
        await reset_session(chat_id)
        existing = None

    if existing is None:
        options = _make_options(chat_id, dynamic_mcp_server)
        client = ClaudeSDKClient(options=options)
        await client.connect()
        _sessions[chat_id] = _Session(client=client, dynamic_tool_names=current_tool_names)

    client = _sessions[chat_id].client

    # Prepend current time (keeps system prompt stable for caching)
    timestamped_message = f"[Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}]\n\n{user_message}"

    session_log(chat_id, "user", user_message)

    try:
        await client.query(timestamped_message)
        parts = []
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        reply = "\n".join(parts) or "(no response)"
    except Exception as e:
        logger.exception("Agent error: {}", e)
        reply = f"Error: {e}"

    session_log(chat_id, "assistant", reply)
    return reply
