const namesEl = document.getElementById("names");
const namesField = document.getElementById("namesField");
const namesFileEl = document.getElementById("namesFile");
const uploadNamesBtn = document.getElementById("uploadNamesBtn");
const uploadProgress = document.getElementById("uploadProgress");
const uploadProgressFill = document.getElementById("uploadProgressFill");
const uploadProgressLabel = document.getElementById("uploadProgressLabel");
const messageEl = document.getElementById("message");
const linkedinUsernameEl = document.getElementById("linkedinUsername");
const linkedinPasswordEl = document.getElementById("linkedinPassword");
const linkedinStatusEl = document.getElementById("linkedinStatus");
const togglePasswordBtn = document.getElementById("togglePasswordBtn");
const attachmentField = document.getElementById("attachmentField");
const attachmentFileEl = document.getElementById("attachmentFile");
const uploadAttachmentBtn = document.getElementById("uploadAttachmentBtn");
const clearAttachmentBtn = document.getElementById("clearAttachmentBtn");
const attachmentNameEl = document.getElementById("attachmentName");
const attachmentProgress = document.getElementById("attachmentProgress");
const attachmentProgressFill = document.getElementById("attachmentProgressFill");
const attachmentProgressLabel = document.getElementById("attachmentProgressLabel");
const nameCountEl = document.getElementById("nameCount");
const paceUsageEl = document.getElementById("paceUsage");
const paceHintEl = document.getElementById("paceHint");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const pulseEl = document.getElementById("pulse");
const saveBtn = document.getElementById("saveBtn");
const runBtn = document.getElementById("runBtn");
const stopBtn = document.getElementById("stopBtn");
const userEmailEl = document.getElementById("userEmail");
const adminLinkEl = document.getElementById("adminLink");
const logoutBtn = document.getElementById("logoutBtn");

const MAX_NAMES_FILE_BYTES = 8 * 1024 * 1024;
const TEMPLATE_NAME_HEADER = "name";
const TEMPLATE_ERROR =
  "This file doesn’t match the template. Download the template, keep the name header, and put one full name per row.";

async function api(url, options = {}) {
  const res = await fetch(url, { credentials: "same-origin", ...options });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("auth");
  }
  return res;
}

function isActiveStatus(status) {
  return status === "queued" || status === "running" || status === "stopping";
}

let logCursor = 0;
let pollTimer = null;
let namesImporting = false;
let attachmentPath = null;
let attachmentName = null;
let attachmentUploading = false;
let stopRequested = false;
let linkedinConnected = false;
let currentJobId = null;
let hadActiveJob = false;
let jobPeopleCount = 0;

const STATUS_ICONS = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.2 4.2L19 7.5"/></svg>',
  stopped: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5"/><path d="M12 16.5h.01"/><path d="M12 3.5L21 19H3L12 3.5z"/></svg>',
  error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M15 9l-6 6"/><path d="M9 9l6 6"/></svg>',
};

function parseNames(text) {
  return text
    .split(/\r?\n/)
    .map((n) => n.trim())
    .filter(Boolean);
}

function updateCount() {
  const n = parseNames(namesEl.value).length;
  const label = n === 1 ? "1 person" : n + " people";
  nameCountEl.textContent = label;
  nameCountEl.title = n === 1 ? "1 person loaded" : n + " people loaded";
  updatePaceSummary();
}

const PACE = {
  careful: { daily: 20, weekly: 80 },
  established: { daily: 35, weekly: 120 },
};

let paceRecent = [];
let paceCounts = { today: 0, week: 0 };

function selectedPace() {
  const checked = document.querySelector('input[name="pace"]:checked');
  const value = checked ? checked.value : "careful";
  return PACE[value] ? value : "careful";
}

function rememberPace(pace) {
  if (!pace) return;
  paceCounts = { today: Number(pace.today) || 0, week: Number(pace.week) || 0 };
  paceRecent = Array.isArray(pace.recent_names) ? pace.recent_names : [];
  updatePaceSummary();
}

function updatePaceSummary() {
  if (!paceUsageEl) return;
  const preset = PACE[selectedPace()];
  const names = parseNames(namesEl.value);
  const recent = new Set(paceRecent);
  const left = names.filter((name) => !recent.has(String(name).trim().toLowerCase())).length;
  const days = left ? Math.ceil(left / preset.daily) : 0;
  paceUsageEl.textContent = "Today " + paceCounts.today + "/" + preset.daily + " · week " + paceCounts.week + "/" + preset.weekly;
  if (!paceHintEl) return;
  const daysLabel = days === 1 ? "1 day" : days + " days";
  const queue = left
    ? left + " not messaged in the last 90 days — about " + daysLabel + " at this pace. "
    : "Everyone on this list was messaged in the last 90 days. ";
  paceHintEl.textContent =
    queue +
    "LinkedIn can still limit the account. Press Start again after a cap and people already messaged are skipped.";
}

function setStatus(text, kind = "") {
  let visual = kind;
  if (!visual && text) {
    const t = text.toLowerCase();
    if (
      t.includes("starting") ||
      t.includes("running") ||
      t.includes("stopping") ||
      t.includes("processing") ||
      t.includes("uploading")
    ) {
      visual = "running";
    }
  }
  statusEl.className = "status" + (visual ? ` ${visual}` : "");
  statusEl.replaceChildren();
  if (!text) return;
  if (visual === "ok" || visual === "stopped" || visual === "error") {
    const icon = document.createElement("span");
    icon.className = "status-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = STATUS_ICONS[visual];
    const label = document.createElement("span");
    label.textContent = text;
    statusEl.append(icon, label);
    return;
  }
  statusEl.textContent = text;
}

function payloadFromForm() {
  return {
    people_names: parseNames(namesEl.value),
    message_template: messageEl.value,
    linkedin_username: (linkedinUsernameEl.value || "").trim(),
    linkedin_password: linkedinPasswordEl.value || "",
    attachment_path: attachmentPath,
    attachment_name: attachmentName,
    pace_preset: selectedPace(),
  };
}

function fileNameFromPath(path) {
  if (!path) return "";
  const parts = String(path).split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

function setAttachmentUi() {
  const hasFile = Boolean(attachmentPath);
  attachmentNameEl.textContent = hasFile ? attachmentName || fileNameFromPath(attachmentPath) : "No file chosen";
  attachmentNameEl.classList.toggle("has-file", hasFile);
  clearAttachmentBtn.hidden = !hasFile;
}

function applyConfig(cfg) {
  namesEl.value = (cfg.people_names || []).join("\n");
  messageEl.value = cfg.message_template || "";
  linkedinUsernameEl.value = cfg.linkedin_username || "";
  linkedinPasswordEl.value = "";
  linkedinConnected = Boolean(cfg.linkedin_connected);
  linkedinPasswordEl.placeholder = linkedinConnected ? "Leave blank to keep saved password" : "Password";
  if (linkedinStatusEl) {
    linkedinStatusEl.textContent = linkedinConnected ? "saved" : "used to sign in";
  }
  attachmentPath = cfg.attachment_path || null;
  attachmentName = cfg.attachment_name || fileNameFromPath(attachmentPath) || null;
  const preset = cfg.pace_preset || (cfg.pace && cfg.pace.preset) || "careful";
  const paceInput = document.querySelector('input[name="pace"][value="' + preset + '"]');
  if (paceInput) paceInput.checked = true;
  rememberPace(cfg.pace);
  setAttachmentUi();
  updateCount();
}

async function loadConfig() {
  const res = await api("/api/config");
  if (!res.ok) throw new Error("config HTTP " + res.status);
  const cfg = await res.json();
  applyConfig(cfg);
}

async function saveConfig(quiet = false) {
  try {
    const res = await api("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadFromForm()),
    });
    const data = await res.json();
    if (!res.ok) {
      if (!quiet) setStatus(data.error || "Save failed", "error");
      return null;
    }
    if (data.config) applyConfig(data.config);
    if (!quiet) setStatus(`Saved ${data.count} people`, "ok");
    return data;
  } catch (err) {
    if (err && err.message === "auth") return null;
    if (!quiet) setStatus("Cannot reach Sendline server. Run: py app.py", "error");
    return null;
  }
}

function yieldUi() {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function showUploadProgress(pct, label) {
  uploadProgress.hidden = false;
  const clamped = Math.max(0, Math.min(100, Math.round(pct)));
  uploadProgressFill.style.width = clamped + "%";
  uploadProgressLabel.textContent = label || clamped + "%";
  uploadProgress.setAttribute("aria-valuenow", String(clamped));
}

function hideUploadProgress() {
  uploadProgress.hidden = true;
  uploadProgressFill.style.width = "0%";
  uploadProgressLabel.textContent = "0%";
}

function headerKey(value) {
  return String(value || "")
    .replace(/^\uFEFF/, "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");
}

function looksLikeEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || "").trim());
}

function isTemplateNameHeader(value) {
  return headerKey(value) === TEMPLATE_NAME_HEADER;
}

function templateError() {
  const err = new Error(TEMPLATE_ERROR);
  err.code = "TEMPLATE";
  return err;
}

function detectDelimiter(sample) {
  const comma = (sample.match(/,/g) || []).length;
  const semi = (sample.match(/;/g) || []).length;
  const tab = (sample.match(/\t/g) || []).length;
  if (tab > comma && tab > semi) return "\t";
  if (semi > comma) return ";";
  return ",";
}

function parseDelimitedLine(line, delimiter) {
  const out = [];
  let cur = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (line[i + 1] === '"') {
          cur += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cur += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === delimiter) {
      out.push(cur.trim());
      cur = "";
    } else {
      cur += c;
    }
  }
  out.push(cur.trim());
  return out;
}

async function extractNamesFromText(text, kind, onProgress) {
  const cleaned = String(text || "").replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = cleaned.split("\n");
  const names = [];
  const seen = new Set();
  let delimiter = ",";
  let nameIndex = 0;
  let start = 0;

  if (kind === "csv") {
    const firstData = lines.find((line) => line.trim());
    if (!firstData) throw templateError();
    delimiter = detectDelimiter(firstData);
    const headerCells = parseDelimitedLine(firstData, delimiter);
    nameIndex = headerCells.findIndex(isTemplateNameHeader);
    if (nameIndex < 0) throw templateError();
    start = lines.indexOf(firstData) + 1;
  } else if (kind === "txt") {
    const firstData = lines.find((line) => line.trim());
    if (firstData && isTemplateNameHeader(firstData.trim())) {
      start = lines.indexOf(firstData) + 1;
    }
  }

  const chunk = Math.max(40, Math.ceil(lines.length / 24));
  for (let i = start; i < lines.length; i += chunk) {
    const end = Math.min(lines.length, i + chunk);
    for (let j = i; j < end; j++) {
      const raw = lines[j];
      if (!raw || !raw.trim()) continue;
      let name = "";
      if (kind === "csv") {
        const cells = parseDelimitedLine(raw, delimiter);
        name = cells[nameIndex] || "";
      } else {
        name = raw.trim();
      }
      name = name.replace(/\s+/g, " ").trim();
      if (!name || looksLikeEmail(name) || isTemplateNameHeader(name)) continue;
      const key = name.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      names.push(name);
    }
    const pct = 18 + ((end - start) / Math.max(1, lines.length - start)) * 80;
    await onProgress(pct, "Processing names…");
  }
  return names;
}

function fileKind(file) {
  const name = (file.name || "").toLowerCase();
  if (name.endsWith(".csv")) return "csv";
  if (name.endsWith(".txt")) return "txt";
  const type = (file.type || "").toLowerCase();
  if (type.includes("csv")) return "csv";
  if (type.includes("text")) return "txt";
  return "";
}

async function importNamesFile(file) {
  if (!file || namesImporting) return;
  const kind = fileKind(file);
  if (!kind) {
    setStatus("Please upload a .txt or .csv file.", "error");
    return;
  }
  if (file.size > MAX_NAMES_FILE_BYTES) {
    setStatus("That file is too large. Please keep it under 8 MB.", "error");
    return;
  }

  namesImporting = true;
  uploadNamesBtn.disabled = true;
  namesEl.setAttribute("aria-busy", "true");
  showUploadProgress(4, "Reading file…");
  setStatus("Processing " + file.name + "…");

  const started = Date.now();
  try {
    await yieldUi();
    const text = await file.text();
    showUploadProgress(16, "Reading file…");
    await yieldUi();

    const names = await extractNamesFromText(text, kind, async (pct, label) => {
      showUploadProgress(pct, label);
      await yieldUi();
    });

    showUploadProgress(98, "Loading names…");
    await yieldUi();

    const elapsed = Date.now() - started;
    if (elapsed < 420) await sleep(420 - elapsed);

    if (!names.length) {
      hideUploadProgress();
      setStatus(
        kind === "csv"
          ? "The name column is empty. Add people to the template, then upload."
          : "No names found. Add one full name per line, or use the CSV template.",
        "error"
      );
      return;
    }

    namesEl.value = names.join("\n");
    updateCount();
    showUploadProgress(100, "100%");
    await sleep(180);
    hideUploadProgress();
    setStatus(`Loaded ${names.length} people from ${file.name}`, "ok");
    saveConfig(true);
  } catch (err) {
    hideUploadProgress();
    setStatus(err && err.code === "TEMPLATE" ? TEMPLATE_ERROR : "Could not read that file. Download the template and upload the filled CSV.", "error");
  } finally {
    namesImporting = false;
    uploadNamesBtn.disabled = false;
    namesEl.removeAttribute("aria-busy");
    namesFileEl.value = "";
  }
}

function isNamesFile(file) {
  return Boolean(file && fileKind(file));
}

const ATTACHMENT_EXTS = [".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".gif"];

function isAttachmentFile(file) {
  const name = (file && file.name ? file.name : "").toLowerCase();
  return ATTACHMENT_EXTS.some((ext) => name.endsWith(ext));
}

function showAttachmentProgress(pct) {
  attachmentProgress.hidden = false;
  const clamped = Math.max(0, Math.min(100, Math.round(pct)));
  attachmentProgressFill.style.width = clamped + "%";
  attachmentProgressLabel.textContent = clamped + "%";
  attachmentProgress.setAttribute("aria-valuenow", String(clamped));
}

function hideAttachmentProgress() {
  attachmentProgress.hidden = true;
  attachmentProgressFill.style.width = "0%";
  attachmentProgressLabel.textContent = "0%";
}

function uploadAttachmentFile(file) {
  if (!file || attachmentUploading) return;
  if (!isAttachmentFile(file)) {
    setStatus("Please attach a PDF, Word, PowerPoint, Excel, or image file.", "error");
    return;
  }

  attachmentUploading = true;
  uploadAttachmentBtn.disabled = true;
  showAttachmentProgress(2);
  setStatus("Uploading " + file.name + "…");

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/attachment");
  xhr.withCredentials = true;
  xhr.upload.addEventListener("progress", (event) => {
    if (!event.lengthComputable) return;
    showAttachmentProgress((event.loaded / event.total) * 100);
  });
  xhr.addEventListener("load", () => {
    attachmentUploading = false;
    uploadAttachmentBtn.disabled = false;
    attachmentFileEl.value = "";
    hideAttachmentProgress();
    let data = {};
    try {
      data = JSON.parse(xhr.responseText);
    } catch (_) {}
    if (xhr.status === 401) {
      window.location.href = "/login";
      return;
    }
    if (xhr.status < 200 || xhr.status >= 300 || !data.ok) {
      setStatus(data.error || "Attachment upload failed", "error");
      return;
    }
    attachmentPath = data.attachment_path;
    attachmentName = data.attachment_name;
    setAttachmentUi();
    setStatus("Attached " + attachmentName, "ok");
  });
  xhr.addEventListener("error", () => {
    attachmentUploading = false;
    uploadAttachmentBtn.disabled = false;
    attachmentFileEl.value = "";
    hideAttachmentProgress();
    setStatus("Cannot reach Sendline server. Run: py app.py", "error");
  });
  const body = new FormData();
  body.append("file", file);
  xhr.send(body);
}

async function clearAttachment() {
  try {
    const res = await api("/api/attachment", { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(data.error || "Could not remove attachment", "error");
      return;
    }
    attachmentPath = null;
    attachmentName = null;
    setAttachmentUi();
    setStatus("Attachment removed", "ok");
  } catch (_) {
    setStatus("Cannot reach Sendline server", "error");
  }
}

function setRunningUi(active) {
  runBtn.disabled = active;
  stopBtn.hidden = !active;
  uploadAttachmentBtn.disabled = active || attachmentUploading;
  clearAttachmentBtn.disabled = active;
  linkedinUsernameEl.disabled = active;
  linkedinPasswordEl.disabled = active;
  togglePasswordBtn.disabled = active;
  document.querySelectorAll('input[name="pace"]').forEach((input) => {
    input.disabled = active;
  });
  if (!active) stopBtn.disabled = false;
  if (active) pulseEl.dataset.state = "running";
}

function applyJobState(data) {
  const status = data.job_status || (data.running ? "running" : "idle");
  const mine = Boolean(data.is_mine);
  const active = mine && isActiveStatus(status);
  if (data.job_id && data.job_id !== currentJobId) {
    if (status === "running" || status === "queued") {
      logCursor = 0;
    }
    currentJobId = data.job_id;
  }
  if (typeof data.people_count === "number") jobPeopleCount = data.people_count;
  setRunningUi(active);
  if (active) hadActiveJob = true;
  if (status === "queued" && mine) {
    const n = data.queue_position || 1;
    setStatus("Waiting — another campaign is using Chrome (position " + n + ")", "running");
    pulseEl.dataset.state = "running";
  } else if (status === "running" && mine) {
    const count = data.people_count || jobPeopleCount;
    setStatus(
      count ? "Running for " + count + " people — watch Chrome + the live log" : "Running — watch Chrome + the live log",
      "running"
    );
    pulseEl.dataset.state = "running";
  } else if (status === "stopping" && mine) {
    setStatus("Stopping — closing Chrome…", "running");
    pulseEl.dataset.state = "running";
  }
  return { active, status, mine };
}

function logLineClass(line) {
  const raw = String(line || "");
  const t = raw.toLowerCase();
  if (
    t.includes("stop requested") ||
    t.includes("forcing exit") ||
    t.includes("did not stop in time") ||
    t.includes("exit 1") ||
    t.includes("exit 75") ||
    t.includes("pace reached") ||
    t.includes("account limit")
  ) {
    return "log-line log-line--warn";
  }
  if (t.includes("error") || t.includes("failed")) return "log-line log-line--err";
  if (t.includes("worker finished (exit 0)") || t.includes("successfully")) return "log-line log-line--ok";
  if (t.includes("[info]")) return "log-line log-line--info";
  return "log-line";
}

function appendLogs(lines) {
  if (!lines.length) return;
  const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 24;
  const frag = document.createDocumentFragment();
  for (const line of lines) {
    const row = document.createElement("span");
    row.className = logLineClass(line);
    row.textContent = line + "\n";
    frag.appendChild(row);
  }
  logEl.appendChild(frag);
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
}

async function pollLogs() {
  try {
    const res = await api(`/api/logs?after=${logCursor}`);
    const data = await res.json();
    appendLogs(data.lines || []);
    logCursor = data.next ?? logCursor;
    if (data.pace) rememberPace(data.pace);
    const state = applyJobState(data);
    if (!state.active) {
      clearInterval(pollTimer);
      pollTimer = null;
      if (!hadActiveJob) return;
      const code = data.exit_code;
      if (data.job_status === "paused") {
        pulseEl.dataset.state = "stopped";
        setStatus(data.pace_note || "Pace limit reached. Start again later — already-messaged people are skipped.", "stopped");
      } else if (code === 0 || data.job_status === "done") {
        pulseEl.dataset.state = "done";
        setStatus("Sending finished successfully", "ok");
      } else if (stopRequested || data.job_status === "cancelled" || code === 1 || code === 130) {
        pulseEl.dataset.state = "stopped";
        setStatus("Worker stopped — Chrome was force-closed", "stopped");
      } else if (data.job_status === "failed") {
        pulseEl.dataset.state = "error";
        setStatus("Worker failed — check the log", "error");
      }
      stopRequested = false;
      hadActiveJob = false;
    }
  } catch (err) {
    if (err && err.message === "auth") return;
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
    const creds = payloadFromForm();
    if (!creds.linkedin_username || (!creds.linkedin_password && !linkedinConnected)) {
      setStatus("Add your LinkedIn username and password before launching.", "error");
      pulseEl.dataset.state = "error";
      return;
    }
    setStatus("Starting…", "running");
    logEl.replaceChildren();
    logCursor = 0;
    stopRequested = false;
    hadActiveJob = true;
    pulseEl.dataset.state = "running";
    setRunningUi(true);

    const res = await api("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadFromForm()),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setStatus(data.error || "Could not start sending", "error");
      pulseEl.dataset.state = "error";
      setRunningUi(false);
      hadActiveJob = false;
      return;
    }
    if (data.config) applyConfig(data.config);
    jobPeopleCount = data.count || 0;
    currentJobId = data.job_id || null;
    if (data.job_status === "queued" || res.status === 202) {
      const n = data.queue_position || 1;
      setStatus("Waiting — another campaign is using Chrome (position " + n + ")", "running");
    } else {
      setStatus(`Running for ${data.count} people — watch Chrome + the live log`, "running");
    }
    startPolling();
  } catch (err) {
    if (err && err.message === "auth") return;
    setStatus("Cannot reach Sendline server. Run: py app.py", "error");
    pulseEl.dataset.state = "error";
    setRunningUi(false);
    hadActiveJob = false;
  }
}

async function stop() {
  try {
    stopBtn.disabled = true;
    const res = await api("/api/stop", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      stopBtn.disabled = false;
      setStatus(data.error || "Stop failed", "error");
      return;
    }
    stopRequested = true;
    if (data.job_status === "cancelled") {
      setStatus("Removed from the Chrome queue", "stopped");
      setRunningUi(false);
      hadActiveJob = false;
      pulseEl.dataset.state = "stopped";
      return;
    }
    setStatus("Stopping — closing Chrome…", "running");
  } catch (_) {
    stopBtn.disabled = false;
    setStatus("Cannot reach Sendline server", "error");
  }
}

namesEl.addEventListener("input", updateCount);
document.querySelectorAll('input[name="pace"]').forEach((input) => {
  input.addEventListener("change", updatePaceSummary);
});
saveBtn.addEventListener("click", () => saveConfig(false));
runBtn.addEventListener("click", launch);
stopBtn.addEventListener("click", stop);

togglePasswordBtn.addEventListener("click", () => {
  const show = linkedinPasswordEl.type === "password";
  linkedinPasswordEl.type = show ? "text" : "password";
  togglePasswordBtn.setAttribute("aria-pressed", show ? "true" : "false");
  togglePasswordBtn.setAttribute("aria-label", show ? "Hide password" : "Show password");
  const label = document.getElementById("togglePasswordText");
  if (label) label.textContent = show ? "Hide" : "Show";
});

uploadNamesBtn.addEventListener("click", () => namesFileEl.click());
namesFileEl.addEventListener("change", () => {
  const file = namesFileEl.files && namesFileEl.files[0];
  if (file) importNamesFile(file);
});

namesField.addEventListener("dragenter", (event) => {
  event.preventDefault();
  namesField.classList.add("drop-ready");
});
namesField.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
});
namesField.addEventListener("dragleave", (event) => {
  if (namesField.contains(event.relatedTarget)) return;
  namesField.classList.remove("drop-ready");
});
namesField.addEventListener("drop", (event) => {
  event.preventDefault();
  namesField.classList.remove("drop-ready");
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (!file) return;
  if (!isNamesFile(file)) {
    setStatus("Please drop the filled template (.csv) or a .txt list.", "error");
    return;
  }
  importNamesFile(file);
});

uploadAttachmentBtn.addEventListener("click", () => attachmentFileEl.click());
attachmentFileEl.addEventListener("change", () => {
  const file = attachmentFileEl.files && attachmentFileEl.files[0];
  if (file) uploadAttachmentFile(file);
});
clearAttachmentBtn.addEventListener("click", () => clearAttachment());

attachmentField.addEventListener("dragenter", (event) => {
  event.preventDefault();
  attachmentField.classList.add("drop-ready");
});
attachmentField.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
});
attachmentField.addEventListener("dragleave", (event) => {
  if (attachmentField.contains(event.relatedTarget)) return;
  attachmentField.classList.remove("drop-ready");
});
attachmentField.addEventListener("drop", (event) => {
  event.preventDefault();
  attachmentField.classList.remove("drop-ready");
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (!file) return;
  uploadAttachmentFile(file);
});

logoutBtn.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" }).catch(() => {});
  window.location.href = "/login";
});

async function boot() {
  try {
    const meRes = await api("/api/me");
    const meData = await meRes.json();
    if (meData.user) {
      userEmailEl.textContent = meData.user.email || "";
      adminLinkEl.hidden = meData.user.role !== "admin";
    }
    await loadConfig();
    const statusRes = await api("/api/status");
    const statusData = await statusRes.json();
    const state = applyJobState(statusData);
    if (state.active) {
      hadActiveJob = true;
      startPolling();
    }
  } catch (err) {
    if (err && err.message === "auth") return;
    setStatus("Could not load Sendline — is py app.py running?", "error");
  }
}

boot();
