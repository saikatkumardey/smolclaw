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

Asks for your bot token and Telegram user ID, saves them to `~/.smolclaw/.env`. Takes about two minutes.

### 5. Authenticate with Claude

```bash
smolclaw setup-token
```

| Option | When to use |
|--------|------------|
| Paste API key | You have an [Anthropic API key](https://console.anthropic.com/settings/keys) |
| Login with Claude account | You have Claude Pro / Max / Team |

### 6. Start the bot

```bash
smolclaw start
```

Open Telegram and message your bot.

---

## First conversation

The agent doesn't know you yet. It runs a short onboarding on first message:

```
You:    hi
Agent:  Hey! I'm your personal AI agent — I don't have a name yet, and I
        don't know yours either. What's your name?

You:    Saikat, based in Singapore
Agent:  Nice to meet you, Saikat! Singapore timezone — got it (SGT, UTC+8).
        What kind of things would you like help with day-to-day?

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
"build me a tool that checks the weather in Singapore"
# → writes ~/.smolclaw/tools/get_weather.py, available on next message

# Scheduled tasks
"every morning at 8am SGT send me a summary of my top 3 priorities"
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

## File structure

Everything lives in `~/.smolclaw/` — separate from the installed package:

```
~/.smolclaw/
├── .env              ← credentials (Telegram token, API key)
├── SOUL.md           ← personality, identity, operating instructions
├── USER.md           ← your name, timezone, preferences
├── MEMORY.md         ← long-term memory (grows over time)
├── skills/           ← learned behaviors (Markdown docs)
├── tools/            ← custom tools (Python files, hot-reloaded)
├── sessions/         ← JSONL conversation logs by date
├── uploads/          ← files you send to the bot
└── crons.yaml        ← scheduled jobs
```

Override the workspace path with `SMOLCLAW_HOME=/path/to/dir`.

---

## Architecture

```
Telegram
   │
   ▼
main.py ──► agent.py (ClaudeSDKClient, one per chat)
               │
               ├── Built-in tools: Bash  Read  Write  WebSearch  WebFetch
               ├── SDK tools:      telegram_send  save_handover  self_restart
               │                   self_update  spawn_task  telegram_send_file
               └── Custom tools:   ~/.smolclaw/tools/*.py  (hot-reloaded)

scheduler.py ──► crons.yaml ──► agent.py ──► Telegram
```

Sessions persist per `chat_id` for multi-turn context. Custom tools are `.py` files with `SCHEMA` + `execute()` — drop them in and they're available on the next message, no restart needed. Skills (`~/.smolclaw/skills/*/SKILL.md`) are injected into the system prompt when the session starts.

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
