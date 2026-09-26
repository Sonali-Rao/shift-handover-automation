"""Shift timing helpers.

Rule: every shift starts when the previous shift ends.
    Night     22:30 (previous day) -> 06:30
    Morning   06:30 -> 14:30
    Afternoon 14:30 -> 22:30
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from backend import config


@dataclass
class ShiftWindow:
    key: str          # "morning" | "afternoon" | "night"
    name: str         # "Morning Shift"
    start: datetime
    end: datetime

    @property
    def timing(self) -> str:
        return f"{self.start:%I:%M %p} – {self.end:%I:%M %p}".replace(" 0", " ").lstrip("0")


def shift_end_time(key: str) -> time:
    hours, minutes = config.SHIFTS[key][1].split(":")
    return time(int(hours), int(minutes))


def _shifts_in_day_order() -> list[str]:
    """Shift keys sorted by the time of day they end (night, morning, afternoon)."""
    return sorted(config.SHIFTS, key=shift_end_time)


def _window_ending_at(key: str, end: datetime) -> ShiftWindow:
    order = _shifts_in_day_order()
    previous_key = order[order.index(key) - 1]
    start = end.replace(hour=shift_end_time(previous_key).hour,
                        minute=shift_end_time(previous_key).minute)
    if start >= end:
        start -= timedelta(days=1)
    return ShiftWindow(key, config.SHIFTS[key][0], start, end)


def _next_end(key: str, now: datetime) -> datetime:
    end = datetime.combine(now.date(), shift_end_time(key), tzinfo=now.tzinfo)
    return end if end > now else end + timedelta(days=1)


def now_local() -> datetime:
    return datetime.now(config.TIMEZONE).replace(second=0, microsecond=0)


def current_shift(now: datetime | None = None) -> ShiftWindow:
    """The current shift is simply the shift that ends next."""
    now = now or now_local()
    key = min(config.SHIFTS, key=lambda k: _next_end(k, now))
    return _window_ending_at(key, _next_end(key, now))


def shift_window(key: str, now: datetime | None = None) -> ShiftWindow:
    """Window for the given shift.

    - If it is the running shift: the window ending at the upcoming shift end.
    - Otherwise: the most recently completed occurrence of that shift.
    """
    now = now or now_local()
    if key == current_shift(now).key:
        return _window_ending_at(key, _next_end(key, now))
    end = _next_end(key, now)
    if end > now:
        end -= timedelta(days=1)
    return _window_ending_at(key, end)
