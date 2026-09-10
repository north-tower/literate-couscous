const namesEl = document.getElementById("names");
const namesField = document.getElementById("namesField");
const namesFileEl = document.getElementById("namesFile");
const uploadNamesBtn = document.getElementById("uploadNamesBtn");
const uploadProgress = document.getElementById("uploadProgress");
const uploadProgressFill = document.getElementById("uploadProgressFill");
const uploadProgressLabel = document.getElementById("uploadProgressLabel");
const messageEl = document.getElementById("message");
const attachmentEl = document.getElementById("attachment");
const nameCountEl = document.getElementById("nameCount");
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const pulseEl = document.getElementById("pulse");
const saveBtn = document.getElementById("saveBtn");
const runBtn = document.getElementById("runBtn");
const stopBtn = document.getElementById("stopBtn");

const MAX_NAMES_FILE_BYTES = 8 * 1024 * 1024;
const TEMPLATE_NAME_HEADER = "name";
const TEMPLATE_ERROR =
  "This file doesn’t match the template. Download the template, keep the name header, and put one full name per row.";

let logCursor = 0;
let pollTimer = null;
let namesImporting = false;

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
      if (!quiet) setStatus(data.error || "Save failed", "error");
      return null;
    }
    if (!quiet) setStatus(`Saved ${data.count} people`, "ok");
    return data;
  } catch (err) {
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

loadConfig().catch(() => setStatus("Could not load config — is py app.py running?", "error"));
