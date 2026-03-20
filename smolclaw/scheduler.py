"""APScheduler + crons.yaml."""
from __future__ import annotations

import asyncio
import os
import threading

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from . import workspace
from .auth import default_chat_id
from .tools import TelegramSender

# Skip the `claude -v` subprocess the SDK spawns before every connect().
# Cron jobs run frequently and the version doesn't change between runs.
os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

_telegram = TelegramSender()

HEARTBEAT_OK = "HEARTBEAT_OK"
SUBCONSCIOUS_OK = "SUBCONSCIOUS_OK"


_CRON_TIMEOUT_SECONDS = 300  # 5 minutes max per cron job
_SUBCONSCIOUS_TIMEOUT_SECONDS = 600  # 10 minutes — subprocess boot + MCP init + multi-turn tool loop


def _run_agent_in_thread(job_id: str, prompt: str, timeout: int) -> tuple[str | None, Exception | None]:
    """Run agent in a separate thread with timeout. Returns (result, exception)."""
    from .agent import run

    result_holder: list[str] = []
    exc_holder: list[Exception] = []

    def _thread_target() -> None:
        try:
            result_holder.append(asyncio.run(run(chat_id=f"cron:{job_id}", user_message=prompt)))
        except Exception as e:
            exc_holder.append(e)

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        return None, TimeoutError(f"timed out after {timeout}s")
    if exc_holder:
        return None, exc_holder[0]
    return (result_holder[0] if result_holder else "(no response)"), None


def _should_suppress_result(job_id: str, result: str, heartbeat: bool) -> bool:
    """Return True if this cron result should not be delivered to the user."""
    if heartbeat and HEARTBEAT_OK in result:
        return True
    if job_id == "subconscious" and SUBCONSCIOUS_OK in result:
        return True
    return result == "(no response)"


def _run_job(job_id: str, prompt: str, deliver_to: str, heartbeat: bool = False, timeout: int | None = None) -> None:
    if timeout is None:
        timeout = _CRON_TIMEOUT_SECONDS
    logger.info("Cron: {}", job_id)

    result, exc = _run_agent_in_thread(job_id, prompt, timeout)

    if isinstance(exc, TimeoutError):
        logger.error("Cron {} timed out after {}s — thread abandoned (daemon, will die on exit)", job_id, timeout)
        if deliver_to:
            _telegram.send(chat_id=deliver_to, message=f"Cron '{job_id}' timed out after {timeout}s.")
        return

    if exc is not None:
        logger.error("Cron {} failed: {}", job_id, exc)
        if deliver_to:
            _telegram.send(chat_id=deliver_to, message=f"Cron '{job_id}' failed: {exc}")
        return

    if not _should_suppress_result(job_id, result, heartbeat) and deliver_to:
        _telegram.send(chat_id=deliver_to, message=result)


_HEARTBEAT_TIMEOUT_SECONDS = 120  # 2 minutes — heartbeat should be fast
_last_heartbeat_mtime: float = 0  # tracks when we last ran a heartbeat

_HEARTBEAT_WATCHED_FILES = ("MEMORY.md", "USER.md", "subconscious.yaml")

_HEARTBEAT_PROMPT = (
    "HEARTBEAT_CHECK. Read HEARTBEAT.md and decide if there is anything worth telling the user.\n"
    "If yes: call telegram_send with a short message, then reply HEARTBEAT_OK.\n"
    "If no: reply HEARTBEAT_OK only. Do not send a message."
)


def _heartbeat_has_changes() -> bool:
    """Check if any watched files or session logs changed since last heartbeat."""
    global _last_heartbeat_mtime
    if _last_heartbeat_mtime == 0:
        return True  # first run — always check

    # Check watched files
    for name in _HEARTBEAT_WATCHED_FILES:
        path = workspace.HOME / name
        try:
            if path.stat().st_mtime > _last_heartbeat_mtime:
                return True
        except FileNotFoundError:
            continue

    # Check session logs for new activity
    sessions_dir = workspace.HOME / "sessions"
    if sessions_dir.is_dir():
        for f in sessions_dir.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime > _last_heartbeat_mtime:
                    return True
            except (OSError, FileNotFoundError):
                continue

    return False


def _run_heartbeat() -> None:
    """Run a heartbeat check — but only invoke the model if something changed."""
    global _last_heartbeat_mtime

    if not _heartbeat_has_changes():
        logger.debug("Heartbeat: nothing changed, skipping model call")
        return

    deliver_to = default_chat_id()
    _run_job("heartbeat", _HEARTBEAT_PROMPT, deliver_to, heartbeat=True, timeout=_HEARTBEAT_TIMEOUT_SECONDS)
    _last_heartbeat_mtime = __import__("time").time()


def _run_subconscious() -> None:
    """Run a subconscious reflection cycle."""
    from .config import Config
    cfg = Config.load()
    if not cfg.get("subconscious_enabled", True):
        logger.debug("Subconscious disabled via config")
        return

    from . import subconscious
    threads = subconscious.load_threads()

    # Read tail of recent session logs
    sessions_dir = workspace.HOME / "sessions"
    recent_logs = ""
    if sessions_dir.exists():
        tail_bytes = 4000
        log_parts = []
        for f in sorted(sessions_dir.glob("*.jsonl"), reverse=True)[:3]:
            try:
                size = f.stat().st_size
                if size == 0:
                    continue
                with open(f, "rb") as fh:
                    if size > tail_bytes:
                        fh.seek(size - tail_bytes)
                        fh.readline()  # skip partial first line
                    log_parts.append(fh.read().decode("utf-8", errors="replace"))
            except Exception:
                continue
        recent_logs = "\n".join(log_parts)[:8000]

    memory = workspace.read(workspace.MEMORY)
    prompt = subconscious.build_prompt(threads, recent_logs, memory)
    deliver_to = default_chat_id()
    _run_job("subconscious", prompt, deliver_to, heartbeat=False, timeout=_SUBCONSCIOUS_TIMEOUT_SECONDS)


def _cleanup_stale_files() -> None:
    """Delete screenshots and uploads older than 7 days."""
    import time
    cutoff = time.time() - 7 * 86400
    for dirname in ("screenshots", "uploads"):
        d = workspace.HOME / dirname
        if not d.is_dir():
            continue
        for f in d.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except Exception:
                continue


def _cleanup_idle_browsers() -> None:
    """Close browser contexts that have been idle for too long."""
    try:
        from .browser import BrowserManager
        mgr = BrowserManager.get()
        # Only run if browser has been used (avoid importing Playwright needlessly)
        if mgr._contexts:
            asyncio.run(mgr.cleanup_idle())
    except Exception as e:
        logger.debug("Browser cleanup skipped: {}", e)


def _schedule_builtin_jobs(scheduler: BackgroundScheduler) -> None:
    """Add built-in periodic jobs (cleanup, heartbeat, subconscious)."""
    scheduler.add_job(
        _cleanup_idle_browsers,
        IntervalTrigger(minutes=5),
        id="_browser_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        _cleanup_stale_files,
        IntervalTrigger(hours=24),
        id="_file_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_heartbeat,
        IntervalTrigger(minutes=30),
        id="_heartbeat",
        replace_existing=True,
    )
    logger.info("Scheduled: heartbeat (every 30m, local-first)")

    from .config import Config
    cfg = Config.load()
    if cfg.get("subconscious_enabled", True):
        interval_hours = cfg.get("subconscious_interval_hours", 2)
        scheduler.add_job(
            _run_subconscious,
            IntervalTrigger(hours=interval_hours),
            id="_subconscious",
            replace_existing=True,
        )
        logger.info("Scheduled: subconscious (every {}h)", interval_hours)


def _should_skip_cron_job(job: dict) -> bool:
    """Return True if a cron job entry should be skipped."""
    if job.get("id") == "heartbeat":
        logger.debug("Skipping crons.yaml heartbeat — now built-in with local change detection")
        return True
    if job.get("disabled"):
        logger.info("Skipping disabled job: {}", job.get("id", "?"))
        return True
    missing = [f for f in ("id", "cron", "prompt") if f not in job]
    if missing:
        logger.warning("Skipping cron job — missing fields: %s", missing)
        return True
    return False


def _schedule_cron_job(scheduler: BackgroundScheduler, job: dict) -> None:
    """Schedule a single cron job from crons.yaml."""
    deliver_to = job.get("deliver_to") or default_chat_id()
    is_heartbeat = bool(job.get("heartbeat", False))
    job_timeout = int(job.get("timeout", _CRON_TIMEOUT_SECONDS))
    try:
        scheduler.add_job(
            _run_job,
            CronTrigger.from_crontab(job["cron"]),
            kwargs={
                "job_id": job["id"],
                "prompt": job["prompt"],
                "deliver_to": deliver_to,
                "heartbeat": is_heartbeat,
                "timeout": job_timeout,
            },
            id=job["id"],
            replace_existing=True,
            max_instances=2,
            misfire_grace_time=300,
        )
        logger.info("Scheduled: {} ({}){}", job["id"], job["cron"], " [heartbeat]" if is_heartbeat else "")
    except Exception as e:
        logger.error("Failed to schedule job %s: %s", job.get("id", "?"), e)


def setup_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    _schedule_builtin_jobs(scheduler)

    crons_path = workspace.CRONS
    if not crons_path.exists():
        return scheduler

    data = yaml.safe_load(crons_path.read_text()) or {}
    for job in data.get("jobs", []):
        if not _should_skip_cron_job(job):
            _schedule_cron_job(scheduler, job)
    return scheduler
