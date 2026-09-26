"""THE automation. One function builds the handover - used by BOTH the
"Generate Handover" button and the scheduler, so the logic is never duplicated.

Pipeline (see generate_handover):
     1. Load tickets                      9. Identify aging tickets
     2. Load Teams messages              10. Identify on-hold tickets
     3. Filter irrelevant Teams messages 11. Extract important Teams updates
     4. Group tickets by cloud           12. Extract shift changes
     5. Calculate ticket counts          13. Build handover data
     6. Identify SLA risk                14. Populate email template
     7. Identify SLA breaches            15. Save generated handover
     8. Identify important tickets       16. Send / simulate sending (send_handover)
"""
import logging
import threading
from datetime import datetime

from jinja2 import Environment, FileSystemLoader

from backend import config
from backend.automation import processor as p
from backend.automation.shifts import ShiftWindow, current_shift, now_local, shift_window
from backend.services.cleaner import CleaningResult, get_text_cleaner
from backend.services.email_service import get_email_service
from backend.sources.teams import get_teams_source
from backend.sources.tickets import Ticket, get_ticket_source

_templates = Environment(loader=FileSystemLoader(config.TEMPLATES_DIR),
                         trim_blocks=True, lstrip_blocks=True, autoescape=False)
_lock = threading.Lock()
log = logging.getLogger("uvicorn.error")

# The most recent handover + run history, kept in memory for the dashboard.
state: dict = {"latest": None, "last_run": None, "last_send": None}


def generate_handover(shift_key: str | None = None, trigger: str = "manual",
                      now: datetime | None = None) -> dict:
    steps: list[dict] = []

    def step(label: str, detail: str) -> None:
        steps.append({"label": label, "detail": detail})

    window = shift_window(shift_key, now) if shift_key else current_shift(now)
    as_of = window.end  # the handover always describes the shift as of its end time

    # 1-2. Load data
    tickets = [t for t in get_ticket_source().get_tickets(window) if t.created_at <= as_of]
    step("Tickets loaded", f"{len(tickets)} tickets across {len(config.ASSIGNMENT_GROUPS)} assignment groups")
    messages = get_teams_source().get_messages(window)
    step("Teams updates loaded", f"{len(messages)} messages from the shift")

    # 3. Clean Teams chat
    cleaned = get_text_cleaner().clean(messages)
    step("Information cleaned",
         f"{len(cleaned.updates)} useful updates kept, {len(cleaned.removed)} irrelevant/duplicate messages removed")

    # 4-5. Group + count
    groups = p.count_by_group(tickets, window)
    step("Ticket summary created", f"Generated {groups[-1]['generated']}, open {groups[-1]['open']}, "
                                   f"SLA risk {groups[-1]['sla_risk']}, breached {groups[-1]['sla_breached']}")

    # 6-12. Identify what the next shift must know
    data = build_handover_data(window, tickets, cleaned, groups)
    step("Important updates extracted",
         f"{len(data['urgent'])} urgent tickets, {len(data['shift_changes'])} shift changes, "
         f"{len(data['actions'])} actions")

    # 14. Fill the predefined templates
    text_body = _templates.get_template("handover_email.txt").render(h=data)
    html_body = _templates.get_template("handover_email.html").render(h=data)

    # 15. Save
    files = save_handover(window, data["subject"], html_body, text_body)
    step("Handover generated", f"Saved to {files['text']}")

    result = {
        "subject": data["subject"],
        "html": html_body,
        "text": text_body,
        "shift": {"key": window.key, "name": window.name, "timing": window.timing,
                  "start": window.start.isoformat(), "end": window.end.isoformat()},
        "summary": groups[-1],
        "groups": groups,
        "teams": {
            "kept": [{"time": u.message.timestamp.strftime("%I:%M %p").lstrip("0"),
                      "author": u.message.author, "team": u.message.team,
                      "original": u.message.text, "summary": u.summary, "kind": u.kind}
                     for u in cleaned.updates],
            "removed": [{"time": m.timestamp.strftime("%I:%M %p").lstrip("0"),
                         "author": m.author, "team": m.team, "original": m.text, "reason": reason}
                        for m, reason in cleaned.removed],
        },
        "steps": steps,
        "files": files,
        "trigger": trigger,
        "generated_at": now_local().isoformat(),
    }
    with _lock:
        state["latest"] = result
        state["last_run"] = {"at": result["generated_at"], "trigger": trigger,
                             "shift": window.name, "subject": data["subject"]}
    return result


def build_handover_data(window: ShiftWindow, tickets: list[Ticket],
                        cleaned: CleaningResult, groups: list[dict]) -> dict:
    as_of = window.end
    latest_note = {u.ticket_id: u.summary.split(": ", 1)[1] for u in cleaned.ticket_updates}
    escalated_ids = {u.ticket_id for u in cleaned.ticket_updates if "escalated" in u.categories}
    waiting_for = {u.ticket_id: u.waiting_for for u in cleaned.ticket_updates if u.waiting_for}
    by_id = {t.ticket_id: t for t in tickets}

    # 6-8. Urgent / important tickets (Critical + High urgency)
    urgent = []
    for t in tickets:
        level = p.urgency(t, as_of, escalated_ids)
        if level not in ("Critical", "High"):
            continue
        reasons = []
        if t.major_incident:
            reasons.append("Major incident")
        if p.is_sla_breached(t, as_of):
            reasons.append(f"SLA breached at {p.fmt_time(t.sla_due_at, as_of)}")
        elif p.is_sla_risk(t, as_of):
            reasons.append(f"SLA due at {p.fmt_time(t.sla_due_at, as_of)} – approaching breach")
        if t.is_on_hold:
            reasons.append("High-priority ticket currently on hold")
        if t.ticket_id in escalated_ids:
            reasons.append("Escalated")
        if not reasons:
            reasons.append(f"{t.priority} ticket still open")

        if p.is_sla_breached(t, as_of):
            recommendation = f"Escalate and follow up with the {p.short_group_name(t.assignment_group)} team immediately."
        elif p.is_sla_risk(t, as_of):
            recommendation = "Please follow up with the assigned team before the SLA breaches."
        elif t.is_on_hold:
            recommendation = "Follow up on the pending dependency."
        else:
            recommendation = "Continue monitoring."
        urgent.append({
            "ticket_id": t.ticket_id, "group": p.short_group_name(t.assignment_group),
            "priority": t.priority, "urgency": level, "description": t.short_description,
            "reasons": reasons, "recommendation": recommendation,
            "note": latest_note.get(t.ticket_id), "_sort": (p.URGENCY_ORDER.index(level), t.sla_due_at),
        })
    urgent.sort(key=lambda item: item.pop("_sort"))

    # 9. Aging
    aging_tickets = sorted((t for t in tickets if p.is_aging(t, as_of)), key=lambda t: t.created_at)
    aging = [{"ticket_id": t.ticket_id, "group": p.short_group_name(t.assignment_group),
              "age": p.fmt_age(t, as_of), "description": t.short_description} for t in aging_tickets]

    # 10. On hold (reason from the ticket, else from the Teams update)
    on_hold = []
    for t in tickets:
        if t.is_on_hold:
            reason = t.hold_reason or (f"Waiting for {waiting_for[t.ticket_id]}"
                                       if t.ticket_id in waiting_for else "Reason not specified")
            on_hold.append({"ticket_id": t.ticket_id, "group": p.short_group_name(t.assignment_group),
                            "reason": reason, "category": p.hold_category(reason)})

    # 11-12. Teams updates + shift changes
    def describe(update) -> str:
        ticket = by_id.get(update.ticket_id)
        label = f"{update.ticket_id} ({p.short_group_name(ticket.assignment_group)} – {ticket.short_description})" \
            if ticket else update.ticket_id
        return f"{label}: {update.summary.split(': ', 1)[1]}"

    shift_changes = [u.summary for u in cleaned.shift_changes]
    general_updates = [f"{u.message.team}: {u.summary}" for u in cleaned.general_updates]
    ticket_updates = [describe(u) for u in cleaned.ticket_updates]

    # Actions for the incoming shift
    actions = []
    breached_ids = [t.ticket_id for t in tickets if p.is_sla_breached(t, as_of)]
    risk_ids = [t.ticket_id for t in tickets if p.is_sla_risk(t, as_of)]
    open_major = [t.ticket_id for t in tickets if t.major_incident and p.is_open(t)]
    resolved_major = [t.ticket_id for t in tickets
                      if t.major_incident and t.is_resolved and window.start <= t.updated_at <= as_of]
    if breached_ids:
        actions.append(f"Escalate SLA-breached tickets: {', '.join(breached_ids)}.")
    if risk_ids:
        actions.append(f"Follow up on SLA-risk tickets before breach: {', '.join(risk_ids)}.")
    if open_major:
        actions.append(f"Continue monitoring major incident(s): {', '.join(open_major)}.")
    for ticket_id in resolved_major:
        actions.append(f"Monitor {ticket_id} for recurrence (major incident resolved this shift).")
    if aging:
        actions.append(f"Follow up on aging tickets: {', '.join(t['ticket_id'] for t in aging)}.")
    if on_hold:
        actions.append(f"Follow up on the {len(on_hold)} tickets currently on hold with the pending parties.")
    for u in cleaned.ticket_updates:
        if u.action:
            actions.append(f"{u.ticket_id}: {u.action}.")
    if general_updates:
        actions.append("Keep track of the operational notices listed under Important Team Updates.")
    if not actions:
        actions.append("No pending actions. Continue regular monitoring.")

    return {
        "subject": f"Shift Handover – Cloud Operations – {window.name} – {window.end:%d %b %Y}",
        "shift_name": window.name,
        "shift_timing": window.timing,
        "groups": groups,
        "urgent": urgent,
        "aging": aging,
        "aging_threshold": f"{config.AGING_THRESHOLD_HOURS:g}",
        "on_hold": on_hold,
        "shift_changes": shift_changes,
        "general_updates": general_updates,
        "ticket_updates": ticket_updates,
        "actions": actions,
        "signature": config.EMAIL_SIGNATURE,
    }


def save_handover(window: ShiftWindow, subject: str, html_body: str, text_body: str) -> dict:
    config.HANDOVER_DIR.mkdir(parents=True, exist_ok=True)
    base = config.HANDOVER_DIR / f"handover_{window.end:%Y-%m-%d}_{window.key}"
    html_path, text_path = base.with_suffix(".html"), base.with_suffix(".txt")
    html_path.write_text(f"<h2>{subject}</h2>\n{html_body}", encoding="utf-8")
    text_path.write_text(f"Subject: {subject}\n\n{text_body}", encoding="utf-8")
    return {"html": config.display_path(html_path), "text": config.display_path(text_path)}


def send_handover(handover: dict | None = None) -> dict:
    """16. Send the given (or latest) handover through the configured EmailService."""
    handover = handover or state["latest"]
    if handover is None:
        raise ValueError("No handover has been generated yet.")
    result = get_email_service().send(handover["subject"], handover["html"], handover["text"])
    result["subject"] = handover["subject"]
    result["at"] = now_local().isoformat()
    with _lock:
        state["last_send"] = result
    return result


def run_scheduled_handover(shift_key: str) -> None:
    """Called by the scheduler at shift end: generate, then send automatically."""
    handover = generate_handover(shift_key, trigger="scheduled")
    send_handover(handover)
    log.info("[scheduler] %s generated and sent.", handover["subject"])

