"""Daemon process management: PID file helpers and process control."""
from __future__ import annotations

import os
import signal
import subprocess
import time

from .workspace import PID_FILE


def read_pid() -> int | None:
    """Return the PID from PID_FILE, or None if missing/stale."""
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_pid(pid: int) -> None:
    """Write pid to PID_FILE."""
    PID_FILE.write_text(str(pid))


def delete_pid() -> None:
    """Remove PID_FILE if it exists."""
    PID_FILE.unlink(missing_ok=True)


def _is_smolclaw_process(pid: int) -> bool:
    """Best-effort check that pid belongs to a smolclaw process."""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        return "smolclaw" in result.stdout.lower()
    except (OSError, subprocess.TimeoutExpired):
        # If ps fails, assume it could be smolclaw (don't delete PID)
        return True


def is_running() -> tuple[bool, int | None]:
    """Return (True, pid) if daemon is alive, (False, None) otherwise."""
    pid = read_pid()
    if pid is None:
        return False, None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        delete_pid()
        return False, None
    except PermissionError:
        # Process exists but owned by another user — treat as running.
        return True, pid

    # PID is alive — verify it's actually smolclaw
    if not _is_smolclaw_process(pid):
        delete_pid()
        return False, None
    return True, pid


def stop_daemon(timeout: int = 10) -> bool:
    """Send SIGTERM to daemon, wait up to timeout seconds. Returns True if stopped."""
    running, pid = is_running()
    if not running or pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        delete_pid()
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            delete_pid()
            return True
        except PermissionError:
            pass  # still alive

    # Force-kill if still alive after timeout
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    delete_pid()
    return True
