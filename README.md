# SmolClaw

Your personal AI agent. Runs on Telegram. Self-hosted. Powered by Claude.

## Why this exists

Most AI assistant setups are either too heavy (a whole platform with accounts and dashboards) or too thin (a chatbot that forgets everything between sessions). SmolClaw sits in the middle: one process, one Telegram bot, everything in files you can read and edit yourself.

It remembers you across sessions, runs cron jobs, writes custom tools on the fly, and can update itself. You own the data. If something breaks, you can read the source in an afternoon and fix it.

## Philosophy

**Small is the point.** If the codebase grows past what one person can read in an afternoon, something went wrong. Every new file, dependency, and abstraction must justify its existence against deletion.

**The shell is the universal API.** If `Bash` can do it, don't build a tool for it. The agent can install any tool from a Git repo, learn it, and use it. That covers more ground than any plugin system.

**Load what you need, not everything.** Skills exist so the agent can learn on demand. Instructions that aren't needed every turn shouldn't burn tokens every turn.

**Start with one agent.** The main session handles most things. Sub-agents exist (`spawn_task`) but as a last resort — for long-running background work that would block the main conversation. Default to one agent, reach for two only when you have a specific reason.

**The user owns everything.** All data lives in `~/.smolclaw/`. No cloud dependency, no accounts, no telemetry. You can read, edit, or delete every file the agent touches. If it breaks, `rm -rf ~/.smolclaw` and start over.

**Ship over plan.** A working thing today beats a perfect architecture next month.

---

## Setup

### What you need

- Python 3.12+
- A Telegram account
- A Claude account — API key **or** Claude Pro/Max subscription

### 1. Install

```bash
pip install uv
uv tool install git+https://github.com/saikatkumardey/smolclaw
```

### 2. Create a Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the API token it gives you (looks like `123456789:ABCdef...`)

### 3. Get your Telegram user ID

Message **@userinfobot** on Telegram. It replies with your numeric user ID.

### 4. Run setup

```bash
smolclaw setup
```

Asks for your bot token, Telegram user ID, and Claude auth. On Linux systems with systemd, also installs and enables the service automatically. Takes about two minutes.

### 5. Start the bot

```bash
# If systemd service was installed (Linux):
systemctl start smolclaw
systemctl status smolclaw

# Or manually:
smolclaw start              # background daemon
smolclaw start --foreground # foreground (useful for debugging)
smolclaw logs               # view output
smolclaw logs --follow      # stream live (Ctrl-C to stop)
smolclaw stop               # stop the daemon
smolclaw restart            # stop + start
```

Open Telegram and message your bot.

---

## First conversation

The agent doesn't know you yet. It runs a short onboarding on first message:

```
You:    hi
Agent:  Hey! I'm your personal AI agent — I don't have a name yet, and I
        don't know yours either. What's your name?

You:    Saikat, based in India
Agent:  Nice to meet you, Saikat! What timezone are you in?

You:    IST, UTC+5:30
Agent:  Got it. What kind of things would you like help with day-to-day?

You:    coding projects, research, and reminders
Agent:  Perfect. What would you like to call me?

You:    Claw
Agent:  Claw it is. I've saved your name, timezone, and preferences.
        What's first?
```

It writes what it learns to `USER.md`, `SOUL.md`, and `MEMORY.md` and carries that forward across every session.

---

## What you can ask

```
# Files and code
"read ~/projects/api/main.py and tell me what it does"
"write a script to rename all .jpeg files to .jpg in ~/Downloads"

# Shell
"what processes are using port 8080"
"show me disk usage for my home directory"

# Web
"search for the latest release of ripgrep and tell me what changed"
"fetch https://example.com/api/docs and summarise the endpoints"

# Memory
"remember that I prefer dark mode and tabs over spaces"
"what do you know about my coding preferences?"

# Learning tools
"learn to use https://github.com/sharkdp/fd"
# → clones, reads docs, installs, writes a skill, confirms

# Custom tools
"build me a tool that checks the weather for my city"
# → writes ~/.smolclaw/tools/get_weather.py, available on next message

# Scheduled tasks
"every morning at 8am send me a summary of my top 3 priorities"
# → writes a cron job to crons.yaml
```

---

## Bot commands

| Command | What it does |
|---------|-------------|
| `/status` | Model, workspace path, tool counts, last turn cost |
| `/reset` | Clear conversation history and start fresh |
| `/models` | Switch the Claude model |
| `/help` | Show available commands |

For everything else, just talk to the bot. No slash commands needed.

---

Override the workspace path with `SMOLCLAW_HOME=/path/to/dir`.

---

## Architecture

```
  systemd (Restart=always)
         │  auto-restarts on crash or stop
         ▼
  smolclaw start --foreground
         │  writes ~/.smolclaw/.pid
         │  stdout/stderr → ~/.smolclaw/smolclaw.log
         ▼
  ┌──────────────────────────────────────────────────────┐
  │                  main.py  (daemon)                   │
  │                                                      │
  │  Telegram ──► handlers.py ──► agent.run(chat_id)     │
  │                                     │                │
  │                               agent.py               │
  │                     ClaudeSDKClient (one per chat)    │
  │                               │                      │
  │          ┌────────────────────┼─────────────────┐    │
  │          │                    │                  │    │
  │   Built-in tools        MCP tools          Custom tools │
  │   Bash  Read  Write     telegram_send      tools/*.py   │
  │   WebSearch  WebFetch   spawn_task         (hot-reload) │
  │   Glob  Grep            save_handover                │  │
  │                         self_restart                 │  │
  │                         self_update                  │  │
  │                               │                      │  │
  │                          spawn_task                  │  │
  │                               │                      │  │
  │                          sub-agent                   │  │
  │                      (isolated, background)          │  │
  │                      result → Telegram               │  │
  │                                                      │  │
  │  scheduler.py ──► crons.yaml ──► agent.run ──► Telegram │
  └──────────────────────────────────────────────────────┘

  ~/.smolclaw/
  ├── .env              ← secrets (bot token, API key)
  ├── .pid              ← daemon PID
  ├── smolclaw.log      ← stdout/stderr log
  ├── smolclaw.json     ← runtime config (model, timeouts)
  ├── SOUL.md           ← agent identity and operating instructions
  ├── USER.md           ← your profile (name, timezone, preferences)
  ├── MEMORY.md         ← long-term memory, persists across sessions
  ├── HEARTBEAT.md      ← instructions for the 30-min heartbeat cron
  ├── crons.yaml        ← scheduled jobs (loaded on startup)
  ├── handover.md       ← state snapshot across restarts (read once, deleted)
  ├── skills/           ← per-skill SKILL.md files, injected at session start
  ├── tools/            ← custom tools as .py files (SCHEMA + execute())
  ├── sessions/         ← JSONL conversation logs by date
  ├── memory/           ← daily notes and long-form memory files
  └── uploads/          ← files sent via Telegram
```

**How it hangs together:**

- **systemd** manages the process lifecycle. `smolclaw setup` writes the service file with `Restart=always` so the agent recovers from crashes automatically.
- **scheduler.py** reads `crons.yaml` on startup and registers all jobs in-process. Each job runs as a short agent session and can send Telegram messages.
- **agent.py** holds one `ClaudeSDKClient` per chat. Sessions persist across messages for multi-turn context.
- **Custom tools** are `.py` files with a `SCHEMA` dict and `execute()` function. Drop one in `tools/` and it's available on the next message — no restart needed.
- **Skills** are markdown files in `skills/*/SKILL.md`. They're injected into the system prompt at session start, so the agent knows how to use installed CLIs, APIs, or workflows you've taught it.
- **spawn_task** runs a fully isolated sub-agent in the background. It returns immediately; results arrive via Telegram when the task completes. Use it for anything that would take more than a few tool calls.

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | — | Required. Set by `smolclaw setup`. |
| `ALLOWED_USER_IDS` | — | Your Telegram user ID. Set by `smolclaw setup`. |
| `ANTHROPIC_API_KEY` | — | API key auth. Set by `smolclaw setup-token`. |
| `SMOLCLAW_MODEL` | `claude-sonnet-4-6` | Active Claude model. |
| `SMOLCLAW_HOME` | `~/.smolclaw` | Override workspace directory. |
| `SMOLCLAW_SOURCE` | GitHub URL | Source repo for self-update. |
| `SMOLCLAW_SUBAGENT_TIMEOUT` | `120` | Background task timeout in seconds. |

---

## Updating

```bash
# From within Telegram
"update yourself"

# Or from the terminal
smolclaw update
```

The agent saves a handover note before restarting and picks up where it left off.

---

## License

MIT
