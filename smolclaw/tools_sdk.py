"""Custom tools as claude-agent-sdk @tool-decorated async functions."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

from claude_agent_sdk import tool

from . import workspace
from .auth import default_chat_id, is_allowed
from .tools import _edit_telegram, _send_telegram, _send_telegram_file, _send_telegram_voice, _set_reaction, _text_to_voice
from .version import check_remote_version as _check_remote_version
from .version import get_update_summary as _get_update_summary
from .version import local_version as _local_version

_ALLOWED_SOURCE_PREFIX = "git+https://github.com/saikatkumardey/smolclaw"


def _text(t: str) -> dict:
    """Standard tool response wrapper."""
    return {"content": [{"type": "text", "text": t}]}


@tool(
    "telegram_send",
    "Send a new Telegram message. Returns the message_id so you can edit it later with telegram_edit. "
    "Prefer sending one message and editing it over sending multiple messages.",
    {"chat_id": str, "message": str},
)
async def telegram_send(args: dict) -> dict:
    if not is_allowed(args["chat_id"]):
        return _text(f"Error: chat_id {args['chat_id']!r} is not in ALLOWED_USER_IDS.")
    text = await asyncio.to_thread(_send_telegram, args["chat_id"], args["message"])
    return _text(text)


@tool(
    "telegram_edit",
    "Edit an existing Telegram message by message_id. Use this to update a previous message "
    "instead of sending a new one. Ideal for progress updates or refining a response.",
    {"chat_id": str, "message_id": str, "message": str},
)
async def telegram_edit(args: dict) -> dict:
    if not is_allowed(args["chat_id"]):
        return _text(f"Error: chat_id {args['chat_id']!r} is not in ALLOWED_USER_IDS.")
    try:
        message_id = int(args["message_id"])
    except (ValueError, TypeError):
        return _text(f"Error: invalid message_id {args.get('message_id')!r}")
    text = await asyncio.to_thread(_edit_telegram, args["chat_id"], message_id, args["message"])
    return _text(text)


@tool("save_handover", "Save a handover note so state survives restart or update. Call before self_restart or self_update.", {"summary": str})
async def save_handover(args: dict) -> dict:
    from .handover import save
    save(args["summary"])
    return _text("Handover saved.")


@tool("self_restart", "Restart the smolclaw process in-place. Always call save_handover first.", {})
async def self_restart(args: dict) -> dict:
    import signal
    if chat_id := default_chat_id():
        await asyncio.to_thread(_send_telegram, chat_id, "Restarting…")
    os.kill(os.getpid(), signal.SIGTERM)
    return _text("unreachable")


@tool("self_update", "Check for updates and install if a newer version is available. Always call save_handover first.", {})
async def self_update(args: dict) -> dict:
    source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")
    if not source.startswith(_ALLOWED_SOURCE_PREFIX):
        return _text(f"Error: SMOLCLAW_SOURCE {source!r} is not an allowed update URL.")

    old_version = _local_version()

    remote = _check_remote_version(source)
    if remote and remote == old_version:
        return _text(f"Already on latest version (v{old_version}). No update needed.")

    # Actually install
    result = subprocess.run(
        ["uv", "tool", "install", "--upgrade", source],
        capture_output=True, text=True, timeout=120,
    )
    chat_id = default_chat_id()
    if result.returncode != 0:
        msg = f"Update failed:\n{result.stderr}"
        if chat_id:
            await asyncio.to_thread(_send_telegram, chat_id, msg)
        return _text(msg)

    # Build update summary with version + changelog
    summary = _get_update_summary(source, old_version)
    if chat_id:
        await asyncio.to_thread(_send_telegram, chat_id, f"Update successful. Restarting...\n\n{summary}")

    # Save summary in handover so the agent knows what changed after restart
    from .handover import save
    save(f"Self-update completed.\n\n{summary}\n\nPENDING: none")

    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return _text("unreachable")


@tool("telegram_send_file", "Send a local file (markdown, CSV, script, image, etc.) to a Telegram chat_id.", {"chat_id": str, "file_path": str})
async def telegram_send_file(args: dict) -> dict:
    if not is_allowed(args["chat_id"]):
        return _text(f"Error: chat_id {args['chat_id']!r} is not in ALLOWED_USER_IDS.")
    resolved = Path(args["file_path"]).resolve()
    try:
        resolved.relative_to(workspace.HOME.resolve())
    except ValueError:
        return _text(f"Error: file path {args['file_path']!r} is outside the workspace.")
    text = await asyncio.to_thread(_send_telegram_file, args["chat_id"], args["file_path"])
    return _text(text)


@tool(
    "update_config",
    "Update a runtime configuration value (max_turns, subagent_timeout, subagent_max_turns, btw_model). Use /models command for main model changes.",
    {"key": str, "value": str},
)
async def update_config(args: dict) -> dict:
    from .config import Config

    _AGENT_EXCLUDED = {"model"}  # model changes require session resets; use /models
    mutable = set(Config.DEFAULTS.keys()) - _AGENT_EXCLUDED
    key = args["key"]
    if key not in mutable:
        return _text(f"Error: Cannot set '{key}' via this tool. Use /models for model changes.")
    cfg = Config.load()
    # Coerce to the expected type from DEFAULTS
    expected_type = type(Config.DEFAULTS[key])
    try:
        value = expected_type(args["value"])
        cfg.set(key, value)
    except (KeyError, TypeError, ValueError) as e:
        return _text(f"Error: {e}")
    return _text(f"Set {key} = {value}")


@tool("read_skill", "Read the instructions for a skill by name.", {"name": str})
async def read_skill_tool(args: dict) -> dict:
    from .skills import read_skill
    content = read_skill(args["name"], workspace.SKILLS_DIR)
    if content is None:
        return _text(f"Skill {args['name']!r} not found.")
    return _text(content)


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
        return _text("No session logs found.")

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
                return _text(f"[qmd results]\n{qmd_result.stdout.strip()}")
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
        return _text(f"No matches for '{query_str}' in recent session logs.")
    return _text("\n\n".join(results))


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


@tool(
    "telegram_react",
    "React to a user's Telegram message with an emoji. Use this to acknowledge messages, "
    "express understanding, or give feedback. Pick the emoji that fits the situation — "
    "don't always use the same one. The message_id is provided in the user message context.",
    {"chat_id": str, "message_id": str, "emoji": str},
)
async def telegram_react(args: dict) -> dict:
    chat_id = str(args["chat_id"])
    if not is_allowed(chat_id):
        return _text(f"Error: chat_id {chat_id!r} is not in ALLOWED_USER_IDS.")
    try:
        message_id = int(args["message_id"])
    except (ValueError, TypeError):
        return _text(f"Error: invalid message_id {args.get('message_id')!r}")
    emoji = str(args.get("emoji", "\U0001f44d") or "\U0001f44d")
    result = await asyncio.to_thread(_set_reaction, chat_id, message_id, emoji)
    return _text(result)


@tool(
    "test_tool",
    "Validate a staged tool file in tools/.staging/ before deploying. "
    "Checks SCHEMA structure and callable execute. Optionally runs execute with test_args (JSON string).",
    {"file_name": str, "test_args": str},
)
async def test_tool(args: dict) -> dict:
    from .tool_loader import validate_tool_module

    file_name = str(args["file_name"])
    test_args_raw = str(args.get("test_args", "") or "")
    path = workspace.TOOLS_STAGING / file_name

    if not path.exists():
        return _text(f"FAIL: {file_name} not found in staging directory.")

    ok, errors, mod = validate_tool_module(path)
    if not ok:
        return _text(f"FAIL: {file_name}\n" + "\n".join(f"- {e}" for e in errors))

    report = f"PASS: {file_name} — schema and execute valid."

    if test_args_raw:
        try:
            test_args_parsed = json.loads(test_args_raw)
        except json.JSONDecodeError as e:
            return _text(f"PASS (validation) but test_args is invalid JSON: {e}")
        try:
            result = mod.execute(**test_args_parsed)
            report += f"\nexecute() returned: {result!r}"
        except Exception as e:
            report += f"\nexecute() raised: {type(e).__name__}: {e}"

    return _text(report)


@tool(
    "deploy_tool",
    "Move a staged tool from tools/.staging/ to tools/ (goes live on next message). "
    "Re-validates before deploying.",
    {"file_name": str},
)
async def deploy_tool(args: dict) -> dict:
    from .tool_loader import validate_tool_module

    file_name = str(args["file_name"])
    src = workspace.TOOLS_STAGING / file_name

    if not src.exists():
        return _text(f"Error: {file_name} not found in staging directory.")

    ok, errors, _mod = validate_tool_module(src)
    if not ok:
        return _text(f"Refused to deploy {file_name} — validation failed:\n" + "\n".join(f"- {e}" for e in errors))

    dest = workspace.TOOLS_DIR / file_name
    shutil.move(str(src), str(dest))
    return _text(f"Deployed {file_name} to tools/. It will be live on the next message.")


@tool(
    "disable_tool",
    "Disable a live tool by renaming foo.py → foo.py.disabled. Reversible.",
    {"tool_name": str},
)
async def disable_tool(args: dict) -> dict:
    name = str(args["tool_name"])
    # Accept both "foo" and "foo.py"
    if not name.endswith(".py"):
        name = name + ".py"
    path = workspace.TOOLS_DIR / name
    if not path.exists():
        return _text(f"Error: {name} not found in tools directory.")
    disabled = path.with_suffix(".py.disabled")
    path.rename(disabled)
    return _text(f"Disabled {name} → {disabled.name}. Rename back to re-enable.")


@tool(
    "update_subconscious",
    "Manage the subconscious reflection log. Actions: 'add' (with thread_data JSON), "
    "'resolve' (with thread_id), 'list' (returns all open threads).",
    {"action": str, "thread_id": str, "thread_data": str},
)
async def update_subconscious(args: dict) -> dict:
    from . import subconscious

    action = str(args.get("action", ""))
    if action == "list":
        threads = subconscious.load_threads()
        if not threads:
            return _text("No open threads.")
        import yaml
        return _text(yaml.dump(threads, default_flow_style=False))

    if action == "resolve":
        thread_id = str(args.get("thread_id", ""))
        if not thread_id:
            return _text("Error: thread_id required for resolve action.")
        removed = subconscious.resolve_thread(thread_id)
        if removed:
            return _text(f"Resolved thread: {thread_id}")
        return _text(f"Thread not found: {thread_id}")

    if action == "add":
        raw = str(args.get("thread_data", "") or "")
        if not raw:
            return _text("Error: thread_data (JSON string) required for add action.")
        try:
            thread_data = json.loads(raw)
        except json.JSONDecodeError as e:
            return _text(f"Error: invalid JSON in thread_data: {e}")
        try:
            tid = subconscious.add_thread(thread_data)
        except ValueError as e:
            return _text(f"Error: {e}")
        return _text(f"Added thread: {tid}")

    return _text(f"Error: unknown action {action!r}. Use 'add', 'resolve', or 'list'.")


@tool(
    "reflect",
    "Trigger an immediate subconscious reflection cycle. "
    "Reads open threads, recent session logs, and memory, then decides whether to act.",
    {},
)
async def reflect(args: dict) -> dict:
    from .scheduler import _run_subconscious
    await asyncio.to_thread(_run_subconscious)
    return _text("Reflection cycle complete.")


CUSTOM_TOOLS = [
    telegram_send, telegram_edit, telegram_send_file, save_handover, self_restart, self_update,
    update_config, read_skill_tool, search_sessions,
    browse, browser_click, browser_type, browser_screenshot, browser_eval,
    telegram_send_voice, telegram_react,
    test_tool, deploy_tool, disable_tool,
    update_subconscious, reflect,
]
