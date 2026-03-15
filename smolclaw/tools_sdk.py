"""Custom tools as claude-agent-sdk @tool-decorated async functions."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from claude_agent_sdk import tool

from .tools import _send_telegram, _send_telegram_file, _send_telegram_voice, _text_to_voice
from . import workspace
from .auth import is_allowed, default_chat_id

_ALLOWED_SOURCE_PREFIX = "git+https://github.com/saikatkumardey/smolclaw"


@tool("telegram_send", "Send a Telegram message to a chat_id. For cron delivery.", {"chat_id": str, "message": str})
async def telegram_send(args: dict) -> dict:
    if not is_allowed(args["chat_id"]):
        return {"content": [{"type": "text", "text": f"Error: chat_id {args['chat_id']!r} is not in ALLOWED_USER_IDS."}]}
    text = await asyncio.to_thread(_send_telegram, args["chat_id"], args["message"])
    return {"content": [{"type": "text", "text": text}]}


@tool("save_handover", "Save a handover note so state survives restart or update. Call before self_restart or self_update.", {"summary": str})
async def save_handover(args: dict) -> dict:
    from .handover import save
    save(args["summary"])
    return {"content": [{"type": "text", "text": "Handover saved."}]}


@tool("self_restart", "Restart the smolclaw process in-place. Always call save_handover first.", {})
async def self_restart(args: dict) -> dict:
    if chat_id := default_chat_id():
        await asyncio.to_thread(_send_telegram, chat_id, "Restarting…")
    exe = shutil.which("smolclaw") or sys.argv[0]
    base = sys.argv[1:] if sys.argv[1:] else ["start"]
    if "--foreground" not in base and "-f" not in base:
        base = base + ["--foreground"]
    os.execv(exe, [exe] + base)
    return {"content": [{"type": "text", "text": "unreachable"}]}


def _get_update_summary(source: str, old_version: str) -> str:
    """Get version and changelog after a successful update."""
    import importlib.metadata
    import re

    # Get new version from the freshly installed package
    try:
        # Invalidate cached metadata so we pick up the new install
        importlib.metadata.packages_distributions.cache_clear()
    except AttributeError:
        pass
    try:
        new_ver_result = subprocess.run(
            ["uv", "tool", "run", "smolclaw", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        new_version = new_ver_result.stdout.strip() if new_ver_result.returncode == 0 else None
    except Exception:
        new_version = None

    if not new_version:
        # Fallback: parse from uv tool list
        try:
            list_result = subprocess.run(
                ["uv", "tool", "list"], capture_output=True, text=True, timeout=10,
            )
            for line in list_result.stdout.splitlines():
                if "smolclaw" in line.lower():
                    m = re.search(r"v?(\d+\.\d+\.\d+)", line)
                    if m:
                        new_version = m.group(1)
                    break
        except Exception:
            pass

    new_version = new_version or "unknown"

    # Get recent commits from GitHub for the changelog
    changes = []
    try:
        repo_match = re.search(r"github\.com/([^/]+/[^/.\s]+)", source)
        if repo_match:
            repo = repo_match.group(1).rstrip(".git")
            import requests
            resp = requests.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"per_page": "10"},
                timeout=10,
            )
            if resp.status_code == 200:
                for commit in resp.json():
                    msg = commit.get("commit", {}).get("message", "").split("\n")[0]
                    if msg and not msg.startswith("bump version"):
                        changes.append(f"- {msg}")
                        if len(changes) >= 5:
                            break
    except Exception:
        pass

    parts = [f"Updated: {old_version} -> {new_version}"]
    if changes:
        parts.append("\nRecent changes:\n" + "\n".join(changes))
    return "\n".join(parts)


@tool("self_update", "Pull latest smolclaw from GitHub, reinstall, and restart. Always call save_handover first.", {})
async def self_update(args: dict) -> dict:
    import importlib.metadata

    source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")
    if not source.startswith(_ALLOWED_SOURCE_PREFIX):
        return {"content": [{"type": "text", "text": f"Error: SMOLCLAW_SOURCE {source!r} is not an allowed update URL."}]}

    # Capture current version before update
    try:
        old_version = importlib.metadata.version("smolclaw")
    except Exception:
        old_version = "unknown"

    result = subprocess.run(
        ["uv", "tool", "install", "--upgrade", source],
        capture_output=True, text=True, timeout=120,
    )
    chat_id = default_chat_id()
    if result.returncode != 0:
        msg = f"Update failed:\n{result.stderr}"
        if chat_id:
            await asyncio.to_thread(_send_telegram, chat_id, msg)
        return {"content": [{"type": "text", "text": msg}]}

    # Build update summary with version + changelog
    summary = _get_update_summary(source, old_version)
    if chat_id:
        await asyncio.to_thread(_send_telegram, chat_id, f"Update successful. Restarting...\n\n{summary}")

    # Save summary in handover so the agent knows what changed after restart
    from .handover import save
    save(f"Self-update completed.\n\n{summary}\n\nPENDING: none")

    exe = shutil.which("smolclaw") or sys.argv[0]
    base = sys.argv[1:] if sys.argv[1:] else ["start"]
    if "--foreground" not in base and "-f" not in base:
        base = base + ["--foreground"]
    os.execv(exe, [exe] + base)
    return {"content": [{"type": "text", "text": "unreachable"}]}


@tool("telegram_send_file", "Send a local file (markdown, CSV, script, image, etc.) to a Telegram chat_id.", {"chat_id": str, "file_path": str})
async def telegram_send_file(args: dict) -> dict:
    if not is_allowed(args["chat_id"]):
        return {"content": [{"type": "text", "text": f"Error: chat_id {args['chat_id']!r} is not in ALLOWED_USER_IDS."}]}
    resolved = Path(args["file_path"]).resolve()
    if not str(resolved).startswith(str(workspace.HOME.resolve())):
        return {"content": [{"type": "text", "text": f"Error: file path {args['file_path']!r} is outside the workspace."}]}
    text = await asyncio.to_thread(_send_telegram_file, args["chat_id"], args["file_path"])
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "update_config",
    "Update a runtime configuration value (max_turns, subagent_timeout, subagent_max_turns). Use /models command for model changes.",
    {"key": str, "value": int},
)
async def update_config(args: dict) -> dict:
    from .config import Config

    _AGENT_EXCLUDED = {"model"}  # model changes require session resets; use /models
    mutable = set(Config.DEFAULTS.keys()) - _AGENT_EXCLUDED
    key = args["key"]
    if key not in mutable:
        return {"content": [{"type": "text", "text": f"Error: Cannot set '{key}' via this tool. Use /models for model changes."}]}
    cfg = Config.load()
    try:
        cfg.set(key, int(args["value"]))
    except (KeyError, TypeError, ValueError) as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}
    return {"content": [{"type": "text", "text": f"Set {key} = {args['value']}"}]}


@tool("read_skill", "Read the instructions for a skill by name.", {"name": str})
async def read_skill_tool(args: dict) -> dict:
    from .skills import read_skill
    content = read_skill(args["name"], workspace.SKILLS_DIR)
    if content is None:
        return {"content": [{"type": "text", "text": f"Skill {args['name']!r} not found."}]}
    return {"content": [{"type": "text", "text": content}]}


@tool(
    "search_sessions",
    "Search past conversation logs. Use this before claiming you don't remember something. "
    "Returns matching messages from session history.",
    {"query": str, "date": str, "chat_id": str},
)
async def search_sessions(args: dict) -> dict:
    query_str = str(args.get("query", ""))
    date_filter = str(args.get("date", ""))  # optional YYYY-MM-DD
    chat_filter = str(args.get("chat_id", ""))  # optional

    sessions_dir = workspace.HOME / "sessions"
    if not sessions_dir.exists():
        return {"content": [{"type": "text", "text": "No session logs found."}]}

    # Determine files to search
    if date_filter:
        files = [sessions_dir / f"{date_filter}.jsonl"]
    else:
        files = sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:7]

    # Try qmd first (semantic search) if available and query is non-trivial
    if shutil.which("qmd") and len(query_str) > 3:
        try:
            qmd_result = await asyncio.to_thread(
                subprocess.run,
                ["qmd", "search", query_str, "--limit", "10"],
                capture_output=True, text=True, timeout=30,
            )
            if qmd_result.returncode == 0 and qmd_result.stdout.strip():
                return {"content": [{"type": "text", "text": f"[qmd results]\n{qmd_result.stdout.strip()}"}]}
        except Exception:
            pass  # fall through to grep

    # Fallback: grep through JSONL session logs
    results = []
    query_lower = query_str.lower()
    for f in files:
        if not f.exists():
            continue
        try:
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("role") not in ("user", "assistant"):
                    continue
                if chat_filter and entry.get("chat_id") != chat_filter:
                    continue
                content = entry.get("content", "")
                if not isinstance(content, str):
                    continue
                if query_lower in content.lower():
                    ts = entry.get("ts", "")[:16]
                    cid = entry.get("chat_id", "")
                    snippet = content[:300]
                    results.append(f"[{ts}] ({cid}) {entry['role']}: {snippet}")
                    if len(results) >= 20:
                        break
        except Exception:
            continue
        if len(results) >= 20:
            break

    if not results:
        return {"content": [{"type": "text", "text": f"No matches for '{query_str}' in recent session logs."}]}
    return {"content": [{"type": "text", "text": "\n\n".join(results)}]}


def _text(t: str) -> dict:
    return {"content": [{"type": "text", "text": t}]}


@tool(
    "browse",
    "Navigate to a URL in a headless browser. Renders JavaScript. "
    "Returns page title and visible text content. Use for JS-heavy pages that WebFetch can't read. "
    "Creates a persistent browser session per chat_id — subsequent browser_* calls reuse it.",
    {"chat_id": str, "url": str},
)
async def browse(args: dict) -> dict:
    from .browser import BrowserManager

    try:
        result = await BrowserManager.get().navigate(str(args["chat_id"]), str(args["url"]))
        return _text(f"Title: {result['title']}\nURL: {result['url']}\n\n{result['text']}")
    except Exception as e:
        return _text(f"Browser error: {e}")


@tool(
    "browser_click",
    "Click an element on the current browser page by CSS selector.",
    {"chat_id": str, "selector": str},
)
async def browser_click(args: dict) -> dict:
    from .browser import BrowserManager

    try:
        result = await BrowserManager.get().click(str(args["chat_id"]), str(args["selector"]))
        return _text(result)
    except Exception as e:
        return _text(f"Click failed: {e}")


@tool(
    "browser_type",
    "Type text into a form field by CSS selector. Clears existing content first.",
    {"chat_id": str, "selector": str, "text": str},
)
async def browser_type(args: dict) -> dict:
    from .browser import BrowserManager

    try:
        result = await BrowserManager.get().type_text(
            str(args["chat_id"]), str(args["selector"]), str(args["text"])
        )
        return _text(result)
    except Exception as e:
        return _text(f"Type failed: {e}")


@tool(
    "browser_screenshot",
    "Take a screenshot of the current browser page. Returns the file path. "
    "Use Read tool to view the image or telegram_send_file to send it.",
    {"chat_id": str},
)
async def browser_screenshot(args: dict) -> dict:
    from .browser import BrowserManager

    try:
        path = await BrowserManager.get().screenshot(str(args["chat_id"]))
        return _text(f"Screenshot saved: {path}")
    except Exception as e:
        return _text(f"Screenshot failed: {e}")


@tool(
    "browser_eval",
    "Execute JavaScript on the current browser page and return the result.",
    {"chat_id": str, "javascript": str},
)
async def browser_eval(args: dict) -> dict:
    from .browser import BrowserManager

    try:
        result = await BrowserManager.get().evaluate(str(args["chat_id"]), str(args["javascript"]))
        return _text(result)
    except Exception as e:
        return _text(f"JS eval failed: {e}")


@tool(
    "telegram_send_voice",
    "Convert text to a voice message (OGG/Opus) and send it to a Telegram chat. "
    "Great for daily summaries, briefings, or any content that's nicer to listen to. "
    "Uses edge-tts for synthesis. Optional voice parameter (default: en-US-AriaNeural).",
    {"chat_id": str, "text": str, "voice": str, "caption": str},
)
async def telegram_send_voice(args: dict) -> dict:
    import tempfile

    chat_id = str(args["chat_id"])
    if not is_allowed(chat_id):
        return _text(f"Error: chat_id {chat_id!r} is not in ALLOWED_USER_IDS.")

    text = str(args["text"])
    voice = str(args.get("voice", "en-US-AriaNeural") or "en-US-AriaNeural")
    caption = str(args.get("caption", "") or "")

    # Generate voice file
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False, dir=str(workspace.HOME)) as tmp:
        ogg_path = tmp.name

    try:
        result = await asyncio.to_thread(_text_to_voice, text, ogg_path, voice)
        if result != ogg_path:
            return _text(f"TTS error: {result}")

        send_result = await asyncio.to_thread(_send_telegram_voice, chat_id, ogg_path, caption)
        return _text(send_result)
    finally:
        Path(ogg_path).unlink(missing_ok=True)


CUSTOM_TOOLS = [
    telegram_send, telegram_send_file, save_handover, self_restart, self_update,
    update_config, read_skill_tool, search_sessions,
    browse, browser_click, browser_type, browser_screenshot, browser_eval,
    telegram_send_voice,
]
