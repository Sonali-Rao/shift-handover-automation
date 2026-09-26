"""Cleans Microsoft Teams chat into short, useful handover lines.

    TextCleaner                <- interface
        RuleBasedTextCleaner   <- default: simple keyword/regex rules, fully offline
        (optional future) LLMTextCleaner - could send the *kept* messages to an LLM
        for nicer summaries. Not needed: the rule-based version is the default.

Steps for every message:
    1. Drop greetings / acknowledgements ("Good morning", "Thanks", "Noted", ...)
    2. Drop chatter with no operational content (no ticket ID, no ops keyword)
    3. Detect shift changes ("I will be covering the evening shift instead of Amit")
    4. Summarize ticket updates ("INC0012456: Investigation ongoing; waiting for Network team.")
    5. Remove duplicates: keep only the LATEST update per ticket
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from backend.sources.teams import TeamsMessage

TICKET_ID_RE = re.compile(r"\bINC\d{7}\b")

NOISE_PHRASES = {
    "hi", "hey", "hello", "hi all", "hello all", "hey team", "morning", "morning all",
    "good morning", "good morning everyone", "good morning all", "good morning team",
    "good afternoon", "good evening", "good evening team", "good night", "gn",
    "thanks", "thank you", "thanks all", "thx", "ty", "ok", "okay", "okay noted",
    "ok noted", "noted", "sure", "done", "yes", "no", "cool", "great", "bye",
}

OPERATIONAL_KEYWORDS = re.compile(
    r"\b(outage|latency|degraded|advisory|maintenance|change window|patching|incident|"
    r"escalat\w*|down|alert\w*|monitor\w*|shift|cover\w*|on hold|waiting|resolved|"
    r"investigat\w*|failure|failed|error\w*|p1|p2)\b",
    re.IGNORECASE,
)

SHIFT_CHANGE_PATTERNS = [
    # "I will be covering the evening shift instead of Amit today."
    re.compile(r"\b(?:I will be|I'll be|I am|I'm|I will) cover(?:ing)? (?:the )?(?P<shift>\w+) shift "
               r"(?:instead of|for) (?P<other>\w+)(?: (?P<when>today|tomorrow|tonight))?", re.IGNORECASE),
    # "Neha is covering the evening shift instead of Amit."
    re.compile(r"\b(?P<person>[A-Z]\w+) (?:will be|is) cover(?:ing)? (?:the )?(?P<shift>\w+) shift "
               r"(?:instead of|for) (?P<other>\w+)(?: (?P<when>today|tomorrow|tonight))?"),
]
SHIFT_CHANGE_HINT = re.compile(r"\b(cover\w*|swap\w*|taking over)\b.*\bshift\b", re.IGNORECASE)

WAITING_RE = re.compile(r"waiting (?:for|on) (?:the )?([^.,;]+)", re.IGNORECASE)
ESCALATED_RE = re.compile(r"escalated to (?:the )?([^.,;]+)", re.IGNORECASE)

# (regex, category, phrase) - checked in this order; all matches go into the summary.
STATUS_RULES = [
    (re.compile(r"\b(resolved|restored|fixed)\b", re.I), "resolved", "Resolved"),
    (re.compile(r"\bon hold\b", re.I), "on_hold", "On hold"),
    (re.compile(r"\bmajor incident\b", re.I), "incident", "Major incident declared"),
    (re.compile(r"being investigated|under investigation|investigating|working on it", re.I),
     "investigating", "Investigation ongoing"),
    (re.compile(r"keep monitoring|continue monitoring|please monitor", re.I),
     "monitoring", "Continue monitoring in the next shift"),
]


@dataclass
class TeamsUpdate:
    kind: str                      # "shift_change" | "ticket_update" | "general"
    summary: str                   # short cleaned line for the email
    message: TeamsMessage
    ticket_id: str | None = None
    categories: list[str] = field(default_factory=list)
    waiting_for: str | None = None
    action: str | None = None      # e.g. "Follow up with Network team"


@dataclass
class CleaningResult:
    updates: list[TeamsUpdate]                 # everything kept
    removed: list[tuple[TeamsMessage, str]]    # (message, reason) that was filtered out

    @property
    def shift_changes(self) -> list[TeamsUpdate]:
        return [u for u in self.updates if u.kind == "shift_change"]

    @property
    def ticket_updates(self) -> list[TeamsUpdate]:
        return [u for u in self.updates if u.kind == "ticket_update"]

    @property
    def general_updates(self) -> list[TeamsUpdate]:
        return [u for u in self.updates if u.kind == "general"]


class TextCleaner(ABC):
    @abstractmethod
    def clean(self, messages: list[TeamsMessage]) -> CleaningResult:
        ...


def normalize(text: str) -> str:
    """Lower-case, remove punctuation/emoji, collapse spaces."""
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(text.split())


def follow_up_action(waiting_for: str) -> str:
    """'customer confirmation' -> 'Follow up with customer for confirmation'."""
    lower = waiting_for.lower()
    if "customer" in lower:
        return "Follow up with customer for confirmation"
    from_match = re.search(r"from (?:the )?(.+)", waiting_for)
    if from_match:
        suffix = " for approval" if "approval" in lower else ""
        return f"Follow up with {from_match.group(1)}{suffix}"
    target = re.sub(r"\s+(confirmation|response|update|details|approval)$", "", waiting_for,
                    flags=re.IGNORECASE)
    return f"Follow up with {target}"


class RuleBasedTextCleaner(TextCleaner):

    def clean(self, messages: list[TeamsMessage]) -> CleaningResult:
        candidates: list[TeamsUpdate] = []
        removed: list[tuple[TeamsMessage, str]] = []

        for msg in sorted(messages, key=lambda m: m.timestamp):
            reason = self.noise_reason(msg.text)
            if reason:
                removed.append((msg, reason))
            else:
                candidates.append(self.extract(msg))

        # Keep only the latest update per ticket / per identical text.
        latest: dict[str, TeamsUpdate] = {}
        for update in candidates:
            key = update.ticket_id or normalize(update.summary)
            if key in latest:
                removed.append((latest[key].message, "Duplicate / superseded by a later update"))
            latest[key] = update

        kept = sorted(latest.values(), key=lambda u: u.message.timestamp)
        removed.sort(key=lambda item: item[0].timestamp)
        return CleaningResult(updates=kept, removed=removed)

    # -- step 1 + 2 ----------------------------------------------------------
    @staticmethod
    def noise_reason(text: str) -> str | None:
        norm = normalize(text)
        if not norm or norm in NOISE_PHRASES:
            return "Greeting / acknowledgement"
        if not TICKET_ID_RE.search(text) and not OPERATIONAL_KEYWORDS.search(text):
            return "Casual chat - no operational information"
        return None

    # -- step 3 + 4 ----------------------------------------------------------
    def extract(self, msg: TeamsMessage) -> TeamsUpdate:
        shift_change = self.extract_shift_change(msg)
        if shift_change:
            return TeamsUpdate(kind="shift_change", summary=shift_change, message=msg,
                               categories=["shift_change"])

        ticket_ids = TICKET_ID_RE.findall(msg.text)
        if not ticket_ids:
            return TeamsUpdate(kind="general", summary=self.tidy_sentence(msg.text),
                               message=msg, categories=["general"])

        ticket_id = ticket_ids[0]
        categories, phrases = [], []
        for pattern, category, phrase in STATUS_RULES:
            if pattern.search(msg.text):
                categories.append(category)
                phrases.append(phrase)

        action = None
        escalated = ESCALATED_RE.search(msg.text)
        if escalated:
            categories.append("escalated")
            phrases.append(f"escalated to {escalated.group(1).strip()}")
            action = f"Track escalation with {escalated.group(1).strip()}"

        waiting_for = None
        waiting = WAITING_RE.search(msg.text)
        if waiting:
            waiting_for = re.split(r"\s+(?:to|on)\s+", waiting.group(1).strip())[0]
            categories.append("waiting")
            phrases.append(f"waiting for {waiting_for}")
            action = follow_up_action(waiting_for)

        if not action and "monitoring" in categories:
            action = "Continue monitoring"
        if not action and "incident" in categories:
            action = "Continue monitoring the major incident bridge"

        if phrases:
            summary = "; ".join(phrases)
        else:  # no known phrase: fall back to the message without the ticket ID
            summary = TICKET_ID_RE.sub("", msg.text).strip(" -:,.")
        summary = f"{ticket_id}: {summary[0].upper()}{summary[1:]}."

        return TeamsUpdate(kind="ticket_update", summary=summary, message=msg,
                           ticket_id=ticket_id, categories=categories or ["update"],
                           waiting_for=waiting_for, action=action)

    @staticmethod
    def extract_shift_change(msg: TeamsMessage) -> str | None:
        for pattern in SHIFT_CHANGE_PATTERNS:
            match = pattern.search(msg.text)
            if match:
                parts = match.groupdict()
                person = parts.get("person") or msg.author
                when = f" {parts['when']}" if parts.get("when") else ""
                return f"{person} will cover the {parts['shift'].lower()} shift instead of {parts['other']}{when}."
        if SHIFT_CHANGE_HINT.search(msg.text):
            return f"{msg.author}: {RuleBasedTextCleaner.tidy_sentence(msg.text)}"
        return None

    @staticmethod
    def tidy_sentence(text: str) -> str:
        text = re.sub(r"^(heads up|fyi|update|note)\s*[:\-]\s*", "", text.strip(), flags=re.IGNORECASE)
        text = text[0].upper() + text[1:]
        return text if text.endswith((".", "!", "?")) else text + "."


def get_text_cleaner() -> TextCleaner:
    return RuleBasedTextCleaner()
