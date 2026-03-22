from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)
from loguru import logger

from . import workspace
from .agent_tools import _make_delegate_tool, _make_spawn_task_tool
from .config import Config
from .handover import clear as handover_clear
from .handover import exists as handover_exists
from .handover import save as handover_save
from .handover_builder import build_auto_handover as _build_auto_handover
from .prompt_builder import build_system_prompt as _system_prompt
from .session_state import SessionState
from .tool_loader import load_custom_tools
from .tools_sdk import CUSTOM_TOOLS

_AUTO_ROTATE_THRESHOLD = 0.70
_CONTEXT_WINDOW_TOKENS = 1_000_000

_task_registry: dict[str, dict] = {}

AVAILABLE_MODELS: list[tuple[str, str]] = [
    ("claude-opus-4-6",           "Opus 4.6 — Most capable"),
    ("claude-sonnet-4-6",         "Sonnet 4.6 — Balanced (default)"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 — Fastest"),
]

AVAILABLE_EFFORTS: list[tuple[str, str]] = [
    ("low",    "Low — fast, minimal thinking (default)"),
    ("medium", "Medium — balanced thinking"),
    ("high",   "High — deeper reasoning"),
    ("max",    "Max — maximum thinking budget"),
]


def get_current_model() -> str:
    return Config.load().get("model")


async def set_model(model_id: str) -> None:
    cfg = Config.load()
    cfg.set("model", model_id)
    os.environ["SMOLCLAW_MODEL"] = model_id
    for chat_id in list(_sessions.keys()):
        await reset_session(chat_id)


def get_current_effort() -> str:
    return Config.load().get("effort")


async def set_effort(effort: str) -> None:
    cfg = Config.load()
    cfg.set("effort", effort)
    for chat_id in list(_sessions.keys()):
        await reset_session(chat_id)


def get_streaming() -> bool:
    return Config.load().get("streaming")


async def set_streaming(enabled: bool) -> None:
    Config.load().set("streaming", enabled)


@dataclass
class _Session:
    client: ClaudeSDKClient
    dynamic_tool_names: frozenset[str] = field(default_factory=frozenset)
    last_result: ResultMessage | None = None
    handover_pending: bool = False


_sessions: dict[str, _Session] = {}
_session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _prune_stale_locks() -> None:
    stale = [cid for cid in _session_locks if cid not in _sessions]
    for cid in stale:
        lock = _session_locks[cid]
        if not lock.locked():
            del _session_locks[cid]


def _force_terminate_transport(transport) -> None:
    proc = getattr(transport, '_process', None) if transport is not None else None
    if proc is None:
        return
    try:
        proc.terminate()
        logger.info("Force-terminated subprocess")
    except Exception as kill_e:
        logger.debug("Could not terminate subprocess: {}", kill_e)


async def reset_session(chat_id: str) -> None:
    session = _sessions.pop(chat_id, None)
    if session:
        transport = getattr(session.client, '_transport', None)
        # ClaudeSDKClient.disconnect() calls query.close() which exits an anyio TaskGroup
        # cancel scope. That scope is bound to the asyncio task that called connect(), so
        # calling disconnect() cross-task raises "Attempted to exit cancel scope in a
        # different task than it was entered in" (documented SDK limitation, v0.0.20).
        # Fix: cancel the scope directly (safe cross-task — just sets a flag) and
        # force-terminate the subprocess, bypassing the broken tg.__aexit__() path.
        query = getattr(session.client, '_query', None)
        if query is not None:
            tg = getattr(query, '_tg', None)
            if tg is not None:
                tg.cancel_scope.cancel()
            query._closed = True
        _force_terminate_transport(transport)
    try:
        from .browser import BrowserManager
        await BrowserManager.get().close_session(chat_id)
    except Exception as e:
        logger.warning("Failed to close browser session for {}: {}", chat_id, e)


async def interrupt_session(chat_id: str) -> bool:
    if session := _sessions.get(chat_id):
        try:
            await session.client.interrupt()
            return True
        except Exception:
            logger.debug("failed to interrupt session %s", chat_id, exc_info=True)
    return False


def get_last_result(chat_id: str) -> ResultMessage | None:
    if session := _sessions.get(chat_id):
        return session.last_result
    return None


_TASK_EXPIRY_SECONDS = 3600
_TASK_STUCK_SECONDS = 7200


def list_tasks() -> list[dict]:
    now = time.time()
    stale = [
        tid for tid, info in _task_registry.items()
        if (info["task"].done() and (now - info["started_at"]) > _TASK_EXPIRY_SECONDS)
        or (not info["task"].done() and (now - info["started_at"]) > _TASK_STUCK_SECONDS)
    ]
    for tid in stale:
        info = _task_registry.pop(tid)
        if not info["task"].done():
            logger.warning("Pruning stuck task {} ({})", tid, info["description"])
            info["task"].cancel()

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


def cancel_all_tasks(chat_id: str) -> int:
    cancelled = 0
    to_remove = []
    for tid, info in _task_registry.items():
        if info.get("chat_id") == chat_id and not info["task"].done():
            info["task"].cancel()
            info["status"] = "cancelled"
            cancelled += 1
            to_remove.append(tid)
    for tid in to_remove:
        _task_registry.pop(tid, None)
    return cancelled


def session_log(chat_id: str, role: str, content: str | dict) -> None:
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        path = workspace.HOME / "sessions" / f"{today}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": now.isoformat(),
            "chat_id": chat_id,
            "role": role,
            "content": content,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("session_log failed: %s", e)


def _select_tools_for_chat(chat_id: str, cfg: Config) -> list:
    if chat_id.startswith("cron:subconscious"):
        from .tools_sdk import reflect, telegram_send, update_subconscious
        return [telegram_send, update_subconscious, reflect]
    if chat_id.startswith("cron:"):
        return [*CUSTOM_TOOLS]
    spawn_task = _make_spawn_task_tool(chat_id, cfg, _task_registry)
    delegate = _make_delegate_tool(chat_id, cfg)
    return [*CUSTOM_TOOLS, spawn_task, delegate]


def _select_model(chat_id: str, cfg: Config) -> str:
    model = cfg.get("model")
    if chat_id.startswith("cron:subconscious"):
        return cfg.get("subconscious_model") or model
    if chat_id.startswith("cron:"):
        return cfg.get("cron_model") or model
    return model


def _select_max_turns(chat_id: str, cfg: Config) -> int:
    if chat_id.startswith("cron:subconscious"):
        return 3
    return cfg.get("max_turns")


def _make_options(chat_id: str, dynamic_mcp_server=None) -> ClaudeAgentOptions:
    cfg = Config.load()
    is_cron = chat_id.startswith("cron:")

    smolclaw_tools = _select_tools_for_chat(chat_id, cfg)
    smolclaw_server = create_sdk_mcp_server(name="smolclaw", version="1.0.0", tools=smolclaw_tools)

    smolclaw_tool_names = [f"mcp__smolclaw__{t.name}" for t in smolclaw_tools]
    allowed = ["Bash", "Read", "Write", "WebSearch", "WebFetch", *smolclaw_tool_names]

    mcp_servers = {"smolclaw": smolclaw_server}
    if dynamic_mcp_server is not None and not chat_id.startswith("cron:subconscious"):
        mcp_servers["dynamic"] = dynamic_mcp_server
        allowed.append("mcp__dynamic__*")

    return ClaudeAgentOptions(
        model=_select_model(chat_id, cfg),
        system_prompt=_system_prompt(slim=is_cron),
        allowed_tools=allowed,
        mcp_servers=mcp_servers,
        permission_mode="acceptEdits",
        cwd=str(workspace.HOME),
        max_turns=_select_max_turns(chat_id, cfg),
        effort=cfg.get("effort"),
        include_partial_messages=True,
    )


async def _ensure_session(
    chat_id: str, current_tool_names: frozenset[str], dynamic_mcp_server,
) -> None:
    existing = _sessions.get(chat_id)

    # Cron jobs get a fresh event loop per call, so cached sessions have dead transports
    if existing is not None and chat_id.startswith("cron:"):
        _sessions.pop(chat_id, None)
        existing = None

    if existing is not None and existing.dynamic_tool_names != current_tool_names:
        logger.info("Dynamic tools changed for {}; resetting client", chat_id)
        await reset_session(chat_id)
        existing = None

    if existing is None:
        options = _make_options(chat_id, dynamic_mcp_server)
        client = ClaudeSDKClient(options=options)
        await client.connect()
        _sessions[chat_id] = _Session(
            client=client,
            dynamic_tool_names=current_tool_names,
            handover_pending=handover_exists(),
        )


def _collect_assistant_parts(msg: AssistantMessage, parts: list[str], tool_names: list[str]) -> None:
    for block in msg.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ToolUseBlock):
            tool_names.append(block.name)


def _format_result(parts: list[str], tool_names: list[str]) -> str:
    if parts:
        return "\n".join(parts)
    if tool_names:
        return f"Done. (used: {', '.join(dict.fromkeys(tool_names))})"
    return "(no response)"


def _extract_stream_delta(msg: StreamEvent) -> str | None:
    if msg.parent_tool_use_id is not None:
        return None
    event = msg.event
    if event.get("type") != "content_block_delta":
        return None
    delta = event.get("delta", {})
    if delta.get("type") != "text_delta":
        return None
    return delta.get("text", "") or None


async def _execute_turn(chat_id: str, timestamped_message: str) -> str:
    session = _sessions[chat_id]
    await session.client.query(timestamped_message)
    parts: list[str] = []
    tool_names: list[str] = []
    async for msg in session.client.receive_response():
        if isinstance(msg, AssistantMessage):
            _collect_assistant_parts(msg, parts, tool_names)
        elif isinstance(msg, ResultMessage):
            session.last_result = msg
            _log_result(chat_id, msg)
    return _format_result(parts, tool_names)


async def _execute_turn_streaming(chat_id: str, timestamped_message: str):
    session = _sessions[chat_id]
    await session.client.query(timestamped_message)
    parts: list[str] = []
    tool_names: list[str] = []
    async for msg in session.client.receive_response():
        if isinstance(msg, StreamEvent):
            text = _extract_stream_delta(msg)
            if text:
                yield ("text_delta", text)
        elif isinstance(msg, AssistantMessage):
            _collect_assistant_parts(msg, parts, tool_names)
        elif isinstance(msg, ResultMessage):
            session.last_result = msg
            _log_result(chat_id, msg)
    yield ("done", _format_result(parts, tool_names))


def _log_result(chat_id: str, msg: ResultMessage) -> None:
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


async def _maybe_auto_rotate(chat_id: str) -> None:
    session = _sessions.get(chat_id)
    if not (session and session.last_result):
        return
    usage = session.last_result.usage or {}
    used = usage.get("cache_read_input_tokens", 0) + usage.get("input_tokens", 0)
    fill = used / _CONTEXT_WINDOW_TOKENS
    if fill >= _AUTO_ROTATE_THRESHOLD:
        logger.info("Auto-rotating session {} (context at {:.0%})", chat_id, fill)
        handover_text = _build_auto_handover(chat_id)
        if handover_text:
            handover_save(handover_text)
        await reset_session(chat_id)


def _prepare_dynamic_tools() -> tuple[frozenset[str], object]:
    dynamic_tools = load_custom_tools()
    names = frozenset(t.name for t in dynamic_tools)
    server = (
        create_sdk_mcp_server(name="dynamic", version="1.0.0", tools=dynamic_tools)
        if dynamic_tools else None
    )
    return names, server


def _timestamp_message(user_message: str) -> str:
    return f"[Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]\n\n{user_message}"


async def _finalize_turn(chat_id: str, reply: str) -> None:
    """Clear handover, log reply, auto-rotate, and clean up locks."""
    session = _sessions.get(chat_id)
    if session and session.handover_pending:
        handover_clear()
        session.handover_pending = False
    session_log(chat_id, "assistant", reply)
    try:
        await _maybe_auto_rotate(chat_id)
    except Exception as e:
        logger.warning("Auto-rotation failed for {}: {} — forcing session removal", chat_id, e)
        _sessions.pop(chat_id, None)


def _cleanup_locks(chat_id: str) -> None:
    if chat_id.startswith("cron:"):
        _session_locks.pop(chat_id, None)
    else:
        _prune_stale_locks()


async def run_streaming(chat_id: str, user_message: str):
    tool_names, mcp_server = _prepare_dynamic_tools()
    lock = _session_locks[chat_id]
    async with lock:
        await _ensure_session(chat_id, tool_names, mcp_server)
        timestamped = _timestamp_message(user_message)
        session_log(chat_id, "user", user_message)

        reply = "(no response)"
        try:
            async for event_type, data in _execute_turn_streaming(chat_id, timestamped):
                if event_type == "done":
                    reply = data
                else:
                    yield (event_type, data)
        except Exception as e:
            logger.exception("Agent error for {}: {}: {}", chat_id, type(e).__name__, e)
            reply = f"Something went wrong ({type(e).__name__}). Please try again."

        await _finalize_turn(chat_id, reply)
        yield ("done", reply)

    _cleanup_locks(chat_id)


async def run(chat_id: str, user_message: str) -> str:
    tool_names, mcp_server = _prepare_dynamic_tools()
    lock = _session_locks[chat_id]
    async with lock:
        await _ensure_session(chat_id, tool_names, mcp_server)
        timestamped = _timestamp_message(user_message)
        session_log(chat_id, "user", user_message)

        try:
            reply = await _execute_turn(chat_id, timestamped)
        except Exception as e:
            logger.exception("Agent error for {}: {}: {}", chat_id, type(e).__name__, e)
            reply = f"Something went wrong ({type(e).__name__}). Please try again."

        await _finalize_turn(chat_id, reply)

    _cleanup_locks(chat_id)
    return reply
