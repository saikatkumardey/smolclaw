# CLI Tool Learning Protocol

You can learn to use any CLI tool by pointing at its GitHub repo. No MCP servers. No connectors. Just CLIs.

When the user says "learn to use <repo>" or "install <url>":

## Step 1 — Clone and inspect

```
shell_exec("git clone <url> /tmp/<name> --depth 1")
shell_exec("cat /tmp/<name>/README.md")
shell_exec("cat /tmp/<name>/README* /tmp/<name>/docs/*.md 2>/dev/null | head -200")
```

## Step 2 — Figure out how to install

Check pyproject.toml / setup.py → use `uv tool install` or `uv pip install`
Check Cargo.toml → use `cargo install`
Check go.mod → use `go install`
Check package.json → use `npm install -g`
Binary releases → download from GitHub releases
When in doubt: `shell_exec("cd /tmp/<name> && cat pyproject.toml setup.py Makefile 2>/dev/null")`

## Step 3 — Install it

```
shell_exec("uv tool install /tmp/<name>")   # Python
shell_exec("cargo install --path /tmp/<name>")  # Rust
```

## Step 4 — Verify and explore

```
shell_exec("<tool> --help")
shell_exec("<tool> <subcommand> --help")
```

## Step 5 — Write a skill

Create `skills/<tool-name>/SKILL.md` with:
- What the tool does (1 sentence)
- How to install (exact command)
- Key commands with examples
- Common flags and options
- Any gotchas or prerequisites

## Step 6 — Confirm to the user

Tell them: tool installed, skill written, ready to use.

This works for ANY CLI — Python, Rust, Go, Node, shell scripts. The skill persists across sessions so you never have to re-learn it.
