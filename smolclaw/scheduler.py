"""APScheduler + crons.yaml."""
from __future__ import annotations

import asyncio
import threading

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from . import workspace
from .auth import default_chat_id
from .tools import TelegramSender

_telegram = TelegramSender()

HEARTBEAT_OK = "HEARTBEAT_OK"
SUBCONSCIOUS_OK = "SUBCONSCIOUS_OK"


_CRON_TIMEOUT_SECONDS = 300  # 5 minutes max per cron job


def _run_job(job_id: str, prompt: str, deliver_to: str, heartbeat: bool = False) -> None:
    from .agent import run
    logger.info("Cron: {}", job_id)

    result_holder: list[str] = []
    exc_holder: list[Exception] = []

    def _thread_target() -> None:
        try:
            result_holder.append(asyncio.run(run(chat_id=f"cron:{job_id}", user_message=prompt)))
        except Exception as e:
            exc_holder.append(e)

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    t.join(timeout=_CRON_TIMEOUT_SECONDS)

    if t.is_alive():
        logger.error("Cron {} timed out after {}s — thread abandoned (daemon, will die on exit)", job_id, _CRON_TIMEOUT_SECONDS)
        if deliver_to:
            _telegram.send(chat_id=deliver_to, message=f"Cron '{job_id}' timed out after {_CRON_TIMEOUT_SECONDS}s.")
        return

    if exc_holder:
        logger.error("Cron {} failed: {}", job_id, exc_holder[0])
        if deliver_to:
            _telegram.send(chat_id=deliver_to, message=f"Cron '{job_id}' failed: {exc_holder[0]}")
        return

    result = result_holder[0] if result_holder else "(no response)"
    if heartbeat and HEARTBEAT_OK in result:
        logger.debug("Heartbeat {}: silent (HEARTBEAT_OK)", job_id)
        return
    if job_id == "subconscious" and SUBCONSCIOUS_OK in result:
        logger.debug("Subconscious {}: silent (SUBCONSCIOUS_OK)", job_id)
        return
    if deliver_to and result != "(no response)":
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
    _run_job("subconscious", prompt, deliver_to, heartbeat=False)


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


def setup_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()

    # Periodic browser idle cleanup (every 5 min)
    scheduler.add_job(
        _cleanup_idle_browsers,
        IntervalTrigger(minutes=5),
        id="_browser_cleanup",
        replace_existing=True,
    )

    # Clean up stale screenshots and uploads (daily)
    scheduler.add_job(
        _cleanup_stale_files,
        IntervalTrigger(hours=24),
        id="_file_cleanup",
        replace_existing=True,
    )

    # Subconscious reflection loop
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

    crons_path = workspace.CRONS
    if not crons_path.exists():
        return scheduler

    data = yaml.safe_load(crons_path.read_text()) or {}
    for job in data.get("jobs", []):
        if job.get("disabled"):
            logger.info("Skipping disabled job: {}", job.get("id", "?"))
            continue
        missing = [f for f in ("id", "cron", "prompt") if f not in job]
        if missing:
            logger.warning("Skipping cron job — missing fields: %s", missing)
            continue
        deliver_to = job.get("deliver_to") or default_chat_id()
        is_heartbeat = bool(job.get("heartbeat", False))
        try:
            scheduler.add_job(
                _run_job,
                CronTrigger.from_crontab(job["cron"]),
                kwargs={
                    "job_id": job["id"],
                    "prompt": job["prompt"],
                    "deliver_to": deliver_to,
                    "heartbeat": is_heartbeat,
                },
                id=job["id"],
                replace_existing=True,
                max_instances=2,
                misfire_grace_time=300,
            )
            logger.info("Scheduled: {} ({}){}", job["id"], job["cron"], " [heartbeat]" if is_heartbeat else "")
        except Exception as e:
            logger.error("Failed to schedule job %s: %s", job.get("id", "?"), e)
            continue
    return scheduler
