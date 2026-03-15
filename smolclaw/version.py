"""Shared version utilities — single source of truth for version checks and update summaries."""
from __future__ import annotations

import importlib.metadata
import re
import subprocess
from pathlib import Path


def local_version() -> str:
    """Get installed smolclaw version, with fallbacks for uv tool installs."""
    try:
        return importlib.metadata.version("smolclaw")
    except Exception:
        pass
    # Fallback: parse from uv tool list
    try:
        result = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            if "smolclaw" in line.lower():
                m = re.search(r"v?(\d+\.\d+\.\d+)", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    # Fallback: read pyproject.toml directly (works in dev)
    try:
        toml = Path(__file__).parent.parent / "pyproject.toml"
        if toml.exists():
            m = re.search(r'version\s*=\s*"([^"]+)"', toml.read_text())
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"


def check_remote_version(source: str) -> str | None:
    """Check the latest version from GitHub pyproject.toml. Returns version string or None."""
    try:
        import requests
        repo_match = re.search(r"github\.com/([^/]+/[^/.\s]+)", source)
        if not repo_match:
            return None
        repo = repo_match.group(1).rstrip(".git")
        resp = requests.get(
            f"https://raw.githubusercontent.com/{repo}/main/pyproject.toml",
            timeout=10,
        )
        if resp.status_code == 200:
            m = re.search(r'version\s*=\s*"([^"]+)"', resp.text)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def get_update_summary(source: str, old_version: str) -> str:
    """Get version transition and changelog after a successful update."""
    # Get new version from the freshly installed binary
    new_version = None
    try:
        ver_result = subprocess.run(
            ["uv", "tool", "run", "smolclaw", "--", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if ver_result.returncode == 0:
            m = re.search(r"(\d+\.\d+\.\d+)", ver_result.stdout)
            if m:
                new_version = m.group(1)
    except Exception:
        pass

    if not new_version:
        try:
            list_result = subprocess.run(
                ["uv", "tool", "list"], capture_output=True, text=True, timeout=10,
            )
            for line in list_result.stdout.splitlines():
                if "smolclaw" in line.lower():
                    m = re.search(r"v?(\d+\.\d+\.\d+)", line)
                    if m:
                        new_version = m.group(1)
                    break
        except Exception:
            pass

    new_version = new_version or "unknown"

    # Get recent commits from GitHub
    changes = []
    try:
        repo_match = re.search(r"github\.com/([^/]+/[^/.\s]+)", source)
        if repo_match:
            repo = repo_match.group(1).rstrip(".git")
            import requests
            resp = requests.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"per_page": "10"},
                timeout=10,
            )
            if resp.status_code == 200:
                for commit in resp.json():
                    msg = commit.get("commit", {}).get("message", "").split("\n")[0]
                    if msg and not msg.startswith("bump version"):
                        changes.append(f"- {msg}")
                        if len(changes) >= 5:
                            break
    except Exception:
        pass

    parts = [f"{old_version} -> {new_version}"]
    if changes:
        parts.append("\nRecent changes:\n" + "\n".join(changes))
    return "\n".join(parts)
