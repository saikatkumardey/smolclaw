"""claude-agent-sdk ClaudeSDKClient loop."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from loguru import logger

from . import workspace
from .config import Config
from .handover import clear as handover_clear
from .handover import exists as handover_exists
from .handover import load as handover_load
from .handover import save as handover_save
from .session_state import SessionState
from .skills import list_skills
from .tool_loader import load_custom_tools
from .tools_sdk import CUSTOM_TOOLS

# Auto-rotation: when context exceeds this fraction, build a handover and reset
_AUTO_ROTATE_THRESHOLD = 0.70
_CONTEXT_WINDOW_TOKENS = 200_000

# Task registry: task_id -> {task, description, started_at, status}
_task_registry: dict[str, dict] = {}

# Available Claude models: (model_id, display_label)
AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("claude-opus-4-6",           "Opus 4.6 — Most capable"),
    ("claude-sonnet-4-6",         "Sonnet 4.6 — Balanced (default)"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 — Fastest"),
]

# Available effort levels: (effort_id, display_label)
AVAILABLE_EFFORTS: list[tuple[str, str]] = [
    ("low",    "Low — fast, minimal thinking (default)"),
    ("medium", "Medium — balanced thinking"),
    ("high",   "High — deeper reasoning"),
    ("max",    "Max — maximum thinking budget"),
]


def get_current_model() -> str:
    return Config.load().get("model")


async def set_model(model_id: str) -> None:
    """Persist the chosen model to smolclaw.json and reset all sessions."""
    cfg = Config.load()
    cfg.set("model", model_id)
    # Keep env var in sync for scheduler and other env-var readers
    os.environ["SMOLCLAW_MODEL"] = model_id
    for chat_id in list(_sessions.keys()):
        await reset_session(chat_id)


def get_current_effort() -> str:
    return Config.load().get("effort")


async def set_effort(effort: str) -> None:
    """Persist the chosen effort level to smolclaw.json and reset all sessions."""
    cfg = Config.load()
    cfg.set("effort", effort)
    for chat_id in list(_sessions.keys()):
        await reset_session(chat_id)


@dataclass
class _Session:
    client: ClaudeSDKClient
    dynamic_tool_names: frozenset[str] = field(default_factory=frozenset)
    last_result: ResultMessage | None = None
    handover_pending: bool = False


# One session per chat_id, cached in memory for multi-turn
_sessions: dict[str, _Session] = {}
_session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _prune_stale_locks() -> None:
    """Remove locks for chat_ids that have no active session."""
    stale = [cid for cid in _session_locks if cid not in _sessions]
    for cid in stale:
        lock = _session_locks[cid]
        if not lock.locked():
            del _session_locks[cid]


async def reset_session(chat_id: str) -> None:
    """Disconnect and remove the cached session for chat_id."""
    session = _sessions.pop(chat_id, None)
    if session:
        try:
            await session.client.disconnect()
        except Exception as e:
            logger.warning("Failed to disconnect session for {}: {}", chat_id, e)
    # Always clean up browser context, even if disconnect failed
    try:
        from .browser import BrowserManager
        await BrowserManager.get().close_session(chat_id)
    except Exception as e:
        logger.warning("Failed to close browser session for {}: {}", chat_id, e)


async def interrupt_session(chat_id: str) -> bool:
    """Interrupt the active turn for chat_id. Returns True if signal was sent."""
    if session := _sessions.get(chat_id):
        try:
            await session.client.interrupt()
            return True
        except Exception:
            pass
    return False


def get_last_result(chat_id: str) -> ResultMessage | None:
    """Return the ResultMessage from the most recent turn, if any."""
    if session := _sessions.get(chat_id):
        return session.last_result
    return None



_TASK_EXPIRY_SECONDS = 3600  # prune completed tasks after 1 hour


def list_tasks() -> list[dict]:
    """Return summary of all tracked background tasks, pruning stale completed ones."""
    now = time.time()
    # Prune completed tasks older than 1 hour
    stale = [
        tid for tid, info in _task_registry.items()
        if info["task"].done() and (now - info["started_at"]) > _TASK_EXPIRY_SECONDS
    ]
    for tid in stale:
        del _task_registry[tid]

    rows = []
    for tid, info in _task_registry.items():
        elapsed = int(now - info["started_at"])
        rows.append({
            "id": tid,
            "status": "running" if not info["task"].done() else info.get("status", "done"),
            "description": info["description"][:60],
            "elapsed_s": elapsed,
        })
    return rows


def session_log(chat_id: str, role: str, content: str | dict) -> None:
    """Append a line to today's session log. JSONL, one file per day."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = workspace.HOME / "sessions" / f"{today}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "chat_id": chat_id,
            "role": role,
            "content": content,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("session_log failed: %s", e)


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
        f"- SOUL.md:    {workspace.SOUL}  (identity + personality)\n"
        f"- AGENT.md:   {workspace.AGENT}  (operational playbook)\n"
        f"- USER.md:    {workspace.USER}\n"
        f"- MEMORY.md:  {workspace.MEMORY}\n"
        f"- crons.yaml: {workspace.CRONS}\n"
        f"- skills/:    {workspace.SKILLS_DIR}/<name>/SKILL.md\n"
        f"- tools/:     {workspace.TOOLS_DIR}/<name>.py\n"
        f"- Config:     {workspace.CONFIG}  (smolclaw.json — runtime settings)\n"
        f"- Session:    {workspace.SESSION_STATE}  (session_state.json — usage tracking)\n"
        f"Never use bare filenames like 'SOUL.md' — always the full path above."
    )


def _system_prompt(slim: bool = False) -> str:
    # Order is deliberately stable-first for prompt caching:
    # workspace context (static) → SOUL (rarely changes) → USER (rarely changes)
    # → skills (infrequently changes) → MEMORY (frequently changes)
    # → handover/onboarding (ephemeral, always last)
    #
    # slim=True: stripped-down prompt for cron jobs (no SOUL, USER, skills, handover)
    parts = [_workspace_context()]

    if slim:
        # Crons only need AGENT.md (operational rules) and MEMORY.md
        if agent_content := workspace.read(workspace.AGENT):
            parts.append(f"=== AGENT.md ===\n{agent_content.strip()}")
        if memory := workspace.read(workspace.MEMORY):
            parts.append(f"=== MEMORY.md ===\n{memory.strip()}")
        return "\n\n".join(parts)

    user_content = ""
    for path, name in (
        (workspace.SOUL, "SOUL.md"),
        (workspace.AGENT, "AGENT.md"),
        (workspace.USER, "USER.md"),
    ):
        content = workspace.read(path)
        if path == workspace.USER:
            user_content = content
        if content:
            parts.append(f"=== {name} ===\n{content.strip()}")

    if skills := list_skills(workspace.SKILLS_DIR):
        parts.append(
            f"=== AVAILABLE SKILLS ===\n"
            f"Use the read_skill tool to load a skill's instructions on demand.\n"
            f"Skills: {', '.join(skills)}"
        )

    if memory := workspace.read(workspace.MEMORY):
        parts.append(f"=== MEMORY.md ===\n{memory.strip()}")

    # Inject handover note if one exists (capped at 4000 chars to prevent token waste)
    if handover := handover_load():
        handover_text = handover.strip()[:4000]
        parts.append(
            f"=== HANDOVER NOTE (read-only context) ===\n"
            f"The following is history from the previous session. "
            f"Do NOT re-execute any actions described here. Only resume tasks listed under PENDING.\n\n"
            f"{handover_text}"
        )

    # Inject onboarding instructions if user is not yet known
    if "Not set yet" in user_content:
        parts.append(_onboarding_block())

    return "\n\n".join(parts)


def _context_fill_from_result(result: ResultMessage | None) -> float:
    """Return context fill fraction (0.0-1.0) from a ResultMessage."""
    if not result:
        return 0.0
    usage = result.usage or {}
    used = usage.get("cache_read_input_tokens", 0) + usage.get("input_tokens", 0)
    return used / _CONTEXT_WINDOW_TOKENS


def _build_auto_handover(chat_id: str) -> str:
    """Build a handover summary from recent session log entries for this chat_id."""
    sessions_dir = workspace.HOME / "sessions"
    if not sessions_dir.exists():
        return ""

    # Collect recent messages for this chat from the last 2 days of logs
    files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:2]
    messages: list[dict] = []
    for f in files:
        try:
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("chat_id") != chat_id:
                    continue
                if entry.get("role") in ("user", "assistant"):
                    content = entry.get("content", "")
                    if isinstance(content, str) and content.strip():
                        messages.append(entry)
        except Exception:
            continue

    if not messages:
        return ""

    # Take the last 10 exchanges (enough for context, fits in 4000 chars)
    recent = messages[-10:]
    parts = ["CONTEXT (recent conversation):"]
    for msg in recent:
        role = msg["role"]
        content = msg["content"][:300]
        ts = msg.get("ts", "")[:16]
        parts.append(f"[{ts}] {role}: {content}")

    parts.append("\nPENDING: none (auto-rotated due to context pressure)")
    return "\n".join(parts)


def _make_spawn_task_tool(chat_id: str, cfg: Config):
    """Build spawn_task with task registry and progress-capable sub-agents."""
    import time

    from .tools import _send_telegram

    subagent_timeout = cfg.get("subagent_timeout")
    subagent_max_turns = cfg.get("subagent_max_turns")

    # Build a minimal telegram_send tool for sub-agents so they can report progress
    @tool("telegram_send", "Send a Telegram message to report progress or results.", {"message": str})
    async def _subagent_telegram_send(args: dict) -> dict:
        await asyncio.to_thread(_send_telegram, chat_id, args["message"])
        return {"content": [{"type": "text", "text": "Sent."}]}

    subagent_mcp = create_sdk_mcp_server(
        name="smolclaw", version="1.0.0", tools=[_subagent_telegram_send]
    )

    @tool(
        "spawn_task",
        (
            "Run an isolated sub-agent task in the background. Returns a task ID immediately. "
            "Result is delivered to the user via Telegram when done. "
            "The sub-agent has access to telegram_send to report progress mid-task. "
            "Use for any task requiring more than 3 tool calls."
        ),
        {"task": str},
    )
    async def spawn_task(args: dict) -> dict:
        task_id = uuid.uuid4().hex[:8]
        description = args["task"][:80]

        opts = ClaudeAgentOptions(
            model=cfg.get("model"),
            allowed_tools=["Bash", "Read", "Write", "WebSearch", "WebFetch", "mcp__smolclaw__telegram_send"],
            mcp_servers={"smolclaw": subagent_mcp},
            permission_mode="acceptEdits",
            max_turns=subagent_max_turns,
            cwd=str(workspace.HOME),
        )

        async def _run() -> None:
            try:
                parts = []
                async with asyncio.timeout(subagent_timeout):
                    async for msg in query(prompt=args["task"], options=opts):
                        if isinstance(msg, AssistantMessage):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    parts.append(block.text)
                result = "\n".join(parts) or "(no output)"
                _task_registry[task_id]["status"] = "done"
            except TimeoutError:
                result = f"Task {task_id} timed out."
                _task_registry[task_id]["status"] = "timed_out"
            except Exception as e:
                result = f"Task {task_id} failed: {e}"
                _task_registry[task_id]["status"] = "failed"
            await asyncio.to_thread(_send_telegram, chat_id, result)

        task = asyncio.create_task(_run())
        _task_registry[task_id] = {
            "task": task,
            "description": description,
            "started_at": time.time(),
            "status": "running",
        }
        return {"content": [{"type": "text", "text": f"Task started (ID: {task_id}). I'll message you when it's done."}]}

    return spawn_task


def _make_options(chat_id: str, dynamic_mcp_server=None) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions with full tool set."""
    cfg = Config.load()
    spawn_task = _make_spawn_task_tool(chat_id, cfg)
    smolclaw_tools = [*CUSTOM_TOOLS, spawn_task]
    smolclaw_server = create_sdk_mcp_server(name="smolclaw", version="1.0.0", tools=smolclaw_tools)

    smolclaw_tool_names = [f"mcp__smolclaw__{t.name}" for t in smolclaw_tools]
    allowed = ["Bash", "Read", "Write", "WebSearch", "WebFetch", *smolclaw_tool_names]

    mcp_servers = {"smolclaw": smolclaw_server}

    if dynamic_mcp_server is not None:
        mcp_servers["dynamic"] = dynamic_mcp_server
        # Dynamic tool names can't be enumerated here without loading again;
        # the caller adds them to allowed_tools via the tool list.
        # We allow all mcp__dynamic__* by adding a wildcard entry.
        # Claude Code supports trailing-* wildcards in allowed_tools.
        allowed.append("mcp__dynamic__*")

    # Cron jobs use a cheaper model and slimmer system prompt
    is_cron = chat_id.startswith("cron:")
    model = cfg.get("model")
    if is_cron:
        model = cfg.get("cron_model") or model
    if chat_id.startswith("cron:subconscious"):
        model = cfg.get("subconscious_model") or model

    return ClaudeAgentOptions(
        model=model,
        system_prompt=_system_prompt(slim=is_cron),
        allowed_tools=allowed,
        mcp_servers=mcp_servers,
        permission_mode="acceptEdits",
        cwd=str(workspace.HOME),
        max_turns=cfg.get("max_turns"),
        effort=cfg.get("effort"),
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

    lock = _session_locks[chat_id]

    async with lock:
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
            has_handover = handover_exists()
            _sessions[chat_id] = _Session(
                client=client,
                dynamic_tool_names=current_tool_names,
                handover_pending=has_handover,
            )

    client = _sessions[chat_id].client

    # Prepend current time (keeps system prompt stable for caching)
    timestamped_message = f"[Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]\n\n{user_message}"

    session_log(chat_id, "user", user_message)

    try:
        await client.query(timestamped_message)
        parts: list[str] = []
        tool_names: list[str] = []
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_names.append(block.name)
            elif isinstance(msg, ResultMessage):
                _sessions[chat_id].last_result = msg
                usage = msg.usage or {}
                session_log(chat_id, "result", {
                    "turns": msg.num_turns,
                    "duration_ms": msg.duration_ms,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                    "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
                })
                try:
                    SessionState.load().record_turn(chat_id, msg)
                except Exception as e:
                    logger.warning("SessionState.record_turn failed: {}", e)
        if parts:
            reply = "\n".join(parts)
        elif tool_names:
            reply = f"Done. (used: {', '.join(dict.fromkeys(tool_names))})"
        else:
            reply = "(no response)"
    except Exception as e:
        logger.exception("Agent error for {}: {}: {}", chat_id, type(e).__name__, e)
        reply = f"Something went wrong ({type(e).__name__}). Please try again."
    finally:
        # Always clear handover after first turn — even on error, the handover
        # has been injected into the system prompt and shouldn't persist.
        session = _sessions.get(chat_id)
        if session and session.handover_pending:
            handover_clear()
            session.handover_pending = False

    session_log(chat_id, "assistant", reply)

    # Auto-rotate: if context is filling up, save handover and reset for next message
    # Must hold the lock to prevent concurrent messages from racing with rotation.
    try:
        async with _session_locks[chat_id]:
            session = _sessions.get(chat_id)
            if session and session.last_result:
                fill = _context_fill_from_result(session.last_result)
                if fill >= _AUTO_ROTATE_THRESHOLD:
                    logger.info("Auto-rotating session {} (context at {:.0%})", chat_id, fill)
                    handover_text = _build_auto_handover(chat_id)
                    if handover_text:
                        handover_save(handover_text)
                    await reset_session(chat_id)
    except Exception as e:
        logger.warning("Auto-rotation failed for {}: {} — forcing session removal", chat_id, e)
        _sessions.pop(chat_id, None)

    _prune_stale_locks()
    return reply
