# SmolClaw

Your personal AI agent. Runs on Telegram. Self-hosted.

## Philosophy

SmolClaw is opinionated. These principles guide every decision.

**Small is the point.** If the codebase grows past what one person can read in an afternoon, something went wrong. Every new file, dependency, and abstraction must justify its existence against deletion.

**The shell is the universal API.** If `shell_exec` can do it, don't build a tool for it. Don't wrap protocols around what a CLI already handles. The agent can install any tool from a Git repo, learn it, and use it forever. That covers more ground than any plugin system.

**Load what you need, not everything.** Skills exist so the agent can learn on demand. Instructions that aren't needed every turn shouldn't burn tokens every turn. Lazy beats eager.

**One agent is enough.** Don't add orchestration, sub-agents, or multi-agent hierarchies until the single agent genuinely can't handle the task. Complexity is a cost, not a feature.

**The user owns everything.** All data lives in `~/.smolclaw/`. No cloud dependency. No accounts. No telemetry. The user can read, edit, or delete every file the agent touches. If the agent breaks, `rm -rf ~/.smolclaw` and start over.

**Fewer files, fewer problems.** Three workspace files (SOUL, USER, MEMORY) beat seven. One config (.env) beats three. Merge before you split.

**Ship over plan.** A working thing today beats a perfect architecture next month. If it takes more than a day to build, it's too big. Break it down or cut scope.

![SmolClaw setup wizard](assets/setup-demo.svg)

## Quickstart

```bash
# Install
pip install uv
uv tool install git+https://github.com/saikatkumardey/smolclaw

# Setup (2 min wizard — bot token, user ID, API key)
smolclaw setup

# Run
smolclaw start
```

That's it. Your agent is live on Telegram.

---

## Architecture

![smolclaw architecture](assets/architecture.svg)

## What it does

- Remembers you across sessions (MEMORY.md)
- Learns your name and preferences on first boot
- Runs shell commands, reads/writes files, searches the web
- Learns any CLI tool: point it at a GitHub repo, it installs and remembers
- Builds custom skills and tools through conversation

## Learn a new CLI tool

```
You: learn to use https://github.com/saikatkumardey/teleport-scanner
Agent: clones repo → reads README → installs → writes skill → confirms
```

Next session the agent already knows how to use it.

## Build a custom skill

```
You: remember how to check my server uptime every morning
Agent: writes skills/server-uptime/SKILL.md with exact steps
```

## Build a custom tool

```
You: build me a tool that checks the weather
Agent: writes tools/get_weather.py with SCHEMA + execute()
Tool loads automatically on next message
```

## File structure

All agent data lives in `~/.smolclaw/` — separate from the installed package.

```
~/.smolclaw/
├── .env              ← credentials (written by smolclaw setup)
├── SOUL.md           ← personality, identity, operating instructions, heartbeat rules
├── USER.md           ← your preferences (filled on first boot)
├── MEMORY.md         ← long-term memory (grows over time)
├── skills/           ← learned behaviors (markdown)
├── tools/            ← custom tools (Python)
└── crons.yaml        ← scheduled jobs + heartbeat
```

Override with `SMOLCLAW_HOME=/path/to/dir` if needed.

## Models

Supports any model via LiteLLM — Anthropic, OpenAI, Groq, Ollama, and more.
Set in `.env` as `LITELLM_MODEL=anthropic/claude-sonnet-4-6`.

## Backlog

Things worth building when a real use case demands them:

- **Sub-agents** — smolagents' `ManagedAgent` lets the main agent delegate to specialists. Useful for parallel research + build workflows. Not needed until single-agent hits a wall.
- **Streaming responses** — stream partial replies to Telegram instead of waiting for full completion
- **Auto-memory trimming** — prune stale MEMORY.md entries automatically when approaching 80 lines

## License

MIT
