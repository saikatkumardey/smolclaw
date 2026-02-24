"""Core tools — smolagents Tool subclasses."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from smolagents import Tool
from smolagents import DuckDuckGoSearchTool, PythonInterpreterTool, VisitWebpageTool, WikipediaSearchTool


def _workspace() -> Path:
    from . import workspace
    return workspace.HOME


_BOT_TOKEN = lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")


class ShellExecTool(Tool):
    name = "shell_exec"
    description = "Run a shell command. Covers git, file ops, system tasks. Returns stdout+stderr."
    inputs = {
        "command": {"type": "string", "description": "Shell command to run"},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)", "nullable": True},
    }
    output_type = "string"

    def forward(self, command: str, timeout: int = 30) -> str:
        try:
            ws = _workspace()
            r = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=ws if ws.exists() else None
            )
            return (r.stdout + r.stderr).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Timeout after {timeout}s"
        except Exception as e:
            return f"Error: {e}"


class FileReadTool(Tool):
    name = "file_read"
    description = "Read a file. offset/limit are line numbers."
    inputs = {
        "path": {"type": "string", "description": "Path to the file"},
        "offset": {"type": "integer", "description": "Start line (default 0)", "nullable": True},
        "limit": {"type": "integer", "description": "Max lines to read (default 200)", "nullable": True},
    }
    output_type = "string"

    def forward(self, path: str, offset: int = 0, limit: int = 200) -> str:
        try:
            lines = Path(path).read_text().splitlines()
            return "\n".join(lines[offset: offset + limit]) or "(empty)"
        except Exception as e:
            return f"Error: {e}"


class FileWriteTool(Tool):
    name = "file_write"
    description = "Write content to a file. Creates parent dirs."
    inputs = {
        "path": {"type": "string", "description": "Path to the file"},
        "content": {"type": "string", "description": "Content to write"},
    }
    output_type = "string"

    def forward(self, path: str, content: str) -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return f"Written {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"






class TelegramSendTool(Tool):
    name = "telegram_send"
    description = "Send a Telegram message to a chat_id. For cron delivery."
    inputs = {
        "chat_id": {"type": "string", "description": "Telegram chat ID"},
        "message": {"type": "string", "description": "Message text (Markdown)"},
    }
    output_type = "string"

    def forward(self, chat_id: str, message: str) -> str:
        try:
            token = _BOT_TOKEN()
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=10,
            )
            return "Sent." if r.ok else f"Failed: {r.text}"
        except Exception as e:
            return f"Error: {e}"


class SaveHandoverTool(Tool):
    name = "save_handover"
    description = "Save a handover note so state survives restart or update. Call before self_restart or self_update."
    inputs = {
        "summary": {"type": "string", "description": "Brief summary of current context, active tasks, and any pending work."},
    }
    output_type = "string"

    def forward(self, summary: str) -> str:
        from .handover import save
        save(summary)
        return "Handover saved."


class SelfRestartTool(Tool):
    name = "self_restart"
    description = "Restart the smolclaw process in-place. Always call save_handover first."
    inputs = {}
    output_type = "string"

    def forward(self) -> str:
        exe = shutil.which("smolclaw") or sys.argv[0]
        args = [exe] + sys.argv[1:] if sys.argv[1:] else [exe, "start"]
        os.execv(exe, args)
        return "unreachable"  # execv replaces the process


class SelfUpdateTool(Tool):
    name = "self_update"
    description = "Pull latest smolclaw from GitHub, reinstall, and restart. Always call save_handover first."
    inputs = {}
    output_type = "string"

    def forward(self) -> str:
        source = os.getenv("SMOLCLAW_SOURCE", "git+https://github.com/saikatkumardey/smolclaw")
        result = subprocess.run(
            ["uv", "tool", "install", "--upgrade", source],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return f"Update failed:\n{result.stderr}"
        exe = shutil.which("smolclaw") or sys.argv[0]
        args = [exe, "start"]
        os.execv(exe, args)
        return "unreachable"


# Instantiate all tools
TOOLS_LIST: list[Tool] = [
    ShellExecTool(),
    FileReadTool(),
    FileWriteTool(),
    TelegramSendTool(),
    SaveHandoverTool(),
    SelfRestartTool(),
    SelfUpdateTool(),
    PythonInterpreterTool(),
    DuckDuckGoSearchTool(),
    VisitWebpageTool(),
]

# Tools that sub-agents are allowed to use (no telegram, no restart/update)
WORKER_TOOL_NAMES = {"shell_exec", "file_read", "file_write", "python_interpreter", "web_search", "visit_webpage"}
WORKER_TOOLS = [t for t in TOOLS_LIST if t.name in WORKER_TOOL_NAMES]


class SpawnTaskTool(Tool):
    name = "spawn_task"
    description = (
        "Run an isolated task in a separate agent. Use for long, independent, or "
        "context-heavy work that would clutter the main conversation. The sub-agent "
        "runs synchronously and returns a result string. It has no access to conversation "
        "history or telegram."
    )
    inputs = {
        "task": {"type": "string", "description": "Clear description of what the sub-agent should do"},
    }
    output_type = "string"

    def forward(self, task: str) -> str:
        import signal
        from smolagents import ToolCallingAgent, LiteLLMModel

        model_id = os.getenv("LITELLM_MODEL", "anthropic/claude-sonnet-4-6")
        worker = ToolCallingAgent(
            tools=list(WORKER_TOOLS),
            model=LiteLLMModel(model_id=model_id),
            max_steps=15,
        )

        # Log sub-agent start
        from .agent import session_log
        session_log("subagent", "system", f"SUBAGENT_START: {task[:200]}")

        # Timeout wrapper
        timeout = int(os.getenv("SMOLCLAW_SUBAGENT_TIMEOUT", "120"))

        def _timeout_handler(signum, frame):
            raise TimeoutError(f"Sub-agent timed out after {timeout} seconds")

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout)
        try:
            result = str(worker.run(task))
        except TimeoutError:
            result = f"Error: sub-agent timed out after {timeout} seconds"
        except Exception as e:
            result = f"Error: {e}"
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

        session_log("subagent", "system", f"SUBAGENT_RESULT: {result[:500]}")
        return result


# Add spawn_task to the main tools list
TOOLS_LIST.append(SpawnTaskTool())
