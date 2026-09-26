"""Where Microsoft Teams messages come from.

    TeamsSource             <- interface the automation depends on
        MockTeamsSource     <- reads data/teams_messages.json (used by the demo)
        (future) MicrosoftGraphTeamsSource

Future MicrosoftGraphTeamsSource (not implemented - no credentials needed for the demo):
    1. Register an app in Microsoft Entra ID with ChannelMessage.Read.All permission.
    2. Get a token with the client-credentials flow (e.g. the `msal` library),
       reading the client id/secret from environment variables - never from code.
    3. GET https://graph.microsoft.com/v1.0/teams/{team-id}/channels/{channel-id}/messages
    4. Convert each message into a `TeamsMessage` and keep those inside the shift window.
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend import config
from backend.automation.shifts import ShiftWindow


@dataclass
class TeamsMessage:
    message_id: str
    timestamp: datetime
    author: str
    team: str        # Azure | AWS | OpenShift | GCP | OCI
    text: str


class TeamsSource(ABC):
    @abstractmethod
    def get_messages(self, window: ShiftWindow) -> list[TeamsMessage]:
        """Return the Teams messages posted during the shift."""


class MockTeamsSource(TeamsSource):
    """Reads messages from JSON. Uses the same timestamp "replay" as MockTicketSource."""

    def __init__(self, path: Path = config.TEAMS_FILE):
        self.path = path

    def get_messages(self, window: ShiftWindow) -> list[TeamsMessage]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        offset = window.end - datetime.fromisoformat(data["referenceShiftEnd"])
        messages = [
            TeamsMessage(
                message_id=m["id"],
                timestamp=(datetime.fromisoformat(m["timestamp"]) + offset).astimezone(config.TIMEZONE),
                author=m["author"],
                team=m["team"],
                text=m["message"],
            )
            for m in data["messages"]
        ]
        return [m for m in messages if window.start <= m.timestamp <= window.end]


def get_teams_source() -> TeamsSource:
    return MockTeamsSource()
