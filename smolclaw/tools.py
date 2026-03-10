"""Minimal tools module — shared Telegram helper + TelegramSender for scheduler use."""
from __future__ import annotations

import os
from pathlib import Path

import httpx

from . import workspace

MAX_TG_MSG = 4000


def _send_telegram(chat_id: str, message: str) -> str:
    """Send a Telegram message. Returns 'Sent.' or an error string.

    Uses httpx (sync) instead of requests so the function is safe to call via
    asyncio.to_thread() from async callers without pulling in two HTTP stacks.
    """
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chunks = [message[i:i + MAX_TG_MSG] for i in range(0, len(message), MAX_TG_MSG)]
        with httpx.Client(timeout=10) as client:
            for chunk in chunks:
                r = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
                )
                if not r.is_success:
                    return f"Failed: {r.text}"
        return "Sent."
    except Exception as e:
        return f"Error: {e}"


def _send_telegram_file(chat_id: str, file_path: str) -> str:
    """Send a file to a Telegram chat via sendDocument. Returns 'Sent.' or an error string.

    Uses httpx (sync) instead of requests so the function is safe to call via
    asyncio.to_thread() from async callers without pulling in two HTTP stacks.
    """
    try:
        path = Path(file_path).resolve()
        if not str(path).startswith(str(workspace.HOME.resolve())):
            return f"Error: file path {file_path!r} is outside the workspace."
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        with open(path, "rb") as f:
            with httpx.Client(timeout=30) as client:
                r = client.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": chat_id},
                    files={"document": (path.name, f)},
                )
        return "Sent." if r.is_success else f"Failed: {r.text}"
    except FileNotFoundError:
        return f"File not found: {file_path}"
    except Exception as e:
        return f"Error: {e}"


class TelegramSender:
    """Send Telegram messages. Used by scheduler for cron job delivery."""

    def send(self, chat_id: str, message: str) -> str:
        return _send_telegram(chat_id, message)
