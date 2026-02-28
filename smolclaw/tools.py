"""Minimal tools module — shared Telegram helper + TelegramSender for scheduler use."""
from __future__ import annotations

import os
from pathlib import Path

import requests


def _send_telegram(chat_id: str, message: str) -> str:
    """Send a Telegram message. Returns 'Sent.' or an error string."""
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        return "Sent." if r.ok else f"Failed: {r.text}"
    except Exception as e:
        return f"Error: {e}"


def _send_telegram_file(chat_id: str, file_path: str) -> str:
    """Send a file to a Telegram chat via sendDocument. Returns 'Sent.' or an error string."""
    try:
        path = Path(file_path)
        if not path.exists():
            return f"File not found: {file_path}"
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id},
                files={"document": (path.name, f)},
                timeout=30,
            )
        return "Sent." if r.ok else f"Failed: {r.text}"
    except Exception as e:
        return f"Error: {e}"


class TelegramSender:
    """Send Telegram messages. Used by scheduler for cron job delivery."""

    def send(self, chat_id: str, message: str) -> str:
        return _send_telegram(chat_id, message)
