"""APScheduler + crons.yaml."""
from __future__ import annotations

import asyncio
import os
import threading

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .tools import TelegramSender
from . import workspace
from .auth import default_chat_id

_telegram = TelegramSender()

from loguru import logger

HEARTBEAT_OK = "HEARTBEAT_OK"


def _run_job(job_id: str, prompt: str, deliver_to: str, heartbeat: bool = False) -> None:
    from .agent import run
    logger.info("Cron: {}", job_id)

    result_holder: list[str] = []
    exc_holder: list[Exception] = []

    def _thread_target() -> None:
        try:
            # NOTE: spawn_task background tasks are not supported in cron context.
            # asyncio.run() creates a fresh event loop that is destroyed when run() returns,
            # cancelling any fire-and-forget tasks created during execution.
            result_holder.append(asyncio.run(run(chat_id=f"cron:{job_id}", user_message=prompt)))
        except Exception as e:
            exc_holder.append(e)

    t = threading.Thread(target=_thread_target, daemon=True)
    t.start()
    t.join()  # scheduler thread is freed immediately; job runs in its own thread

    if exc_holder:
        logger.error("Cron {} failed: {}", job_id, exc_holder[0])
        return

    result = result_holder[0] if result_holder else "(no response)"
    if heartbeat and HEARTBEAT_OK in result:
        logger.debug("Heartbeat {}: silent (HEARTBEAT_OK)", job_id)
        return
    if deliver_to and result != "(no response)":
        _telegram.send(chat_id=deliver_to, message=result)


def setup_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    crons_path = workspace.CRONS
    if not crons_path.exists():
        return scheduler

    data = yaml.safe_load(crons_path.read_text()) or {}
    for job in data.get("jobs", []):
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
