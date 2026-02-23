"""APScheduler + crons.yaml."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .tools import telegram_send

logger = logging.getLogger("smolclaw.scheduler")
DEFAULT_CHAT = os.getenv("ALLOWED_USER_IDS", "").split(",")[0]


async def _run_job(job_id: str, prompt: str, deliver_to: str) -> None:
    from .agent import run
    logger.info("Cron: %s", job_id)
    try:
        result = run(chat_id=f"cron:{job_id}", user_message=prompt)
        telegram_send(deliver_to, result)
    except Exception as e:
        logger.error("Cron %s failed: %s", job_id, e)


def setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    crons_path = Path("crons.yaml")
    if not crons_path.exists():
        return scheduler
    for job in yaml.safe_load(crons_path.read_text()).get("jobs", []):
        scheduler.add_job(
            _run_job,
            CronTrigger.from_crontab(job["cron"]),
            kwargs={"job_id": job["id"], "prompt": job["prompt"], "deliver_to": job.get("deliver_to", DEFAULT_CHAT)},
            id=job["id"],
            replace_existing=True,
        )
        logger.info("Scheduled: %s (%s)", job["id"], job["cron"])
    return scheduler
