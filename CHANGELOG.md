# Changelog

All notable changes to smolclaw will be documented in this file.

## [0.7.6] - 2026-03-20

- Add Makefile with one-shot `make release` command
- /cc: full Claude Code mode — takes over messages, streams tool output, shows session footer
- /cc: switch from ACP to direct `claude` CLI stream-json, auto-bypass permissions
- Remove heartbeat (redundant with subconscious)
- Build rich handover from session logs on /restart

## [0.6.6] - 2026-03-14

- Add `smolclaw chat` TUI for terminal-based conversations
- Inline message editing to reduce Telegram chat spam
- OOM-safe log streaming, null-message safety, stale cache eviction
- Heartbeat and subconscious timeouts fixed — local-first change detection
- Self-improvement step added to subconscious reflection
- Serialize concurrent messages per chat_id to prevent (no response)
- python-doctor CI badge and workflow

## [0.5.4] - 2026-03-08

- Add subconscious: background reflection loop for autonomous reasoning
- Add /btw command for side questions without polluting history
- Add TTS voice messages via edge-tts
- Add telegram_react tool and emoji reaction handler
- Self-evolving tools: test_tool, deploy_tool, disable_tool
- Split SOUL.md into SOUL.md (identity) + AGENT.md (operational playbook)
- PID file guard to prevent duplicate foreground instances
- Slim cron prompts + model routing — cut cron token usage ~60%
- Lightpanda browser support
- Ruff linter config and full lint cleanup

## [0.4.1] - 2026-02-28

- Add headless browser tools (Playwright)
- Auto session rotation and search_sessions tool
- /effort command with inline keyboard picker
- /update command with direct handover, upgrade, and restart
- /context command and auto-warn on context window fill
- /crons command to list scheduled jobs
- spawn_task progress reporting and /tasks command
- Config system (smolclaw.json), token-only usage tracking
- systemd user service, watchdog, daemon management
- install.sh one-liner, GitHub Actions release workflow
- Security hardening: auth validation, path restrictions, message chunking
- Streaming responses, /cancel interrupt

## [0.1.0] - 2026-02-20

- Initial release
- Telegram bot wrapping claude-agent-sdk
- File send/receive, non-blocking spawn_task
- Cron job scheduler with YAML config
- Workspace health doctor
