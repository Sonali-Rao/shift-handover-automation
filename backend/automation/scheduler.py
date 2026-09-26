"""Runs the handover automatically at the end of every shift.

    MORNING_SHIFT_END   14:30 -> Morning shift handover
    AFTERNOON_SHIFT_END 22:30 -> Afternoon shift handover
    NIGHT_SHIFT_END     06:30 -> Night shift handover

Each job calls the SAME function used by the "Generate Handover" button.
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend import config
from backend.automation.handover_generator import run_scheduled_handover
from backend.automation.shifts import shift_end_time

scheduler = BackgroundScheduler(timezone=config.TIMEZONE)


def start_scheduler() -> None:
    for key, (name, _end) in config.SHIFTS.items():
        end = shift_end_time(key)
        scheduler.add_job(
            run_scheduled_handover,
            CronTrigger(hour=end.hour, minute=end.minute, timezone=config.TIMEZONE),
            args=[key],
            id=f"handover_{key}",
            name=f"{name} handover",
            replace_existing=True,
            misfire_grace_time=600,
        )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def scheduled_jobs() -> list[dict]:
    return [
        {"id": job.id, "name": job.name,
         "next_run": job.next_run_time.isoformat() if job.next_run_time else None}
        for job in sorted(scheduler.get_jobs(), key=lambda j: j.next_run_time)
    ]
