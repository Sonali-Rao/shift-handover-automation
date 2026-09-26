// Dashboard logic: just calls the backend API and shows the results.
const $ = (id) => document.getElementById(id);

const STEPS = [
  "Tickets loaded",
  "Teams updates loaded",
  "Information cleaned",
  "Ticket summary created",
  "Important updates extracted",
  "Handover generated",
];

let handover = null;   // latest generated handover from the API
let chatTab = "removed";

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatDateTime(iso) {
  return new Date(iso).toLocaleString([], { day: "2-digit", month: "short", hour: "numeric", minute: "2-digit" });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

// ---- Status -----------------------------------------------------------------
async function loadStatus() {
  try {
    const s = await api("/api/status");
    $("currentShift").textContent = s.current_shift.name.replace(" Shift", "");
    $("shiftEnd").textContent = formatTime(s.current_shift.end);
    $("nextRun").textContent = s.next_run ? formatTime(s.next_run.next_run) : "Scheduler off";
    $("recipients").textContent = s.recipients;
    if (s.last_run) {
      $("lastRun").textContent = `${formatTime(s.last_run.at)} (${s.last_run.trigger})`;
    }
    $("shiftSelect").options[0].textContent = `Current shift (${s.current_shift.name})`;
    $("jobs").innerHTML = s.scheduled_jobs.length
      ? s.scheduled_jobs.map((j) => `<li><span>${escapeHtml(j.name)}</span><span>${formatDateTime(j.next_run)}</span></li>`).join("")
      : "<li>Scheduler disabled (ENABLE_SCHEDULER=false)</li>";
  } catch (err) {
    setStatus("Backend not reachable", true);
  }
}

function setStatus(text, busy = false) {
  const pill = $("automationStatus");
  pill.lastChild.textContent = `Automation: ${text}`;
  pill.classList.toggle("busy", busy);
}

// ---- Generate -----------------------------------------------------------------
function renderProgress(doneCount = 0, details = []) {
  $("progress").innerHTML = STEPS.map((label, i) => {
    const done = i < doneCount;
    const detail = done && details[i] ? `<span class="detail">${escapeHtml(details[i])}</span>` : "";
    return `<li class="${done ? "done" : ""}"><span class="mark">${done ? "✓" : "○"}</span>${label}${detail}</li>`;
  }).join("");
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function generate() {
  $("generateBtn").disabled = true;
  $("sendResult").hidden = true;
  setStatus("Generating…", true);
  renderProgress(0);
  try {
    const result = await api("/api/handover/generate", {
      method: "POST",
      body: JSON.stringify({ shift: $("shiftSelect").value || null }),
    });
    // Tick the steps one by one so the pipeline is visible.
    const details = result.steps.map((s) => s.detail);
    for (let i = 1; i <= STEPS.length; i++) {
      renderProgress(i, details);
      await sleep(250);
    }
    showHandover(result);
    setStatus("Ready");
    loadStatus();
  } catch (err) {
    setStatus("Error", true);
    showSendResult(`✗ ${err.message}`, true);
  } finally {
    $("generateBtn").disabled = false;
  }
}

function showHandover(result) {
  handover = result;
  document.querySelectorAll("#summaryCards strong").forEach((el) => {
    el.textContent = result.summary[el.dataset.key];
  });
  $("emailMeta").hidden = false;
  $("emailTo").textContent = $("recipients").textContent;
  $("emailSubject").textContent = result.subject;
  $("emailBody").innerHTML = result.html;   // HTML produced by our own backend template
  $("copyBtn").disabled = false;
  $("sendBtn").disabled = false;
  renderChat();
}

// ---- Teams chat panel --------------------------------------------------------
function renderChat() {
  if (!handover) return;
  const { kept, removed } = handover.teams;
  $("chatCounts").innerHTML =
    `<b>${kept.length + removed.length}</b> messages · <b>${kept.length}</b> kept · <b>${removed.length}</b> filtered out`;
  const items = chatTab === "kept"
    ? kept.map((m) => `<li><div class="who">${m.time} · ${escapeHtml(m.author)} · ${escapeHtml(m.team)}</div>
        <div class="text">${escapeHtml(m.original)}</div>
        <div class="summary">→ ${escapeHtml(m.summary)}</div></li>`)
    : removed.map((m) => `<li class="removed"><div class="who">${m.time} · ${escapeHtml(m.author)} · ${escapeHtml(m.team)}</div>
        <div class="text">${escapeHtml(m.original)}</div>
        <div class="reason">${escapeHtml(m.reason)}</div></li>`);
  $("chatList").innerHTML = items.join("");
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    chatTab = tab.dataset.tab;
    renderChat();
  });
});

// ---- Copy + Send ----------------------------------------------------------------
const plainText = () => `Subject: ${handover.subject}\n\n${handover.text}`;

async function copyEmail() {
  if (!handover) return;
  const html = `<p><strong>Subject:</strong> ${escapeHtml(handover.subject)}</p>${handover.html}`;
  try {
    // Rich copy (pastes as formatted email in Outlook), with plain text fallback.
    await navigator.clipboard.write([new ClipboardItem({
      "text/html": new Blob([html], { type: "text/html" }),
      "text/plain": new Blob([plainText()], { type: "text/plain" }),
    })]);
  } catch {
    // Older/restricted browsers: copy the plain-text version via a hidden textarea.
    const area = document.createElement("textarea");
    area.value = plainText();
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    if (!ok) return showSendResult("✗ Copy blocked by the browser – select the email text manually.", true);
  }
  $("copyBtn").textContent = "✓ Copied";
  setTimeout(() => ($("copyBtn").textContent = "Copy Email"), 1500);
}

async function sendEmail() {
  $("sendBtn").disabled = true;
  try {
    const r = await api("/api/handover/send", { method: "POST" });
    showSendResult(
      `✓ Handover email generated successfully.<br>✓ Email sent to: ${escapeHtml(r.recipients)}` +
      `<small>Mock send – saved as ${escapeHtml(r.file)}</small>`
    );
    loadStatus();
  } catch (err) {
    showSendResult(`✗ ${escapeHtml(err.message)}`, true);
  } finally {
    $("sendBtn").disabled = false;
  }
}

function showSendResult(html, isError = false) {
  const box = $("sendResult");
  box.innerHTML = html;
  box.classList.toggle("error", isError);
  box.hidden = false;
}

// ---- Start --------------------------------------------------------------------
$("generateBtn").addEventListener("click", generate);
$("copyBtn").addEventListener("click", copyEmail);
$("sendBtn").addEventListener("click", sendEmail);

renderProgress(0);
loadStatus();
setInterval(loadStatus, 30000);
// Show a handover the scheduler may already have produced.
api("/api/handover/latest").then((h) => h && showHandover(h)).catch(() => {});
