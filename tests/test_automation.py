"""Basic tests for the important automation rules.

Run:  python -m pytest -v
"""
from datetime import datetime, timedelta

import pytest

from backend import config
from backend.automation import processor as p
from backend.automation.handover_generator import generate_handover, send_handover
from backend.automation.shifts import ShiftWindow, current_shift, shift_window
from backend.services.cleaner import RuleBasedTextCleaner
from backend.sources.teams import TeamsMessage
from backend.sources.tickets import Ticket

TZ = config.TIMEZONE
SHIFT_END = datetime(2026, 9, 26, 14, 30, tzinfo=TZ)
WINDOW = ShiftWindow("morning", "Morning Shift", datetime(2026, 9, 26, 6, 30, tzinfo=TZ), SHIFT_END)


def make_ticket(ticket_id="INC0000001", group="Azure Cloud Operations", priority="P3",
                status="In Progress", created_hours_ago=2, sla_in_hours=10, **extra) -> Ticket:
    return Ticket(
        ticket_id=ticket_id, assignment_group=group, short_description="Test ticket",
        priority=priority, status=status, assignee="Test",
        created_at=SHIFT_END - timedelta(hours=created_hours_ago), updated_at=SHIFT_END,
        sla_due_at=SHIFT_END + timedelta(hours=sla_in_hours), **extra,
    )


def make_message(text, author="Rahul", minutes_before_end=60) -> TeamsMessage:
    return TeamsMessage("MSG", SHIFT_END - timedelta(minutes=minutes_before_end), author, "Azure", text)


# --- tickets -----------------------------------------------------------------------
def test_tickets_are_grouped_into_the_five_cloud_groups():
    tickets = [make_ticket("A", "Azure Cloud Operations"), make_ticket("B", "AWS Cloud Operations"),
               make_ticket("C", "Azure Cloud Operations"), make_ticket("D", "OCI")]
    groups = p.group_tickets(tickets)
    assert list(groups)[:5] == config.ASSIGNMENT_GROUPS
    assert [t.ticket_id for t in groups["Azure Cloud Operations"]] == ["A", "C"]
    assert groups["GCP"] == []


def test_ticket_counts_per_group():
    tickets = [
        make_ticket("A", created_hours_ago=2),                      # generated, open
        make_ticket("B", status="Resolved", created_hours_ago=3),  # generated, not open
        make_ticket("C", created_hours_ago=30),                     # not generated (older), open, aging
        make_ticket("D", status="On Hold", on_hold=True),           # generated, open, on hold
    ]
    azure = p.count_by_group(tickets, WINDOW)[0]
    assert azure["group"] == "Azure Cloud Operations"
    assert azure["generated"] == 3
    assert azure["open"] == 3
    assert azure["on_hold"] == 1
    assert azure["aging"] == 1
    assert p.count_by_group(tickets, WINDOW)[-1]["group"] == "Total"


def test_sla_risk_detection():
    assert p.is_sla_risk(make_ticket(sla_in_hours=1), SHIFT_END)
    assert not p.is_sla_risk(make_ticket(sla_in_hours=5), SHIFT_END)       # far away
    assert not p.is_sla_risk(make_ticket(sla_in_hours=-1), SHIFT_END)      # already breached
    assert not p.is_sla_risk(make_ticket(sla_in_hours=1, status="Resolved"), SHIFT_END)


def test_sla_breach_detection():
    assert p.is_sla_breached(make_ticket(sla_in_hours=-0.5), SHIFT_END)
    assert not p.is_sla_breached(make_ticket(sla_in_hours=0.5), SHIFT_END)
    assert not p.is_sla_breached(make_ticket(sla_in_hours=-3, status="Resolved"), SHIFT_END)


def test_aging_detection_uses_threshold():
    assert p.is_aging(make_ticket(created_hours_ago=30), SHIFT_END, threshold_hours=24)
    assert not p.is_aging(make_ticket(created_hours_ago=10), SHIFT_END, threshold_hours=24)
    assert not p.is_aging(make_ticket(created_hours_ago=72, status="Resolved"), SHIFT_END)


def test_on_hold_detection_and_reason_category():
    assert make_ticket(status="On Hold").is_on_hold
    assert make_ticket(on_hold=True).is_on_hold
    assert not make_ticket(status="Resolved", on_hold=True).is_on_hold
    assert p.hold_category("Waiting for customer confirmation") == "Customer"
    assert p.hold_category("Waiting for approval from Security team") == "Approval"
    assert p.hold_category("Waiting for Oracle vendor support response") == "Vendor"
    assert p.hold_category("Waiting for Network team") == "Another team"


def test_urgency_levels():
    assert p.urgency(make_ticket(priority="P1"), SHIFT_END) == "Critical"
    assert p.urgency(make_ticket(sla_in_hours=-1), SHIFT_END) == "Critical"
    assert p.urgency(make_ticket(priority="P2"), SHIFT_END) == "High"
    assert p.urgency(make_ticket(sla_in_hours=1), SHIFT_END) == "High"
    assert p.urgency(make_ticket(created_hours_ago=30), SHIFT_END) == "Medium"
    assert p.urgency(make_ticket(), SHIFT_END) == "Normal"
    assert p.urgency(make_ticket(status="Resolved"), SHIFT_END) is None


# --- Teams cleaning ------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Good morning everyone", "Hey", "Thanks", "Okay", "Noted",
                                  "Good night", "👍", "Anyone up for lunch at 1?"])
def test_irrelevant_messages_are_filtered(text):
    result = RuleBasedTextCleaner().clean([make_message(text)])
    assert result.updates == []
    assert len(result.removed) == 1


def test_ticket_update_is_summarized_with_next_action():
    msg = make_message("INC0012456 is still being investigated. "
                       "Issue appears to be related to network configuration. Waiting for the Network team.")
    update = RuleBasedTextCleaner().clean([msg]).updates[0]
    assert update.summary == "INC0012456: Investigation ongoing; waiting for Network team."
    assert update.action == "Follow up with Network team"


def test_useful_message_without_ticket_id_is_kept():
    msg = make_message("Heads up: Google Cloud has posted a service advisory for latency in asia-south1.")
    result = RuleBasedTextCleaner().clean([msg])
    assert result.general_updates[0].summary.startswith("Google Cloud has posted a service advisory")


def test_duplicates_keep_only_latest_update_per_ticket():
    older = make_message("INC0012489 has been put on hold. Waiting for customer confirmation.", minutes_before_end=90)
    newer = make_message("INC0012489 on hold, waiting for customer confirmation", minutes_before_end=80)
    result = RuleBasedTextCleaner().clean([older, newer])
    assert len(result.ticket_updates) == 1
    assert result.ticket_updates[0].message is newer
    assert result.removed[0][1].startswith("Duplicate")


def test_shift_change_extraction():
    msg = make_message("I will be covering the evening shift instead of Amit today.", author="Neha")
    result = RuleBasedTextCleaner().clean([msg])
    assert [u.summary for u in result.shift_changes] == [
        "Neha will cover the evening shift instead of Amit today."]


# --- shifts --------------------------------------------------------------------------
def test_current_shift_is_the_shift_that_ends_next():
    assert current_shift(datetime(2026, 9, 26, 10, 0, tzinfo=TZ)).key == "morning"
    assert current_shift(datetime(2026, 9, 26, 18, 0, tzinfo=TZ)).key == "afternoon"
    night = current_shift(datetime(2026, 9, 26, 23, 0, tzinfo=TZ))
    assert night.key == "night"
    assert night.start == datetime(2026, 9, 26, 22, 30, tzinfo=TZ)
    assert night.end == datetime(2026, 9, 27, 6, 30, tzinfo=TZ)


def test_completed_shift_window_is_most_recent_occurrence():
    window = shift_window("morning", datetime(2026, 9, 26, 14, 30, tzinfo=TZ))  # scheduler fires at 14:30
    assert window.end == SHIFT_END
    assert window.start == datetime(2026, 9, 26, 6, 30, tzinfo=TZ)


# --- end-to-end handover -----------------------------------------------------------------
def test_handover_generation_uses_mock_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HANDOVER_DIR", tmp_path / "handovers")
    handover = generate_handover("morning", now=datetime(2026, 9, 26, 12, 0, tzinfo=TZ))
    text = handover["text"]

    assert handover["subject"] == "Shift Handover – Cloud Operations – Morning Shift – 26 Sep 2026"
    for group in config.ASSIGNMENT_GROUPS:
        assert group in text
    for section in ("TICKET SUMMARY", "IMPORTANT / URGENT TICKETS", "AGING TICKETS",
                    "TICKETS ON HOLD", "IMPORTANT TEAM UPDATES", "ACTION REQUIRED"):
        assert section in text

    summary = handover["summary"]
    assert summary["generated"] == 24
    assert summary["sla_risk"] == 4
    assert summary["sla_breached"] == 4
    assert summary["aging"] == 6
    assert summary["on_hold"] == 8

    assert "INC0012456" in text                                   # SLA-risk ticket
    assert "Neha will cover the evening shift instead of Amit" in text
    assert "Good morning" not in text and "lunch" not in text      # noise filtered
    assert len(handover["steps"]) == 6
    assert (tmp_path / "handovers" / "handover_2026-09-26_morning.txt").exists()


def test_mock_email_send(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HANDOVER_DIR", tmp_path / "handovers")
    monkeypatch.setattr(config, "SENT_EMAIL_DIR", tmp_path / "sent")

    result = send_handover(generate_handover("morning"))
    assert result["sent"] is True
    assert result["recipients"] == config.EMAIL_TO_NAME
    assert list((tmp_path / "sent").glob("*.eml"))
