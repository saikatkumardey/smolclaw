# Changelog

All notable changes to smolclaw will be documented in this file.

## [0.7.6] - 2026-03-20

- docs: add release workflow to CLAUDE.md conventions
- add Makefile with one-shot release command
- Clean up CC output: text + compact tool lines, skip tool results
- Add CC footer to every message showing session status
- CC mode takes over all messages, blocks while busy
- ci: update python-doctor badge to 90/100
- Fix /cc continue: use --resume flag, show stderr on errors
- Bump version to 0.7.2
- ci: update python-doctor badge to 91/100
- Format /cc output with HTML for Telegram (bold tools, code blocks, icons)
- Auto-bypass permissions in /cc to prevent hanging on approval prompts
- Bump version to 0.7.1
- Switch /cc from acpx to direct claude CLI stream-json
- Remove heartbeat from templates and description
- Bump version to 0.7.0
- ci: update python-doctor badge to 92/100
- Add /cc command for live Claude Code sessions via ACP
- Build rich handover from session logs on /restart
- Remove heartbeat — redundant with subconscious, always timed out
- Add subconscious feature to README
