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
const FULL_NAME_HEADERS = new Set([
  "name",
  "full_name",
  "fullname",
  "person",
  "person_name",
  "people",
  "contact",
  "contact_name",
  "recipient",
  "candidate",
]);
const FIRST_NAME_HEADERS = new Set([
  "first",
  "first_name",
  "firstname",
  "given",
  "given_name",
  "forename",
]);
const LAST_NAME_HEADERS = new Set([
  "last",
  "last_name",
  "lastname",
  "surname",
  "family",
  "family_name",
]);

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

function nameFromCells(cells, getter) {
  if (getter) return getter(cells);
  if (!cells.length) return "";
  if (cells.length === 1) return cells[0];
  const firstNonEmail = cells.find((c) => c && !looksLikeEmail(c));
  if (firstNonEmail && firstNonEmail.includes(" ")) return firstNonEmail;
  if (cells[0] && !looksLikeEmail(cells[0]) && cells[1] && !looksLikeEmail(cells[1])) {
    return (cells[0] + " " + cells[1]).trim();
  }
  return firstNonEmail || cells[0] || "";
}

function makeCsvNameGetter(headerCells) {
  const keys = headerCells.map(headerKey);
  const fullIdx = keys.findIndex((k) => FULL_NAME_HEADERS.has(k));
  if (fullIdx >= 0) return (cells) => cells[fullIdx] || "";
  const firstIdx = keys.findIndex((k) => FIRST_NAME_HEADERS.has(k));
  const lastIdx = keys.findIndex((k) => LAST_NAME_HEADERS.has(k));
  if (firstIdx >= 0 && lastIdx >= 0) {
    return (cells) => [cells[firstIdx], cells[lastIdx]].filter(Boolean).join(" ").trim();
  }
  if (firstIdx >= 0) return (cells) => cells[firstIdx] || "";
  return null;
}

function looksLikeHeaderRow(cells) {
  const keys = cells.map(headerKey).filter(Boolean);
  if (!keys.length) return false;
  return keys.some(
    (k) =>
      FULL_NAME_HEADERS.has(k) ||
      FIRST_NAME_HEADERS.has(k) ||
      LAST_NAME_HEADERS.has(k) ||
      k === "email" ||
      k === "company" ||
      k === "title"
  );
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

async function extractNamesFromText(text, kind, onProgress) {
  const cleaned = String(text || "").replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = cleaned.split("\n");
  const names = [];
  const seen = new Set();
  let delimiter = ",";
  let getter = null;
  let start = 0;

  if (kind === "csv") {
    const firstData = lines.find((line) => line.trim());
    delimiter = detectDelimiter(firstData || "");
    const headerCells = parseDelimitedLine(firstData || "", delimiter);
    if (looksLikeHeaderRow(headerCells)) {
      getter = makeCsvNameGetter(headerCells);
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
        name = nameFromCells(parseDelimitedLine(raw, delimiter), getter);
      } else {
        name = raw.trim();
        if (j === start && looksLikeHeaderRow([name])) continue;
      }
      name = name.replace(/\s+/g, " ").trim();
      if (!name || looksLikeEmail(name)) continue;
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
      setStatus("No names found in that file. Use one name per line, or a Name column in the CSV.", "error");
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
    setStatus("Could not read that file. Try a .txt or .csv export.", "error");
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
    setStatus("Please drop a .txt or .csv file.", "error");
    return;
  }
  importNamesFile(file);
});

loadConfig().catch(() => setStatus("Could not load config — is py app.py running?", "error"));
