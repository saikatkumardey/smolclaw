from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()


def _success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green]  {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow]  {msg}")


_SERVICE_TEMPLATE = """\
[Unit]
Description=SmolClaw Telegram AI Agent
After=network.target

[Service]
Type=simple
WorkingDirectory={workspace_home}
EnvironmentFile={workspace_home}/.env
ExecStart={smolclaw_binary} start --foreground
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

_SYSTEMD_RUNTIME = Path("/run/systemd/system")


def install_systemd_service(workspace_home: Path) -> None:
    if not _SYSTEMD_RUNTIME.exists():
        return

    smolclaw_binary = shutil.which("smolclaw") or sys.executable

    service_content = _SERVICE_TEMPLATE.format(
        workspace_home=workspace_home,
        smolclaw_binary=smolclaw_binary,
    )

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_path = service_dir / "smolclaw.service"

    try:
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path.write_text(service_content)
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "smolclaw"],
            check=True, capture_output=True,
        )
        _success("Systemd user service installed. Run 'systemctl --user start smolclaw' to start.")
        try:
            subprocess.run(
                ["loginctl", "enable-linger", getpass.getuser()],
                check=True, capture_output=True,
            )
        except (PermissionError, subprocess.CalledProcessError):
            _warn("Could not enable linger (needs root). Service won't auto-start on boot unless you run: sudo loginctl enable-linger " + getpass.getuser())
    except (PermissionError, OSError, subprocess.CalledProcessError) as exc:
        _warn(f"Could not install systemd user service automatically ({exc}).")
        _warn(f"To install manually, save the following to {service_path}")
        _warn("then run: systemctl --user daemon-reload && systemctl --user enable smolclaw")
        console.print()
        console.print(Panel(
            service_content,
            title="[dim]smolclaw.service[/dim]",
            border_style="dim yellow",
            padding=(0, 2),
        ))


_WATCHDOG_DEST = Path("/usr/local/bin/smolclaw-watchdog")
_WATCHDOG_CRON = "*/10 * * * * /usr/local/bin/smolclaw-watchdog >> ~/.smolclaw/watchdog.log 2>&1"


def install_watchdog(workspace_home: Path) -> None:
    watchdog_src = Path(__file__).parent / "watchdog.sh"
    if not watchdog_src.exists():
        _warn("watchdog.sh not found in package — skipping watchdog installation.")
        return

    try:
        shutil.copy2(watchdog_src, _WATCHDOG_DEST)
        _WATCHDOG_DEST.chmod(0o755)
    except (PermissionError, OSError) as exc:
        _warn(f"Could not install watchdog script ({exc}). Try: sudo cp {watchdog_src} {_WATCHDOG_DEST} && sudo chmod +x {_WATCHDOG_DEST}")
        return

    try:
        existing = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True,
        )
        current_crontab = existing.stdout if existing.returncode == 0 else ""
        filtered = "\n".join(
            line for line in current_crontab.splitlines()
            if "smolclaw-watchdog" not in line
        )
        new_crontab = (filtered.rstrip("\n") + "\n" + _WATCHDOG_CRON + "\n").lstrip("\n")
        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, "crontab -", proc.stderr)
        _success("Watchdog installed (system cron, every 10 min)")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        _warn(f"Could not install cron entry ({exc}).")
        _warn(f"Add manually: {_WATCHDOG_CRON}")
