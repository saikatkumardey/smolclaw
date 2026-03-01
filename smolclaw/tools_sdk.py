"""Custom tools as claude-agent-sdk @tool-decorated async functions."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from claude_agent_sdk import tool

from .tools import _send_telegram, _send_telegram_file
from . import workspace

_ALLOWED_SOURCE_PREFIX = "git+https://github.com/saikatkumardey/smolclaw"


def _is_allowed_chat(chat_id: str) -> bool:
    allowed = set(filter(None, os.getenv("ALLOWED_USER_IDS", "").split(",")))
    return chat_id.strip() in allowed


@tool("telegram_send", "Send a Telegram message to a chat_id. For cron delivery.", {"chat_id": str, "message": str})
async def telegram_send(args: dict) -> dict:
    if not _is_allowed_chat(args["chat_id"]):
        return {"content": [{"type": "text", "text": f"Error: chat_id {args['chat_id']!r} is not in ALLOWED_USER_IDS."}]}
    text = await asyncio.to_thread(_send_telegram, args["chat_id"], args["message"])
    return {"content": [{"type": "text", "text": text}]}


@tool("save_handover", "Save a handover note so state survives restart or update. Call before self_restart or self_update.", {"summary": str})
async def save_handover(args: dict) -> dict:
    from .handover import save
    save(args["summary"])
    return {"content": [{"type": "text", "text": "Handover saved."}]}


def _default_chat_id() -> str:
    return os.getenv("ALLOWED_USER_IDS", "").split(",")[0].strip()


@tool("self_restart", "Restart the smolclaw process in-place. Always call save_handover first.", {})
async def self_restart(args: dict) -> dict:
    if chat_id := _default_chat_id():
        await asyncio.to_thread(_send_telegram, chat_id, "Restarting…")
    exe = shutil.which("smolclaw") or sys.argv[0]
    argv = [exe] + sys.argv[1:] if sys.argv[1:] else [exe, "start"]
    os.execv(exe, argv)
    return {"content": [{"type": "text", "text": "unreachable"}]}


@tool("self_update", "Pull latest smolclaw from GitHub, reinstall, and restart. Always call save_handover first.", {})
async def self_update(args: dict) -> dict:
    source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")
    if not source.startswith(_ALLOWED_SOURCE_PREFIX):
        return {"content": [{"type": "text", "text": f"Error: SMOLCLAW_SOURCE {source!r} is not an allowed update URL."}]}
    result = subprocess.run(
        ["uv", "tool", "install", "--upgrade", source],
        capture_output=True, text=True, timeout=120,
    )
    chat_id = _default_chat_id()
    if result.returncode != 0:
        msg = f"Update failed:\n{result.stderr}"
        if chat_id:
            await asyncio.to_thread(_send_telegram, chat_id, msg)
        return {"content": [{"type": "text", "text": msg}]}
    if chat_id:
        await asyncio.to_thread(_send_telegram, chat_id, "✓ Update successful. Restarting…")
    exe = shutil.which("smolclaw") or sys.argv[0]
    os.execv(exe, [exe, "start"])
    return {"content": [{"type": "text", "text": "unreachable"}]}


@tool("telegram_send_file", "Send a local file (markdown, CSV, script, image, etc.) to a Telegram chat_id.", {"chat_id": str, "file_path": str})
async def telegram_send_file(args: dict) -> dict:
    if not _is_allowed_chat(args["chat_id"]):
        return {"content": [{"type": "text", "text": f"Error: chat_id {args['chat_id']!r} is not in ALLOWED_USER_IDS."}]}
    resolved = Path(args["file_path"]).resolve()
    if not str(resolved).startswith(str(workspace.HOME.resolve())):
        return {"content": [{"type": "text", "text": f"Error: file path {args['file_path']!r} is outside the workspace."}]}
    text = await asyncio.to_thread(_send_telegram_file, args["chat_id"], args["file_path"])
    return {"content": [{"type": "text", "text": text}]}


CUSTOM_TOOLS = [telegram_send, telegram_send_file, save_handover, self_restart, self_update]
