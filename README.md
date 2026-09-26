# Shift Handover Mail Automation

A small automation that writes the **L1 Cloud Operations shift-handover email** for you.

> Instead of manually reading tickets and Teams messages and copy-pasting everything into a
> shift-handover email, this automation collects the information, filters the useful updates,
> organizes the shift summary, fills a predefined email template, and automatically sends the
> handover to L1 after every shift.

---

## 1. The real-world problem

L1 Cloud Operations assigns tickets to five assignment groups:
**Azure Cloud Operations, AWS Cloud Operations, OpenShift, GCP, OCI**.
During the shift, engineers post updates in Microsoft Teams.

At the end of every shift, L1 manually:

- counts tickets received, open, on hold, near SLA breach, breached, high priority, and aging
- reads the whole Teams chat, ignores "Good morning" / "Thanks" / "Noted", and picks the useful updates
- notes shift changes ("Neha is covering the evening shift instead of Amit")
- copies all of it into the same email format and sends it to the L1 team

This is the same work every shift. This project automates it.

## 2. Architecture

```text
   Tickets (JSON)        Teams chat (JSON)
         │                      │
         └──────────┬───────────┘
                    ↓
          Clean / filter chat        services/cleaner.py
                    ↓
          Organize tickets           automation/processor.py
                    ↓
          Generate handover          automation/handover_generator.py
                    ↓                + templates/handover_email.html / .txt
          Send email (mock)          services/email_service.py
                    ↓
            L1 recipients
```

The same `generate_handover()` function is called by **both** the dashboard button and the scheduler.

### Project structure

```text
.
├── README.md
├── requirements.txt
├── .env.example                  # all settings (copy to .env)
├── backend/
│   ├── main.py                   # FastAPI app: API + serves the dashboard
│   ├── config.py                 # reads settings from .env (the only place for them)
│   ├── automation/
│   │   ├── shifts.py             # shift windows (morning / afternoon / night)
│   │   ├── processor.py          # ticket rules: SLA risk, breach, aging, on hold, urgency
│   │   ├── handover_generator.py # THE pipeline: load → clean → count → fill template → save → send
│   │   └── scheduler.py          # APScheduler jobs at each shift end
│   ├── sources/
│   │   ├── tickets.py            # TicketSource → MockTicketSource
│   │   └── teams.py              # TeamsSource  → MockTeamsSource
│   ├── services/
│   │   ├── cleaner.py            # TextCleaner  → RuleBasedTextCleaner
│   │   └── email_service.py      # EmailService → MockEmailService (+ Graph stub)
│   └── templates/
│       ├── handover_email.html   # the email (HTML version)
│       └── handover_email.txt    # the email (plain-text version)
├── frontend/                     # index.html, style.css, script.js (vanilla JS)
├── data/
│   ├── tickets.json              # 30 mock tickets
│   └── teams_messages.json       # 40 mock Teams messages
└── tests/test_automation.py      # pytest tests for the rules
```

## 3. Install (Windows)

Needs Python 3.10 or newer. Open a terminal (PowerShell) in the project folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

If PowerShell blocks `Activate.ps1`, run `Set-ExecutionPolicy -Scope Process Bypass` first
(or use `.venv\Scripts\activate.bat` in Command Prompt).
Copying `.env` is optional because every setting has a default.

## 4. Run

One command starts the backend **and** serves the frontend:

```powershell
python -m uvicorn backend.main:app --port 8000
```

Open **http://localhost:8000** and click **Generate Handover**.

Run the tests:

```powershell
python -m pytest -v
```

## 5. How the scheduler works

`backend/automation/scheduler.py` registers one APScheduler cron job per shift when the app starts:

| Shift | Window (each shift starts when the previous one ends) | Handover runs at |
|---|---|---|
| Night | 10:30 PM (previous day) → 6:30 AM | **6:30 AM** |
| Morning | 6:30 AM → 2:30 PM | **2:30 PM** |
| Afternoon | 2:30 PM → 10:30 PM | **10:30 PM** |

The times come from `.env`:

```env
MORNING_SHIFT_END=14:30
AFTERNOON_SHIFT_END=22:30
NIGHT_SHIFT_END=06:30
TIMEZONE=Asia/Kolkata
AGING_THRESHOLD_HOURS=24
SLA_RISK_WINDOW_HOURS=2
```

At shift end the job calls `run_scheduled_handover(shift)`, which runs `generate_handover()`
and then `send_handover()`. The email is sent with no manual step.
The dashboard shows the **next automatic run**, and the "Scheduled Runs" panel lists all three jobs.
Set `ENABLE_SCHEDULER=false` to turn it off. The app must be running for scheduled jobs to fire.

## 6. Generate a handover manually

- **Dashboard:** pick a shift (default: current shift) and click **Generate Handover**.
  You will see the steps tick off:
  `✓ Tickets loaded → ✓ Teams updates loaded → ✓ Information cleaned → ✓ Ticket summary created → ✓ Important updates extracted → ✓ Handover generated`.
  Then use **Copy Email** or **Send Email**.
- **API:**

| Method | URL | What it does |
|---|---|---|
| GET | `/api/status` | current shift, shift end, scheduled jobs, last run |
| POST | `/api/handover/generate` | body `{"shift": "morning"}` (optional); returns the email + data |
| GET | `/api/handover/latest` | last generated handover (or `null`) |
| POST | `/api/handover/send` | sends the last generated handover (mock) |

Interactive API docs: http://localhost:8000/docs

The handover is always calculated **as of the shift end time**. Every run is saved to
`output/handovers/handover_<date>_<shift>.html` and `.txt`.

## 7. Mock ticket data (`data/tickets.json`)

```json
{
  "ticketId": "INC0012456",
  "assignmentGroup": "Azure Cloud Operations",
  "shortDescription": "VM connectivity issue in East US production subnet",
  "priority": "P2",
  "status": "In Progress",
  "assignee": "Rahul",
  "createdAt": "2026-09-26T09:04:00+05:30",
  "updatedAt": "2026-09-26T14:06:00+05:30",
  "slaDueAt": "2026-09-26T15:30:00+05:30",
  "onHold": false,
  "holdReason": null,
  "majorIncident": false
}
```

| Field | Meaning |
|---|---|
| `priority` | P1–P4 |
| `status` | `Open`, `In Progress`, `On Hold`, `Resolved` |
| `slaDueAt` | when the SLA breaches |
| `onHold` / `holdReason` | on hold and what it is waiting for (customer, team, vendor, approval, information) |
| `majorIncident` | optional flag for major incidents |

**Rules** (`processor.py`, all "as of" shift end):

| Metric | Rule |
|---|---|
| Generated | created during the shift window |
| Open | not resolved (includes on hold) |
| On Hold | not resolved and on hold |
| SLA Risk | not resolved and SLA due within `SLA_RISK_WINDOW_HOURS` (2h) after shift end |
| SLA Breached | not resolved and SLA due time already passed |
| Important | not resolved and P1/P2 or major incident |
| Aging | not resolved and open longer than `AGING_THRESHOLD_HOURS` (24h) |

**Urgency.** There is no scoring: the first matching rule wins.

| Level | When |
|---|---|
| Critical | P1, SLA breached, or major incident |
| High | P2 or SLA approaching breach |
| Medium | aging, escalated, or on hold (needs follow-up) |
| Normal | any other open ticket |

Critical and High tickets appear in **Important / Urgent Tickets**.

**Demo "replay".** The JSON files contain `"referenceShiftEnd": "2026-09-26T14:30:00+05:30"`.
The mock sources move every timestamp by the same offset so the data always lines up with the
shift you generate, whatever the date. That keeps the demo meaningful whenever you run it.
A real source would not need this.

## 8. Mock Teams data (`data/teams_messages.json`)

```json
{ "id": "MSG010", "timestamp": "2026-09-26T09:10:00+05:30", "author": "Rahul", "team": "Azure",
  "message": "INC0012456 is still being investigated. ... Waiting for the Network team." }
```

The 40 messages mix useful updates with noise on purpose: greetings, "Thanks", "Noted", emoji,
lunch plans, duplicate posts, shift changes, escalations, and resolutions.

**Cleaning rules** (`services/cleaner.py`, `RuleBasedTextCleaner`):

1. **Greetings / acknowledgements** such as "Good morning", "Hey", "Thanks", "Okay", "Noted" and 👍 are removed.
2. **Casual chat** with no ticket ID and no operations keyword (outage, escalated, waiting, advisory, …) is removed.
3. **Shift changes** are rewritten.
   "I will be covering the evening shift instead of Amit today." becomes
   *Neha will cover the evening shift instead of Amit today.*
4. **Ticket updates** are summarized.
   "INC0012456 is still being investigated … Waiting for the Network team." becomes
   *INC0012456: Investigation ongoing; waiting for Network team.*
   and the action becomes *Follow up with Network team.*
5. **Duplicates.** Only the latest update per ticket is kept.

The dashboard's **Teams Chat Cleaning** panel shows every filtered message with its reason,
and every kept message with its summary.

**Optional AI later.** `TextCleaner` is an interface. An `LLMTextCleaner` could send only the
*kept* messages to an LLM for smoother summaries. It is not required: everything works offline.

## 9. How the email template works

`backend/templates/handover_email.html` (and the `.txt` twin) is a fixed layout written in
[Jinja2](https://jinja.palletsprojects.com/). The generator builds a dictionary `h` and the
template inserts its values:

```html
<p>Please find below the shift handover details for the <strong>{{ h.shift_name }}</strong> ({{ h.shift_timing }}).</p>
{% for r in h.groups %}
  <tr><td>{{ r.group }}</td><td>{{ r.generated }}</td><td>{{ r.open }}</td> ... </tr>
{% endfor %}
```

The sections are: Ticket Summary, Important / Urgent Tickets, Aging Tickets, Tickets On Hold,
Important Team Updates, and Action Required. To change the email wording, edit the template.
No Python changes are needed.

Subject: `Shift Handover – Cloud Operations – <Shift Name> – <DD Mon YYYY>`

## 10. Future integrations

Each data source and output uses a small interface. Adding a real system means adding one
class and returning it from the `get_…()` function. The pipeline does not change.

| Real system | Where | How |
|---|---|---|
| Ticketing system (ServiceNow, Jira, …) | `sources/tickets.py` | a `TicketSource` subclass that calls the tool's REST API and returns `Ticket` objects |
| Microsoft Teams | `sources/teams.py` | `MicrosoftGraphTeamsSource` using Graph `GET /teams/{id}/channels/{id}/messages` (steps documented in the file) |
| Outlook | `services/email_service.py` | `GraphEmailService` using Graph `POST /users/{sender}/sendMail` (stub + steps included); set `EMAIL_PROVIDER=graph` |

In the demo, **Send Email** is simulated. `MockEmailService` writes a real `.eml` file to
`output/sent_emails/`, which you can open in Outlook to see the exact email.

## 11. Deploy (Render, free)

The repo includes `render.yaml`, so Render configures everything automatically:

1. Push this project to GitHub.
2. On [render.com](https://render.com): **New → Blueprint** → select the repository → **Apply**.
3. Open the URL Render gives you (e.g. `https://shift-handover-automation.onrender.com`).

Free plan notes: the service sleeps after ~15 minutes idle (first visit takes ~30-60 s to wake),
scheduled runs only fire while it is awake, and files in `output/` are cleared on restart.
The **Generate Handover** button always works.

## 12. Security

- The demo needs **no credentials** and connects to **no real company system**.
- Never commit real passwords, client secrets, tokens, or API keys. `.env` is in `.gitignore`.
  Only `.env.example` (no secrets) is committed.
- For real integrations, load secrets from environment variables or a secret store
  (e.g. Azure Key Vault), and grant the app only the Graph permissions it needs
  (`ChannelMessage.Read.All`, `Mail.Send`).
- Mock data uses fake names and ticket numbers.
