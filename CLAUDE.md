# CLAUDE.md

## Commands

```bash
uv sync                    # install dev deps
uv run pytest              # run tests (always run full suite before pushing)
uv run pytest tests/test_agent.py::test_run_returns_string  # single test
```

## Architecture

Telegram bot wrapping `claude-agent-sdk`. State lives in `~/.smolclaw/` (not in this repo).

**Request flow:** Telegram message → `handlers.py` → `agent.run(chat_id, msg)` → `ClaudeSDKClient` tool loop (max 10 turns) → reply via Telegram.

**Session lifecycle:** One `_Session` per `chat_id` in `agent._sessions`. Created on first message, reused across turns. Reset by `/reset`, model/effort change, dynamic tool change, or auto-rotation at 70% context fill. `reset_session()` also cleans up browser contexts.

**Auto-rotation** (`agent.py`): When context exceeds 70%, builds a handover from recent session logs and resets. Next message gets a fresh client with handover in system prompt. Invisible to user.

**System prompt** (`agent._system_prompt()`): Built on client creation (not per-message) for prompt caching. Order: workspace context → SOUL.md → USER.md → skills list → MEMORY.md → handover. Current timestamp prepended to user message instead.

## Key files

| File | What it does |
|------|-------------|
| `agent.py` | Session management, system prompt, `run()`, auto-rotation, `spawn_task` |
| `handlers.py` | Telegram command/message handlers, typing loop, reactions, `/update` |
| `tools_sdk.py` | Built-in SDK tools (telegram, browser, search_sessions, etc.) |
| `tool_loader.py` | Hot-loads `~/.smolclaw/tools/*.py` as SDK tools on every request |
| `browser.py` | Playwright browser manager (lazy singleton, per-chat contexts) |
| `main.py` | CLI entrypoint, bot wiring, startup/shutdown hooks |
| `version.py` | Shared version utilities: `local_version()`, `check_remote_version()`, `get_update_summary()` |
| `handover.py` | Save/load/clear handover notes across restarts |

## Tools

**Built-in** (5): `Bash`, `Read`, `Write`, `WebSearch`, `WebFetch`

**SDK tools** (`tools_sdk.py`): `telegram_send`, `telegram_send_file`, `telegram_send_voice`, `telegram_react`, `save_handover`, `self_restart`, `self_update`, `update_config`, `read_skill`, `search_sessions`, `browse`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_eval`

**Dynamic tools** (`~/.smolclaw/tools/*.py`): Must export `SCHEMA` (OpenAI function schema dict) and `execute()`. All params arrive as strings — always coerce defensively.

## Things that bite you

- **`/update` handler is separate from `self_update` tool.** Both call `uv tool install --upgrade` then SIGTERM for systemd restart. Version logic is shared via `version.py` — don't duplicate.
- **Tool wrapper casts ALL params to strings.** Every tool (built-in and dynamic) must defensively coerce: `int()`, `json.loads()` with try/except. Never trust types.
- **System prompt is built once per client, not per message.** Don't add per-message dynamic content to `_system_prompt()` — it breaks prompt caching. Put ephemeral info in the user message instead.
- **Dynamic tool change resets the session.** Adding/modifying a `.py` in `tools/` silently drops conversation history for that chat. Browser contexts are cleaned up too.
- **Handover is one-shot.** Written to `handover.md`, injected into system prompt on next client creation, then deleted. Max 4000 chars.
- **Edited messages are reprocessed.** `on_message` handles both `update.message` and `update.edited_message`. Use `update.edited_message or update.message` to get the right one.
- **Test mocks need `edited_message = None`.** MagicMock is truthy — tests that create mock Updates must explicitly set `update.edited_message = None` or the handler picks up the mock as an edit.
- **`importlib.metadata` doesn't work in uv tool installs.** `importlib.metadata.version("smolclaw")` throws `PackageNotFoundError` when installed via `uv tool install`. Use `version.local_version()` which falls back to parsing `uv tool list` output, then `pyproject.toml`.
- **Always end-to-end test before pushing, not just unit tests.** Unit tests passing doesn't mean the feature works in the real environment. For version checks, runtime metadata, or anything environment-dependent — verify the actual function output in a quick `uv run python -c "..."` smoke test.

## Conventions

- **TDD: write tests first, then implement.** Write a failing test that captures the expected behavior, then write the code to make it pass. This catches environment mismatches and edge cases before they ship.
- Bump version in `pyproject.toml` after every set of changes (before suggesting `/update`).
- Never add co-author lines or "Generated with Claude Code" to commits.
- Run full test suite before pushing — `uv run pytest`.
- Keep `CUSTOM_TOOLS` list at the bottom of `tools_sdk.py` in sync when adding tools.
- Browser tools use lazy imports (`from .browser import BrowserManager`) to avoid importing Playwright at module load.
