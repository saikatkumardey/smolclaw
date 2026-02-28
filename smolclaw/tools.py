"""Minimal tools module — shared Telegram helper + TelegramSender for scheduler use."""
from __future__ import annotations

import os

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


class TelegramSender:
    """Send Telegram messages. Used by scheduler for cron job delivery."""

    def send(self, chat_id: str, message: str) -> str:
        return _send_telegram(chat_id, message)
