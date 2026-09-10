const namesEl = document.getElementById("names");
const messageEl = document.getElementById("message");
const attachmentEl = document.getElementById("attachment");
const nameCountEl = document.getElementById("nameCount");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const pulseEl = document.getElementById("pulse");
const saveBtn = document.getElementById("saveBtn");
const runBtn = document.getElementById("runBtn");
const stopBtn = document.getElementById("stopBtn");

let logCursor = 0;
let pollTimer = null;

function parseNames(text) {
  return text
    .split(/\r?\n/)
    .map((n) => n.trim())
    .filter(Boolean);
}

function updateCount() {
  nameCountEl.textContent = String(parseNames(namesEl.value).length);
}

function setStatus(text, kind = "") {
  statusEl.textContent = text || "";
  statusEl.className = "status" + (kind ? ` ${kind}` : "");
}

function payloadFromForm() {
  const attachment = attachmentEl.value.trim();
  return {
    people_names: parseNames(namesEl.value),
    message_template: messageEl.value,
    attachment_path: attachment || null,
  };
}

function applyConfig(cfg) {
  namesEl.value = (cfg.people_names || []).join("\n");
  messageEl.value = cfg.message_template || "";
  attachmentEl.value = cfg.attachment_path || "";
  updateCount();
}

async function loadConfig() {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error("config HTTP " + res.status);
  const cfg = await res.json();
  applyConfig(cfg);
}

async function saveConfig(quiet = false) {
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadFromForm()),
    });
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.error || "Save failed", "error");
      return null;
    }
    if (!quiet) setStatus(`Saved ${data.count} people`, "ok");
    return data;
  } catch (err) {
    setStatus("Cannot reach Sendline server. Run: py app.py", "error");
    return null;
  }
}

function setRunningUi(running) {
  runBtn.disabled = running;
  stopBtn.hidden = !running;
  if (!running) stopBtn.disabled = false;
  if (running) pulseEl.dataset.state = "running";
}

function appendLogs(lines) {
  if (!lines.length) return;
  const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 24;
  logEl.textContent += (logEl.textContent ? "\n" : "") + lines.join("\n");
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
}

async function pollLogs() {
  try {
    const res = await fetch(`/api/logs?after=${logCursor}`);
    const data = await res.json();
    appendLogs(data.lines || []);
    logCursor = data.next ?? logCursor;
    setRunningUi(!!data.running);
    if (!data.running) {
      clearInterval(pollTimer);
      pollTimer = null;
      const code = data.exit_code;
      if (code === 0) {
        pulseEl.dataset.state = "done";
        setStatus("Worker finished successfully", "ok");
      } else if (code == null || code === 130) {
        pulseEl.dataset.state = "done";
        setStatus("Worker stopped — Chrome closed", "ok");
      } else {
        pulseEl.dataset.state = "error";
        setStatus(`Worker failed (exit ${code}). Check the live log.`, "error");
      }
    }
  } catch (_) {
    setStatus("Lost connection to Sendline server", "error");
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollLogs, 700);
  pollLogs();
}

async function launch() {
  try {
    setStatus("Starting…");
    logEl.textContent = "";
    logCursor = 0;
    pulseEl.dataset.state = "running";
    setRunningUi(true);

    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadFromForm()),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(data.error || "Launch failed", "error");
      pulseEl.dataset.state = "error";
      setRunningUi(false);
      return;
    }
    setStatus(`Running for ${data.count} people — watch Chrome + the live log`, "ok");
    startPolling();
  } catch (err) {
    setStatus("Cannot reach Sendline server. Run: py app.py", "error");
    pulseEl.dataset.state = "error";
    setRunningUi(false);
  }
}

async function stop() {
  try {
    stopBtn.disabled = true;
    const res = await fetch("/api/stop", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      stopBtn.disabled = false;
      setStatus(data.error || "Stop failed", "error");
      return;
    }
    setStatus("Stopping — closing Chrome…", "ok");
  } catch (_) {
    stopBtn.disabled = false;
    setStatus("Cannot reach Sendline server", "error");
  }
}

namesEl.addEventListener("input", updateCount);
saveBtn.addEventListener("click", () => saveConfig(false));
runBtn.addEventListener("click", launch);
stopBtn.addEventListener("click", stop);

loadConfig().catch(() => setStatus("Could not load config — is py app.py running?", "error"));
