"""auth.py — single source of truth for allowed-user validation."""
from __future__ import annotations

import functools
import os
from collections.abc import Callable


def allowed_ids() -> set[str]:
    """Return the set of allowed Telegram user IDs from the environment."""
    raw = os.getenv("ALLOWED_USER_IDS", "")
    return {cid.strip() for cid in raw.split(",") if cid.strip()}


def is_allowed(chat_id: str | int) -> bool:
    """Check whether a chat ID is in the allowlist."""
    return str(chat_id) in allowed_ids()


def default_chat_id() -> str:
    """Return the first allowed user ID, or empty string if none configured."""
    ids = allowed_ids()
    return next(iter(ids), "") if ids else ""


def require_allowed(fn: Callable) -> Callable:
    """Decorator: silently drops updates from non-allowlisted users."""
    @functools.wraps(fn)
    async def wrapper(update, context):
        if update.effective_chat and is_allowed(update.effective_chat.id):
            return await fn(update, context)
    return wrapper
