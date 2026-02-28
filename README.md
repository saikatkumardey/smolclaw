# SmolClaw

Your personal AI agent. Runs on Telegram. Self-hosted. Powered by Claude.

![SmolClaw setup demo](assets/setup-demo.svg)

## Philosophy

SmolClaw is opinionated. These principles guide every decision.

**Small is the point.** If the codebase grows past what one person can read in an afternoon, something went wrong. Every new file, dependency, and abstraction must justify its existence against deletion.

**The shell is the universal API.** If `Bash` can do it, don't build a tool for it. Don't wrap protocols around what a CLI already handles. The agent can install any tool from a Git repo, learn it, and use it forever. That covers more ground than any plugin system.

**Load what you need, not everything.** Skills exist so the agent can learn on demand. Instructions that aren't needed every turn shouldn't burn tokens every turn. Lazy beats eager.

**One agent is enough.** Don't add orchestration, sub-agents, or multi-agent hierarchies until the single agent genuinely can't handle the task. Complexity is a cost, not a feature.

**The user owns everything.** All data lives in `~/.smolclaw/`. No cloud dependency. No accounts. No telemetry. The user can read, edit, or delete every file the agent touches. If the agent breaks, `rm -rf ~/.smolclaw` and start over.

**Ship over plan.** A working thing today beats a perfect architecture next month.

---

## Setup

### Prerequisites

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
3. Copy the **API token** it gives you (looks like `123456789:ABCdef...`)

### 3. Get your Telegram user ID

1. Message **@userinfobot** on Telegram
2. It replies with your numeric user ID (e.g. `123456789`)

### 4. Run the setup wizard

```bash
smolclaw setup
```

This asks for your bot token, your Telegram user ID, and saves them to `~/.smolclaw/.env`. Takes about 2 minutes.

### 5. Authenticate with Claude

```bash
smolclaw setup-token
```

Two options:

| | Option | When to use |
|---|---|---|
| **1** | Paste API key | You have an [Anthropic API key](https://console.anthropic.com/settings/keys) |
| **2** | Login with Claude account | You have Claude Pro / Max / Team (opens browser) |

### 6. Start the bot

```bash
smolclaw start
```

Your agent is now live. Open Telegram and message your bot.

---

## First conversation

The agent doesn't know you yet. On first message it runs an **onboarding flow**:

```
You:    hi
Agent:  Hey! I'm your personal AI agent — I don't have a name yet, and I
        don't know yours either. I'd love to learn a bit about you so I can
        actually be useful. What's your name?

You:    Saikat, based in Singapore
Agent:  Nice to meet you, Saikat! Singapore timezone — got it (SGT, UTC+8).
        What kind of things would you like help with day-to-day?

You:    coding projects, research, and reminders
Agent:  Perfect. Last question — what would you like to call me?

You:    Claw
Agent:  Claw it is. I've saved your name, timezone, and preferences.
        What's first?
```

After onboarding the agent writes what it learned to `USER.md`, `SOUL.md`, and `MEMORY.md`. It carries this forward across every future session.

---

## Bot commands

| Command | What it does |
|---------|-------------|
| `/start` | Wake the bot |
| `/help` | Show available commands |
| `/status` | Show model, workspace path, tool/skill counts |
| `/reset` | Clear conversation history and start fresh |
| `/reload` | Hot-reload skills and memory (no restart needed) |

Just talk to the bot for everything else. No slash commands needed.

---

## What you can ask

```
# Files and code
"read ~/projects/api/main.py and summarise what it does"
"write a Python script to rename all .jpeg files to .jpg in ~/Downloads"

# Shell
"what processes are using port 8080"
"show me disk usage for my home directory"

# Web
"search for the latest release of ripgrep and tell me what changed"
"fetch https://example.com/api/docs and summarise the endpoints"

# Memory
"remember that I prefer dark mode and tabs over spaces"
"what do you remember about my coding preferences?"

# Learning tools
"learn to use https://github.com/sharkdp/fd"
# → clones, reads docs, installs, writes a skill, confirms

# Custom tools
"build me a tool that checks the weather for Singapore"
# → writes ~/.smolclaw/tools/get_weather.py, loads on next message

# Scheduled tasks
"every morning at 8am SGT send me a summary of my top 3 priorities"
# → writes a cron job to crons.yaml
```

---

## File structure

All agent data lives in `~/.smolclaw/` — separate from the installed package.

```
~/.smolclaw/
├── .env              ← credentials (Telegram token, API key)
├── SOUL.md           ← personality, identity, operating instructions
├── USER.md           ← your name, timezone, preferences (filled on first boot)
├── MEMORY.md         ← long-term memory (grows over time)
├── skills/           ← learned behaviors (Markdown docs)
├── tools/            ← custom tools (Python files)
├── sessions/         ← JSONL conversation logs by date
└── crons.yaml        ← scheduled jobs and heartbeat config
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
               │                   self_update  spawn_task
               └── Custom tools:   ~/.smolclaw/tools/*.py  (hot-reloaded)

scheduler.py ──► cron jobs in crons.yaml ──► agent.py ──► Telegram
```

Messages are kept per `chat_id` for multi-turn context. Custom tools drop in as `.py` files with `SCHEMA` + `execute()` — no restart needed. Skills (`~/.smolclaw/skills/*/SKILL.md`) are injected into the system prompt on every request.

---

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TELEGRAM_BOT_TOKEN` | — | Required. Set by `smolclaw setup`. |
| `ALLOWED_USER_IDS` | (all) | Your Telegram user ID. Set by `smolclaw setup`. |
| `ANTHROPIC_API_KEY` | — | API key auth. Set by `smolclaw setup-token`. |
| `LITELLM_MODEL` | `anthropic/claude-sonnet-4-6` | Vision model for photo messages only. |
| `SMOLCLAW_HOME` | `~/.smolclaw` | Override workspace directory. |
| `SMOLCLAW_SOURCE` | GitHub URL | Source repo for `self_update`. |
| `SMOLCLAW_SUBAGENT_TIMEOUT` | `120` | Sub-agent timeout in seconds. |

---

## Updating

```bash
# From within Telegram
"update yourself"

# Or from the terminal
smolclaw update
```

The agent saves a handover note before restarting so it picks up where it left off.

---

## Backlog

- **Streaming responses** — stream partial replies to Telegram as they arrive
- **Auto-memory trimming** — prune stale MEMORY.md entries when approaching the limit

---

## License

MIT
