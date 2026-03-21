from __future__ import annotations

import json

from . import workspace

_MAX_LOG_SIZE = 50_000_000  # 50 MB — skip files larger than this


def _is_chat_message(entry: dict, chat_id: str) -> bool:
    if entry.get("chat_id") != chat_id:
        return False
    if entry.get("role") not in ("user", "assistant"):
        return False
    content = entry.get("content", "")
    return isinstance(content, str) and bool(content.strip())


def _collect_chat_messages(chat_id: str) -> list[dict]:
    sessions_dir = workspace.HOME / "sessions"
    if not sessions_dir.exists():
        return []

    files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:2]
    messages: list[dict] = []
    for f in files:
        try:
            if f.stat().st_size > _MAX_LOG_SIZE:
                continue
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if _is_chat_message(entry, chat_id):
                        messages.append(entry)
        except Exception:
            continue
    return messages


def build_auto_handover(chat_id: str, reason: str = "auto-rotated due to context pressure") -> str:
    messages = _collect_chat_messages(chat_id)
    if not messages:
        return ""

    recent = messages[-30:]
    parts = ["CONTEXT (recent conversation):"]
    for msg in recent:
        parts.append(f"[{msg.get('ts', '')[:16]}] {msg['role']}: {msg['content'][:600]}")

    user_msgs = [m for m in recent if m.get("role") == "user"]
    if user_msgs:
        last_topics = [m["content"][:100] for m in user_msgs[-5:]]
        parts.append("\nRECENT USER TOPICS:\n" + "\n".join(f"- {t}" for t in last_topics))

    parts.append(f"\nPENDING: Review recent topics above and resume if user refers to them. ({reason})")
    return "\n".join(parts)
