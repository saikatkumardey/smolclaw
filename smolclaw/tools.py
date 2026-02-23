"""Six tools. No more."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import requests
import trafilatura

def _workspace() -> Path:
    from . import workspace
    return workspace.HOME
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")
_BOT_TOKEN = lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")


def shell_exec(command: str, timeout: int = 30) -> str:
    try:
        ws = _workspace()
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=ws if ws.exists() else None)
        return (r.stdout + r.stderr).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Timeout after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def file_read(path: str, offset: int = 0, limit: int = 200) -> str:
    try:
        lines = Path(path).read_text().splitlines()
        return "\n".join(lines[offset : offset + limit]) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


def file_write(path: str, content: str) -> str:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def web_fetch(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded)
        return text[:3000] if text else "No content extracted."
    except Exception as e:
        return f"Error: {e}"


def web_search(query: str, n: int = 5) -> str:
    try:
        r = requests.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=10)
        results = r.json().get("results", [])[:n]
        if not results:
            return "No results."
        return "\n".join(f"- {x['title']}: {x['url']}\n  {x.get('content','')[:150]}" for x in results)
    except Exception as e:
        return f"Search failed: {e}"


def telegram_send(chat_id: str, message: str) -> str:
    try:
        token = _BOT_TOKEN()
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        return "Sent." if r.ok else f"Failed: {r.text}"
    except Exception as e:
        return f"Error: {e}"


TOOL_MAP = {
    "shell_exec": shell_exec,
    "file_read": file_read,
    "file_write": file_write,
    "web_fetch": web_fetch,
    "web_search": web_search,
    "telegram_send": telegram_send,
}

TOOLS = [
    {"type": "function", "function": {
        "name": "shell_exec",
        "description": "Run a shell command. Covers git, file ops, system tasks. Returns stdout+stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "default": 30},
        }, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "file_read",
        "description": "Read a file. offset/limit are line numbers.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 200},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "file_write",
        "description": "Write content to a file. Creates parent dirs.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "web_fetch",
        "description": "Fetch a URL and return readable text.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
        }, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web via local SearXNG.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "n": {"type": "integer", "default": 5},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "telegram_send",
        "description": "Send a Telegram message to a chat_id. For cron delivery.",
        "parameters": {"type": "object", "properties": {
            "chat_id": {"type": "string"},
            "message": {"type": "string"},
        }, "required": ["chat_id", "message"]},
    }},
]
