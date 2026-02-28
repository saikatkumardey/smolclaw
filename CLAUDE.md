# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (development)
uv sync

# Install as a tool (production-style)
uv tool install .

# Run
smolclaw setup         # interactive first-time wizard (Telegram token + user ID + Claude auth)
smolclaw setup-token   # re-run Claude authentication only (API key or claude auth login)
smolclaw start         # start the Telegram bot
smolclaw update        # pull latest from GitHub and reinstall

# Tests
uv run pytest
uv run pytest tests/test_agent.py::test_run_returns_string   # single test

# Regenerate demo SVG
uv tool run termstage render demo.yaml --animated --output assets/setup-demo.svg
```

## Architecture

SmolClaw is a self-hosted Telegram bot that wraps a `claude-agent-sdk` `ClaudeSDKClient`. The agent persists state in `~/.smolclaw/` (overridable via `SMOLCLAW_HOME`).

**Request flow:**
1. Telegram message → `main.py` handler → `await agent.run(chat_id, message)`
2. `agent.py` maintains one `_Session` (holding `ClaudeSDKClient` + dynamic tool names) per `chat_id` in `_sessions` dict
3. Each client is constructed with a system prompt (SOUL + USER + MEMORY + skills) and an MCP server containing custom tools
4. `claude-agent-sdk` runs the tool-calling loop (max 10 turns) and returns a string reply
5. Reply is converted from CommonMark to Telegram Markdown v1 (`_to_telegram_md`) and sent with `parse_mode="Markdown"`

**Workspace files** (`~/.smolclaw/`):

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent identity and operating instructions |
| `USER.md` | User profile (name, timezone, preferences) |
| `MEMORY.md` | Persistent agent memory across sessions |
| `HEARTBEAT.md` | Instructions for the 30-minute heartbeat cron |
| `crons.yaml` | Scheduled jobs (APScheduler cron syntax) |
| `skills/<name>/SKILL.md` | Lazily-loaded skill docs injected into system prompt |
| `tools/<name>.py` | User-defined custom tools, hot-loaded on every request |
| `sessions/<date>.jsonl` | JSONL session logs (one file per day) |
| `handover.md` | State persisted across restarts/updates (read once, then deleted) |

**Built-in tools** (Claude Code native, 5): `Bash`, `Read`, `Write`, `WebSearch`, `WebFetch`.

**Custom SDK tools** (`smolclaw/tools_sdk.py`, 5): `telegram_send`, `save_handover`, `self_restart`, `self_update`, `spawn_task` — served via an in-process MCP server named `smolclaw`. Telegram HTTP logic lives in `smolclaw/tools.py:_send_telegram` and is shared with `TelegramSender` (used by the scheduler).

**Custom tools** (`smolclaw/tool_loader.py`): Any `.py` file in `~/.smolclaw/tools/` that exports `SCHEMA` (OpenAI function schema dict) and `execute` (callable) is dynamically loaded as an SDK `@tool`. Tools are reloaded on every request — no restart needed. A change in the dynamic tool set triggers a one-time client reconnect for that chat session.

**Skills** (`smolclaw/skills.py`): Skill docs (`~/.smolclaw/skills/*/SKILL.md`) are read and concatenated into the system prompt on every request.

**Scheduler** (`smolclaw/scheduler.py`): Reads `~/.smolclaw/crons.yaml` at startup and schedules jobs via APScheduler. Each job calls `asyncio.run(agent.run(...))` (stateless per execution) and delivers the result via `TelegramSender`. Heartbeat jobs suppress output if `HEARTBEAT_OK` appears anywhere in the agent reply.

**Sub-agents** (`spawn_task`): Uses the SDK's `query()` function to run an isolated sub-agent with a restricted tool set (no Telegram, no restart) and a configurable timeout (`asyncio.timeout`).

**System prompt construction** (`agent._system_prompt()`): Built fresh on client creation from workspace files + skills + optional handover note + onboarding block (when `USER.md` still has `"Not set yet"`). The prompt is kept stable (for prompt caching) by prepending the current timestamp to the user message instead.

**Model selection**: `get_current_model()` reads `SMOLCLAW_MODEL` env var (default `claude-sonnet-4-6`). `set_model()` persists the choice to `.env` and resets all sessions. Exposed via `/model` and `/models` Telegram commands.

## Bot commands

| Command | Handler | Notes |
|---------|---------|-------|
| `/start` | `on_start` | |
| `/help` | `on_help` | |
| `/status` | `on_status` | shows model, workspace, tool/skill counts |
| `/model` | `on_model` | shows current model |
| `/models` | `on_models` | inline keyboard to switch model |
| `/reset` | `on_reset` | disconnects and removes cached session |
| `/reload` | `on_reload` | (no-op — tools/skills are hot-reloaded automatically) |
| `/restart` | `on_restart` | `os.execv` in-place restart |
| `/update` | `on_update` | agent calls `self_update` tool |

All handlers guard with `_allowed(update)` — non-allowlisted users get no response.

## Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `TELEGRAM_BOT_TOKEN` | — | Required. Set by `smolclaw setup`. |
| `ALLOWED_USER_IDS` | — | Required. Comma-separated Telegram chat IDs. Set by `smolclaw setup`. Bot refuses to start if unset. |
| `SMOLCLAW_MODEL` | `claude-sonnet-4-6` | Active Claude model. Persisted to `.env` by `/models` command. |
| `LITELLM_MODEL` | `anthropic/claude-sonnet-4-6` | Vision model for photo messages only. |
| `SMOLCLAW_HOME` | `~/.smolclaw` | Workspace directory. |
| `SMOLCLAW_SOURCE` | GitHub URL | Source repo for `self_update`. |
| `SMOLCLAW_SUBAGENT_TIMEOUT` | `120` | Sub-agent timeout in seconds. |

## Key design constraints

- `ALLOWED_USER_IDS` is **required** — `smolclaw start` exits if unset. Every handler silently ignores non-allowlisted users.
- The system prompt is rebuilt on **client creation** (not per-message) to enable prompt caching. Avoid patterns that would force client re-creation on every message.
- Sessions are stored in `_sessions: dict[str, _Session]`. Each `_Session` holds a `ClaudeSDKClient` and the `frozenset` of dynamic tool names active when it was created. Use `reset_session(chat_id)` to disconnect and remove — do not manipulate `_sessions` directly from `main.py`.
- Custom tools and skills are **hot-reloaded** on every request without restart. A new tool in `~/.smolclaw/tools/` triggers a one-time client reconnect.
- Cron jobs use `asyncio.run()` (fresh event loop per execution) — stateless, do not share sessions with the main bot.
- `Bash` runs commands with `cwd=~/.smolclaw/` by default (set via `ClaudeAgentOptions.cwd`).
- Heartbeat jobs check `HEARTBEAT_OK in result` (substring, not exact match) to suppress forwarding the reply to Telegram.
- Reply text is passed through `_to_telegram_md()` before sending: converts `**bold**` → `*bold*` and `## headings` → `*heading*`. Falls back to plain text if Telegram rejects the parse.
- Photo messages: `litellm.completion()` runs in `asyncio.to_thread` to avoid blocking the event loop. Temp files are deleted in a `finally` block.
