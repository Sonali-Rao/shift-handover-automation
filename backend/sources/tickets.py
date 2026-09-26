"""Where tickets come from.

    TicketSource            <- interface the automation depends on
        MockTicketSource    <- reads data/tickets.json (used by the demo)
        (future) ServiceNowTicketSource / JiraTicketSource / any ITSM REST API

To plug in a real ticketing system, create a new class that implements
`get_tickets()` and return it from `get_ticket_source()`. Nothing else changes.
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend import config
from backend.automation.shifts import ShiftWindow

RESOLVED_STATUSES = {"Resolved", "Closed", "Cancelled"}


@dataclass
class Ticket:
    ticket_id: str
    assignment_group: str
    short_description: str
    priority: str              # P1 .. P4
    status: str                # Open | In Progress | On Hold | Resolved
    assignee: str
    created_at: datetime
    updated_at: datetime
    sla_due_at: datetime
    on_hold: bool = False
    hold_reason: str | None = None
    major_incident: bool = False

    @property
    def is_resolved(self) -> bool:
        return self.status in RESOLVED_STATUSES

    @property
    def is_on_hold(self) -> bool:
        return not self.is_resolved and (self.on_hold or self.status == "On Hold")


class TicketSource(ABC):
    @abstractmethod
    def get_tickets(self, window: ShiftWindow) -> list[Ticket]:
        """Return all tickets relevant to this shift (new + still-open backlog)."""


class MockTicketSource(TicketSource):
    """Reads tickets from a JSON file.

    Demo "replay": the JSON is written as if the shift ended at
    `referenceShiftEnd`. All timestamps are shifted by the same amount so
    the data always lines up with the shift being generated (today, any shift).
    This keeps the demo meaningful whenever you run it.
    """

    def __init__(self, path: Path = config.TICKETS_FILE):
        self.path = path

    def get_tickets(self, window: ShiftWindow) -> list[Ticket]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        offset = window.end - datetime.fromisoformat(data["referenceShiftEnd"])

        def when(value: str) -> datetime:
            return (datetime.fromisoformat(value) + offset).astimezone(config.TIMEZONE)

        return [
            Ticket(
                ticket_id=t["ticketId"],
                assignment_group=t["assignmentGroup"],
                short_description=t["shortDescription"],
                priority=t["priority"],
                status=t["status"],
                assignee=t["assignee"],
                created_at=when(t["createdAt"]),
                updated_at=when(t["updatedAt"]),
                sla_due_at=when(t["slaDueAt"]),
                on_hold=t.get("onHold", False),
                hold_reason=t.get("holdReason"),
                major_incident=t.get("majorIncident", False),
            )
            for t in data["tickets"]
        ]


def get_ticket_source() -> TicketSource:
    return MockTicketSource()
