# SmolClaw

[![python-doctor](https://img.shields.io/badge/python--doctor-84%2F100-green)](https://github.com/saikatkumardey/python-doctor)

Personal AI agent on Telegram. Self-hosted, powered by Claude.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/saikatkumardey/smolclaw/main/install.sh | bash
```

Or manually:

```bash
pip install uv
uv tool install git+https://github.com/saikatkumardey/smolclaw
smolclaw setup
```

Setup asks for your Telegram bot token, user ID, and Claude auth. Get a bot token from [@BotFather](https://t.me/BotFather) and your user ID from [@userinfobot](https://t.me/userinfobot).

## Run

```bash
smolclaw start       # background daemon
smolclaw start -f    # foreground
smolclaw logs -f     # stream logs
smolclaw stop
smolclaw restart
```

On Linux with systemd, `smolclaw setup` installs and starts the service automatically.

## Commands

| Command | |
|---------|--|
| `/status` | Model, tools, token usage |
| `/models` | Switch Claude model |
| `/effort` | Switch thinking effort |
| `/reset` | Clear conversation history |
| `/cancel` | Cancel running task |
| `/restart` | Restart the bot |
| `/update` | Pull latest and restart |
| `/context` | Context window usage |

## Workspace

Everything lives in `~/.smolclaw/`:

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent identity and instructions |
| `USER.md` | Your profile (name, timezone, preferences) |
| `MEMORY.md` | Persistent memory across sessions |
| `crons.yaml` | Scheduled jobs |
| `skills/*/SKILL.md` | Skill docs injected into system prompt |
| `tools/*.py` | Custom tools — hot-loaded, no restart needed |
| `sessions/*.jsonl` | Conversation logs |
| `handover.md` | State snapshot across restarts |

Override the workspace path: `SMOLCLAW_HOME=/path/to/dir`

## Custom tools

Drop a `.py` file in `~/.smolclaw/tools/` with a `SCHEMA` dict and `execute()` function. Available on the next message, no restart needed.

## Update

```bash
smolclaw update       # from terminal
/update               # from Telegram
```

Saves a handover note before restarting and picks up where it left off.

## License

MIT
