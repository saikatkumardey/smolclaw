# Building Custom Tools

You can build new tools by writing Python files to the `tools/` directory.

## Convention

Every tool file must have:
1. `SCHEMA` — an OpenAI-style function schema dict
2. `execute(**kwargs) -> str` — the implementation

## Example (`tools/get_weather.py`)

```python
SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"}
            },
            "required": ["city"]
        }
    }
}

def execute(city: str) -> str:
    import requests
    r = requests.get(f"https://wttr.in/{city}?format=3", timeout=5)
    return r.text if r.ok else f"Error: {r.status_code}"
```

## Notes

- Tools are loaded on every message — no restart needed.
- Use `Bash` to install any required packages first (e.g. `uv pip install requests`).
- Tell the user what tool you built and how to use it.
## Calling Claude from a script

If no `ANTHROPIC_API_KEY` is configured (e.g. subscription auth flow), use the `claude` CLI instead of the SDK:

```python
import subprocess, os

env = {**os.environ, "CLAUDECODE": ""}  # required: unsets nested session guard
result = subprocess.run(
    ["claude", "-p", "your prompt here"],
    capture_output=True, text=True, env=env
)
output = result.stdout.strip()
```

The `CLAUDECODE=""` is required — without it, `claude` will refuse to run inside an active Claude Code session.
