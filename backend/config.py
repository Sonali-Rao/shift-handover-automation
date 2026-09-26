"""All configurable values live here - nothing else in the code hard-codes them.

Values come from environment variables (or a local .env file) with safe defaults,
so the demo runs even without a .env file.
"""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


# --- Shifts ---------------------------------------------------------------
TIMEZONE_NAME = _env("TIMEZONE", "Asia/Kolkata")
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

# key -> (display name, end time "HH:MM")
SHIFTS = {
    "morning": ("Morning Shift", _env("MORNING_SHIFT_END", "14:30")),
    "afternoon": ("Afternoon Shift", _env("AFTERNOON_SHIFT_END", "22:30")),
    "night": ("Night Shift", _env("NIGHT_SHIFT_END", "06:30")),
}

# --- Ticket rules -----------------------------------------------------------
AGING_THRESHOLD_HOURS = float(_env("AGING_THRESHOLD_HOURS", "24"))
SLA_RISK_WINDOW_HOURS = float(_env("SLA_RISK_WINDOW_HOURS", "2"))

ASSIGNMENT_GROUPS = [
    "Azure Cloud Operations",
    "AWS Cloud Operations",
    "OpenShift",
    "GCP",
    "OCI",
]

# --- Automation -------------------------------------------------------------
ENABLE_SCHEDULER = _env_bool("ENABLE_SCHEDULER", True)

# --- Email ------------------------------------------------------------------
EMAIL_PROVIDER = _env("EMAIL_PROVIDER", "mock")
EMAIL_FROM = _env("EMAIL_FROM", "l1-automation@example.com")
EMAIL_TO = _env("EMAIL_TO", "l1-cloudops@example.com")
EMAIL_TO_NAME = _env("EMAIL_TO_NAME", "L1 Cloud Operations")
EMAIL_SIGNATURE = _env("EMAIL_SIGNATURE", "L1 Cloud Operations")

# --- Files ------------------------------------------------------------------
TICKETS_FILE = PROJECT_ROOT / _env("TICKETS_FILE", "data/tickets.json")
TEAMS_FILE = PROJECT_ROOT / _env("TEAMS_FILE", "data/teams_messages.json")
TEMPLATES_DIR = PROJECT_ROOT / "backend" / "templates"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
OUTPUT_DIR = PROJECT_ROOT / "output"
HANDOVER_DIR = OUTPUT_DIR / "handovers"
SENT_EMAIL_DIR = OUTPUT_DIR / "sent_emails"


def display_path(path: Path) -> str:
    """Show a path relative to the project folder (for the UI / logs)."""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)
