"""Simple, transparent rules for analysing tickets.

Definitions (all evaluated "as of" the shift end):
    Generated     created during the shift window
    Open          not resolved (includes on-hold tickets)
    On Hold       not resolved and marked on hold
    SLA Breached  not resolved and SLA due time has already passed
    SLA Risk      not resolved and SLA due within SLA_RISK_WINDOW_HOURS after shift end
    Important     not resolved and P1/P2 or a major incident
    Aging         not resolved and open longer than AGING_THRESHOLD_HOURS

Urgency (first matching rule wins - no scoring engine):
    Critical  P1, SLA breached, or major incident
    High      P2 or SLA approaching breach
    Medium    aging, escalated, or needs follow-up (on hold)
    Normal    any other open ticket
"""
from datetime import datetime, timedelta

from backend import config
from backend.automation.shifts import ShiftWindow
from backend.sources.tickets import Ticket

URGENCY_ORDER = ["Critical", "High", "Medium", "Normal"]


# --- grouping & counting -----------------------------------------------------
def group_tickets(tickets: list[Ticket]) -> dict[str, list[Ticket]]:
    groups: dict[str, list[Ticket]] = {name: [] for name in config.ASSIGNMENT_GROUPS}
    for ticket in tickets:
        groups.setdefault(ticket.assignment_group, []).append(ticket)
    return groups


def short_group_name(group: str) -> str:
    return group.replace(" Cloud Operations", "")


def is_generated_in_shift(ticket: Ticket, window: ShiftWindow) -> bool:
    return window.start <= ticket.created_at <= window.end


def is_open(ticket: Ticket) -> bool:
    return not ticket.is_resolved


def is_sla_breached(ticket: Ticket, as_of: datetime) -> bool:
    return is_open(ticket) and ticket.sla_due_at <= as_of


def is_sla_risk(ticket: Ticket, as_of: datetime,
                window_hours: float = config.SLA_RISK_WINDOW_HOURS) -> bool:
    return is_open(ticket) and as_of < ticket.sla_due_at <= as_of + timedelta(hours=window_hours)


def is_important(ticket: Ticket) -> bool:
    return is_open(ticket) and (ticket.priority in ("P1", "P2") or ticket.major_incident)


def age_hours(ticket: Ticket, as_of: datetime) -> float:
    return (as_of - ticket.created_at).total_seconds() / 3600


def is_aging(ticket: Ticket, as_of: datetime,
             threshold_hours: float = config.AGING_THRESHOLD_HOURS) -> bool:
    return is_open(ticket) and age_hours(ticket, as_of) > threshold_hours


def count_by_group(tickets: list[Ticket], window: ShiftWindow) -> list[dict]:
    """One row per assignment group + a TOTAL row."""
    as_of = window.end
    rows = []
    for group, items in group_tickets(tickets).items():
        rows.append({
            "group": group,
            "short_name": short_group_name(group),
            "generated": sum(is_generated_in_shift(t, window) for t in items),
            "open": sum(is_open(t) for t in items),
            "on_hold": sum(t.is_on_hold for t in items),
            "sla_risk": sum(is_sla_risk(t, as_of) for t in items),
            "sla_breached": sum(is_sla_breached(t, as_of) for t in items),
            "important": sum(is_important(t) for t in items),
            "aging": sum(is_aging(t, as_of) for t in items),
        })
    total = {"group": "Total", "short_name": "Total"}
    for key in ("generated", "open", "on_hold", "sla_risk", "sla_breached", "important", "aging"):
        total[key] = sum(row[key] for row in rows)
    rows.append(total)
    return rows


# --- urgency -------------------------------------------------------------------
def urgency(ticket: Ticket, as_of: datetime, escalated_ids: set[str] = frozenset()) -> str | None:
    if not is_open(ticket):
        return None
    if ticket.priority == "P1" or ticket.major_incident or is_sla_breached(ticket, as_of):
        return "Critical"
    if ticket.priority == "P2" or is_sla_risk(ticket, as_of):
        return "High"
    if is_aging(ticket, as_of) or ticket.ticket_id in escalated_ids or ticket.is_on_hold:
        return "Medium"
    return "Normal"


# --- on hold -------------------------------------------------------------------
def hold_category(reason: str | None) -> str:
    """What is the ticket waiting for? Customer / Vendor / Approval / Information / Another team."""
    text = (reason or "").lower()
    if "customer" in text:
        return "Customer"
    if "vendor" in text or "support" in text:
        return "Vendor"
    if "approval" in text:
        return "Approval"
    if "information" in text or "details" in text:
        return "Information"
    if "team" in text:
        return "Another team"
    return "Other"


# --- formatting helpers ----------------------------------------------------------
def fmt_time(value: datetime, as_of: datetime) -> str:
    """'4:30 PM', or '25 Sep 3:50 PM' when it is on a different day."""
    text = value.strftime("%I:%M %p").lstrip("0")
    if value.date() != as_of.date():
        text = f"{value:%d %b} {text}"
    return text


def fmt_age(ticket: Ticket, as_of: datetime) -> str:
    hours = age_hours(ticket, as_of)
    if hours >= 48:
        return f"{int(hours // 24)} days"
    return f"{int(hours)} hours"
