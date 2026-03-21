from __future__ import annotations

import asyncio
import time
import uuid

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from . import workspace
from .config import Config


async def _run_subagent_task(task_info: dict, opts: ClaudeAgentOptions) -> None:
    from .tools import _send_telegram
    task_id = task_info["id"]
    try:
        parts = []
        async with asyncio.timeout(task_info["timeout"]):
            async for msg in query(prompt=task_info["prompt"], options=opts):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
        result = "\n".join(parts) or "(no output)"
        task_info["registry"][task_id]["status"] = "done"
    except TimeoutError:
        result = f"Task {task_id} timed out."
        task_info["registry"][task_id]["status"] = "timed_out"
    except Exception as e:
        result = f"Task {task_id} failed: {e}"
        task_info["registry"][task_id]["status"] = "failed"
    await asyncio.to_thread(_send_telegram, task_info["chat_id"], result)


def _make_spawn_task_tool(chat_id: str, cfg: Config, task_registry: dict):
    from .tools import _send_telegram

    subagent_timeout = cfg.get("subagent_timeout")
    subagent_max_turns = cfg.get("subagent_max_turns")

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
        opts = ClaudeAgentOptions(
            model=cfg.get("model"),
            allowed_tools=["Bash", "Read", "Write", "WebSearch", "WebFetch", "mcp__smolclaw__telegram_send"],
            mcp_servers={"smolclaw": subagent_mcp},
            permission_mode="acceptEdits",
            max_turns=subagent_max_turns,
            cwd=str(workspace.HOME),
        )
        info = {"id": task_id, "prompt": args["task"], "timeout": subagent_timeout,
                "registry": task_registry, "chat_id": chat_id}
        task = asyncio.create_task(_run_subagent_task(info, opts))
        task_registry[task_id] = {
            "task": task,
            "description": args["task"][:80],
            "started_at": time.time(),
            "status": "running",
            "chat_id": chat_id,
        }
        return {"content": [{"type": "text", "text": f"Task started (ID: {task_id}). I'll message you when it's done."}]}

    return spawn_task


def _make_delegate_tool(chat_id: str, cfg: Config):
    subagent_timeout = cfg.get("subagent_timeout")
    subagent_max_turns = cfg.get("subagent_max_turns")

    @tool(
        "delegate",
        (
            "Run a sub-agent synchronously and return its result. "
            "The sub-agent uses a faster, cheaper model (Sonnet) with full tool access. "
            "Use for tasks that need multiple tool calls: research, file operations, "
            "web searches, code changes. You get the result back and can reason about it."
        ),
        {"task": str},
    )
    async def delegate(args: dict) -> dict:
        opts = ClaudeAgentOptions(
            model="claude-sonnet-4-6",
            allowed_tools=["Bash", "Read", "Write", "WebSearch", "WebFetch"],
            permission_mode="acceptEdits",
            max_turns=subagent_max_turns,
            cwd=str(workspace.HOME),
        )
        parts: list[str] = []
        try:
            async with asyncio.timeout(subagent_timeout):
                async for msg in query(prompt=args["task"], options=opts):
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                parts.append(block.text)
        except TimeoutError:
            parts.append(f"\n[delegate timed out after {subagent_timeout}s]")
        except Exception as e:
            parts.append(f"\n[delegate error: {e}]")
        result = "\n".join(parts) or "(no output)"
        if len(result) > 12000:
            result = result[:12000] + "\n\n[truncated — full output was longer]"
        return {"content": [{"type": "text", "text": result}]}

    return delegate
