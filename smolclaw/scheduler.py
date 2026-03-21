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

SUBCONSCIOUS_OK = "SUBCONSCIOUS_OK"


_CRON_TIMEOUT_SECONDS = 300  # 5 minutes max per cron job
_SUBCONSCIOUS_TIMEOUT_SECONDS = 600  # 10 minutes — subprocess boot + MCP init + multi-turn tool loop

# Auth failure tracking: consecutive auth errors trigger an alert
_auth_fail_count = 0
_AUTH_ALERT_THRESHOLD = 3  # alert after 3 consecutive auth failures
_auth_alert_sent = False


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


def _should_suppress_result(job_id: str, result: str) -> bool:
    """Return True if this cron result should not be delivered to the user."""
    if job_id == "subconscious" and SUBCONSCIOUS_OK in result:
        return True
    return result == "(no response)"


def _is_auth_error(exc: Exception) -> bool:
    """Return True if the exception looks like an OAuth/auth failure."""
    exc_str = str(exc).lower()
    return "401" in exc_str or "authentication" in exc_str or "oauth" in exc_str or ("token" in exc_str and "expired" in exc_str)


_AUTH_REMIND_INTERVAL = 10  # re-alert every N failures after threshold


def _handle_auth_failure(job_id: str, exc: Exception, deliver_to: str) -> None:
    """Track consecutive auth failures and send an alert after threshold."""
    global _auth_fail_count, _auth_alert_sent
    _auth_fail_count += 1
    if not deliver_to:
        return
    if _auth_fail_count >= _AUTH_ALERT_THRESHOLD and not _auth_alert_sent:
        _telegram.send(
            chat_id=deliver_to,
            message=f"⚠️ AUTH DOWN — {_auth_fail_count} consecutive auth failures. "
            f"OAuth token likely expired. All crons are failing. "
            f"Please run: claude /login"
        )
        _auth_alert_sent = True
    elif _auth_alert_sent and _auth_fail_count % _AUTH_REMIND_INTERVAL == 0:
        _telegram.send(
            chat_id=deliver_to,
            message=f"⚠️ AUTH STILL DOWN — {_auth_fail_count} consecutive failures. "
            f"Please run: claude /login"
        )
    elif not _auth_alert_sent:
        _telegram.send(chat_id=deliver_to, message=f"Cron '{job_id}' failed: {exc}")


def _reset_auth_tracking() -> None:
    """Reset auth failure counters after a successful run."""
    global _auth_fail_count, _auth_alert_sent
    _auth_fail_count = 0
    _auth_alert_sent = False


def _run_job(job_id: str, prompt: str, deliver_to: str, timeout: int | None = None) -> None:
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
        if _is_auth_error(exc):
            _handle_auth_failure(job_id, exc, deliver_to)
        else:
            _reset_auth_tracking()
            if deliver_to:
                _telegram.send(chat_id=deliver_to, message=f"Cron '{job_id}' failed: {exc}")
        return

    _reset_auth_tracking()

    if not _should_suppress_result(job_id, result) and deliver_to:
        _telegram.send(chat_id=deliver_to, message=result)


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
    _run_job("subconscious", prompt, deliver_to, timeout=_SUBCONSCIOUS_TIMEOUT_SECONDS)


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
    """Add built-in periodic jobs (cleanup, subconscious)."""
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
    job_timeout = int(job.get("timeout", _CRON_TIMEOUT_SECONDS))
    try:
        scheduler.add_job(
            _run_job,
            CronTrigger.from_crontab(job["cron"]),
            kwargs={
                "job_id": job["id"],
                "prompt": job["prompt"],
                "deliver_to": deliver_to,
                "timeout": job_timeout,
            },
            id=job["id"],
            replace_existing=True,
            max_instances=2,
            misfire_grace_time=300,
        )
        logger.info("Scheduled: {} ({})", job["id"], job["cron"])
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
