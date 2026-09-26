"""FastAPI app: a tiny API + the static dashboard.

Run from the project folder:
    python -m uvicorn backend.main:app --port 8000
Then open http://localhost:8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import config
from backend.automation import handover_generator as generator
from backend.automation.scheduler import scheduled_jobs, start_scheduler, stop_scheduler
from backend.automation.shifts import current_shift, now_local


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.ENABLE_SCHEDULER:
        start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Shift Handover Mail Automation", lifespan=lifespan)


class GenerateRequest(BaseModel):
    shift: str | None = None   # "morning" | "afternoon" | "night"; empty = current shift


@app.get("/api/status")
def status():
    shift = current_shift()
    jobs = scheduled_jobs()
    return {
        "automation": "Ready",
        "now": now_local().isoformat(),
        "timezone": config.TIMEZONE_NAME,
        "current_shift": {"key": shift.key, "name": shift.name, "timing": shift.timing,
                          "end": shift.end.isoformat()},
        "shifts": [{"key": key, "name": name, "end": end} for key, (name, end) in config.SHIFTS.items()],
        "scheduler_enabled": config.ENABLE_SCHEDULER,
        "scheduled_jobs": jobs,
        "next_run": jobs[0] if jobs else None,
        "recipients": config.EMAIL_TO_NAME,
        "last_run": generator.state["last_run"],
        "last_send": generator.state["last_send"],
    }


@app.post("/api/handover/generate")
def generate(request: GenerateRequest | None = None):
    shift = request.shift if request else None
    if shift and shift not in config.SHIFTS:
        raise HTTPException(400, f"Unknown shift '{shift}'. Use one of: {', '.join(config.SHIFTS)}")
    return generator.generate_handover(shift, trigger="manual")


@app.get("/api/handover/latest")
def latest():
    return generator.state["latest"]   # null until the first handover is generated


@app.post("/api/handover/send")
def send():
    if generator.state["latest"] is None:
        raise HTTPException(400, "Generate a handover before sending.")
    return generator.send_handover()


# The dashboard (index.html, style.css, script.js) is served from "/".
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
