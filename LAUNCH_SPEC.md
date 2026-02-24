# SmolClaw Launch Spec

What needs to happen before v1.0.0.

## Bugs (must fix)

### 1. README inaccuracies
- Description says "SQLite history" — we deleted history.py. Remove smolclaw.db from file structure.
- pyproject.toml description says "6 tools" — we have 10.
- "What it does" section still says "Remembers you across sessions (SQLite history + MEMORY.md)" — rewrite.

### 2. Tests are broken
- `tests/test_tools.py` uses the old function-style API (`shell_exec("echo hello")`). Tools are now smolagents Tool subclasses with `.forward()`. Fix all tests.

### 3. No LICENSE file
- README says MIT but there's no LICENSE file in the repo.

### 4. Heartbeat deliver_to is empty
- `templates/crons.yaml` has `deliver_to: ""` on the heartbeat job. If someone skips setup or installs manually, the heartbeat silently does nothing. The scheduler should fall back to `ALLOWED_USER_IDS` from .env (it does via `DEFAULT_CHAT`, but only if deliver_to is falsy — verify this works).

### 5. No --version flag
- `smolclaw --version` doesn't work. Typer supports `typer.main.get_command()` with version callback. Add it.

## Missing for launch (must add)

### 6. Proper test suite
- Tool tests: each of the 10 tools gets a basic test (mock external calls)
- Agent test: mock LiteLLMModel, verify agent.run() returns a string
- Setup test: verify templates copy correctly on workspace.init()
- Skill loader test: verify skills load from a temp dir
- Tool loader test: verify dynamic tools load from a temp dir

### 7. GitHub Actions CI
- Run tests on push/PR
- Python 3.12+
- `uv sync && uv run pytest`

### 8. Error handling in main.py
- Missing .env: clear error message pointing to `smolclaw setup`
- Missing bot token: don't crash with a traceback, print a human message
- Missing API key: same — catch on first message, tell user to set it
- Network errors: retry logic or at least graceful failure message to user

### 9. Version bump
- Bump to 0.2.0 for this PR (major refactor from litellm to smolagents)
- Set up for 1.0.0 once tests pass and CI is green

### 10. Bot commands
- `/help` — show what the agent can do (tools list, skill count, uptime)
- `/status` — show model, workspace path, tool count, memory size
- `/reset` — clear agent memory for current chat (reset the cached agent instance)

## Nice to have (post-launch)

### 11. Streaming responses
- Telegram supports `editMessageText` for progressive updates
- Stream partial agent output, edit message every N tokens
- Backlog item — not blocking launch

### 12. Image/photo handling
- Telegram sends photos as file IDs
- Download, pass to vision model if available, otherwise describe
- Backlog — not blocking launch

### 13. PyPI publishing
- `uv build && uv publish` workflow
- GitHub Actions release workflow on tag push
- So users can `uv tool install smolclaw` from PyPI instead of git

### 14. Rate limiting / token budget
- Optional daily token budget in .env
- Warn user when approaching limit
- Backlog — not blocking launch

### 15. Graceful shutdown
- Handle SIGTERM/SIGINT in main.py
- Save handover on shutdown
- Clean up scheduler

## Launch checklist

- [x] Fix README inaccuracies (#1)
- [x] Fix tests (#2)
- [x] Add LICENSE file (#3)
- [x] Verify heartbeat fallback (#4)
- [x] Add --version (#5)
- [x] Write proper test suite (#6)
- [x] Add GitHub Actions CI (#7)
- [x] Add error handling in main.py (#8)
- [x] Bump version to 0.2.0 (#9)
- [x] Add bot commands (#10)
- [x] Merge PR #1
- [x] Tag v0.2.0
