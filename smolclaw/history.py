"""SQLite conversation history."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("smolclaw.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id   TEXT NOT NULL,
            role      TEXT NOT NULL,
            content   TEXT NOT NULL,
            timestamp TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def load(chat_id: str, limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def append(chat_id: str, role: str, content: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )
