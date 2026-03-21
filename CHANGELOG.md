# Changelog

All notable changes to smolclaw will be documented in this file.

## [0.7.7] - 2026-03-21

- fix: resolve python-doctor lint, complexity, and precedence issues
- ci: update python-doctor badge to 86/100
- fix: improve memory retention and reduce context amnesia
- update changelog with comprehensive per-version breakdown
- add curated changelog covering all releases, fix Makefile to preserve history


## [0.7.6] - 2026-03-20

- add CC mode that takes over all messages, blocks while busy
- add CC footer to every message showing session status
- clean up CC output: text + compact tool lines, skip tool results
- fix /cc continue: use --resume flag, show stderr on errors
- add Makefile with one-shot release command

## [0.7.2] - 2026-03-20

- auto-bypass permissions in /cc to prevent hanging on approval prompts
- format /cc output with HTML for Telegram (bold tools, code blocks, icons)

## [0.7.1] - 2026-03-20

- switch /cc from acpx to direct claude CLI stream-json
- remove heartbeat from templates and description

## [0.7.0] - 2026-03-20

- fix hardcoded home path in subconscious template
- remove heartbeat — redundant with subconscious, always timed out
- build rich handover from session logs on /restart
- add /cc command for live Claude Code sessions via ACP

## [0.6.6] - 2026-03-20

- add python-doctor CI workflow and score badge
- add smolclaw chat TUI to README

## [0.6.5] - 2026-03-20

- fix lint cleanup — unused imports, unsorted imports, unused variables
- refactor: reduce complexity, fix exceptions, add docstrings — python-doctor score 8 to 84

## [0.6.4] - 2026-03-20

- fix serialize concurrent messages per chat_id to prevent (no response)
- add self-improvement step to subconscious reflection template
- fix always create fresh session for cron jobs
- add inline message editing to reduce chat spam
- fix cron spawn_task silent failure and QoL improvements
- fix self_update tool blocking event loop during version check and install
- add /update command edits inline instead of sending multiple messages
- fix delete placeholder silently when agent produces no text output
- refactor: reduce indirection and deduplicate handler patterns
- fix heartbeat and subconscious timeouts — local-first change detection, slim toolsets
- add smolclaw chat TUI
- fix OOM-safe log streaming, null-message safety, stale cache eviction

## [0.5.4] - 2026-03-16

- prevent duplicate foreground instances via PID file guard

## [0.5.3] - 2026-03-16

- trim heartbeat: remove heavy scripts, add speed guardrails
- slim cron prompt + model routing: cut cron token usage ~60%

## [0.5.2] - 2026-03-16

- fix session races, resource leaks, and error recovery

## [0.5.1] - 2026-03-16

- add subconscious: background reflection loop for autonomous reasoning
- add tool staging workflow to AGENT.md template

## [0.4.28] - 2026-03-16

- add self-evolving tools: test_tool, deploy_tool, disable_tool

## [0.4.27] - 2026-03-16

- add Lightpanda browser support, fix handover/cron/temp-file bugs

## [0.4.26] - 2026-03-16

- add /btw command for side questions without polluting conversation history
- make /btw instant via claude -p with Haiku model and configurable btw_model
- auto-fix PATH on startup so SDK subprocesses find html2md, claude CLI
- enable concurrent update processing so /btw runs in parallel
- fix (no response) when agent acts via tools but returns no text
- add ruff linter config and fix all lint violations
- refactor: deduplicate constants, extract shared patterns, consistent helpers
- fix bugs, add browser cleanup, improve doctor and status

## [0.4.1] - 2026-03-15

- add edit reprocessing, contextual reactions, boot message
- fix clear CLAUDECODE env var to prevent SDK subprocess crash
- fix respect disabled flag in crons.yaml
- add TTS voice messages via edge-tts
- fix /update checks remote version before reinstalling
- fix version check regex and use SIGTERM instead of os.execv
- refactor: extract version utilities, fix restart mechanism
- fix task registry cleanup, doctor TTS checks, remove dead code
- fix cron jobs hanging forever by adding 5-minute timeout
- add reaction handler with agent-controlled telegram_react tool
- split SOUL.md into SOUL.md (identity) + AGENT.md (operational playbook)

## [0.4.0] - 2026-03-15

- add auto session rotation and search_sessions tool to fix memory loss
- add headless browser tools (Playwright)
- fix use systemd user service instead of system service
- fix write logs to file so smolclaw logs command works
- fix use systemctl --user in watchdog for user service
- fix remove SIGCHLD SIG_IGN and add systemd-aware stop command
- show version and changelog after /update
- add persistent typing indicator and instant message reactions

## [0.3.1] - 2026-03-14

- fix delegate restart to systemctl when running under systemd
- fix use sudo for systemctl restart when running as non-root user

## [0.3.0] - 2026-03-14

- add spawn_task progress reporting, task registry, /tasks command
- add /crons command to list scheduled jobs from Telegram
- fix resolve async blocking issues in scheduler, tool_loader, and tools
- remove streaming preview, send single final reply
- eliminate hot-path disk I/O and dead streaming code
- fix pass model and cwd to spawn_task sub-agent options
- add system watchdog via cron
- add install.sh one-liner for curl | bash setup
- ship pre-built binaries via GitHub Actions release workflow
- fix auto-reap claude subprocess zombies from cron jobs
- add /context command and auto-warn on context window fill
- add /effort command to control Claude's thinking depth with inline keyboard picker
- add /update command with direct handover, upgrade, and restart
- load skills on demand instead of preloading into system prompt
- fix suppress (no response) messages from cron jobs
- generate systemd service with Restart=always during setup
- fix /reload actually resets session and /help lists all commands
- add auth.py — single source of truth for allowed-user logic
- refactor: extract handlers into handlers.py, use @require_allowed decorator
- fix always inject --foreground on execv restart
- fix reliability fixes, streaming preview, and test coverage
- use Telegram native sendMessageDraft for streaming

## [0.1.0] - 2026-03-01

- initial release with Telegram bot, Claude agent SDK integration
- add spawn_task for non-blocking background task delegation
- add file send/receive — telegram_send_file tool + on_document handler
- add streaming responses, /cancel interrupt, cost tracking
- send startup notification after restart/update
- add cache token tracking to session logs and /status
- fix load_dotenv override so .env model survives process restart
- fix cron job skipping: allow 2 concurrent instances, 5min misfire grace
- security hardening: chat_id validation, workspace path restriction, message chunking, session race locks
- add Config system with JSON-backed runtime config and token-only usage tracking
- add update_config SDK tool for runtime config changes
- add workspace health diagnostics and /doctor command
- add daemon process management (start/stop/restart/logs)
- fix move bot startup async calls into post_init to avoid closed event loop
- remove litellm dependency — photos/files handled natively
