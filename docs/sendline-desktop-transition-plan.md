# Sendline Desktop — Electron transition plan

> **Mirror:** Also copied to `my-electron-app/docs/sendline-desktop-transition-plan.md`. Edit this file first, then sync the copy.

**Scope:** Move LinkedIn automation to the operator’s Windows PC via an Electron app, while keeping auth, campaigns, and job coordination on the hosted Sendline API (`automate/`). Mac support comes after Windows ships.

**Repos:**

| Folder | Role |
|--------|------|
| `automate/` | Flask API, `static/` UI, `worker.py`, agent API, PyInstaller worker build |
| `my-electron-app/` | Sendline Desktop shell (rename to `sendline-desktop` when ready) |

**Current state:**

- `automate`: Launch → `jobs._spawn()` runs `worker.py` on the **same machine as Flask** (today often the VPS).
- `my-electron-app`: Electron 45 shell — `electron/main.js`, config, preload (Phase 2.1); main window (Phase 2.2); agent loop + tray (Phase 2.3–2.5); bundled worker path (Phase 3.2).

---

## Target architecture

```text
┌─────────────────────────────────────────────────────────┐
│  Sendline Desktop (Electron) — operator Windows PC       │
│  ┌──────────────────┐   ┌─────────────────────────────┐ │
│  │ BrowserWindow    │   │ Main process                 │ │
│  │ → hosted Sendline│   │ Agent loop, spawn worker,    │ │
│  │   UI (HTTPS)     │   │ log upload, stop, tray       │ │
│  └────────┬─────────┘   └──────────────┬──────────────┘ │
└───────────┼────────────────────────────┼──────────────────┘
            │ HTTPS                      │ subprocess
            ▼                            ▼
     Hosted Flask API              worker + Chrome (local)
     SQLite, vault, queue          %LOCALAPPDATA%\Sendline\
```

**Invariant:** `worker.py` and automation Chrome always run on the **same PC** as the Electron main process.

---

## Phase 0 — Decisions (before coding)

> **Recorded:** [sendline-desktop-phase-0-decisions.md](./sendline-desktop-phase-0-decisions.md) (2026-11-02)

- [x] **0.1** Confirm product model: hosted API + local worker (not “Electron bundles Flask” for production).
- [x] **0.2** Confirm UI strategy: Electron `loadURL(https://your-sendline-host/)` (same cookies as browser; minimal `static/` fork).
- [x] **0.3** Confirm agent auth: device **agent token** (Bearer), not storing Sendline password in Electron.
- [x] **0.4** Confirm execution mode: per-user `server` | `local` (default `server` until desktop rollout).
- [x] **0.5** Confirm Chrome: user installs Google Chrome; not bundled in Electron.
- [x] **0.6** Pick dev API URL (e.g. `http://127.0.0.1:5055`) and production URL for config defaults — dev fixed; prod via `SENDLINE_API_URL` (no baked-in default).

---

## Phase 1 — Backend: local execution + agent API (`automate/`)

**Goal:** Server queues jobs for a desktop agent; no `worker.py` on VPS for `local` users.

**Exit criteria:** Manual test agent (script or curl) completes a job; Chrome does not start on server; `/api/logs` still updates.

### 1.1 Data model

- [x] **1.1.1** Add migration: `users.execution_mode TEXT NOT NULL DEFAULT 'server'` (`server` | `local`).
- [x] **1.1.2** Add table `agent_tokens` (id, user_id, label, token_hash, created_at, last_seen_at, revoked_at).
- [x] **1.1.3** Add helper `db.get_execution_mode(user_id)` and admin override if needed.
- [x] **1.1.4** Document how to set a user to `local` (SQL or admin UI later). → [sendline-desktop-phase-1-1-data-model.md](./sendline-desktop-phase-1-1-data-model.md)

### 1.2 Agent authentication

- [x] **1.2.1** Implement `auth.agent_required` decorator (Bearer token → user_id).
- [x] **1.2.2** `POST /api/agent/register` (session login + CSRF): body `{ label }` → returns `{ token }` once (store hash only).
- [x] **1.2.3** `POST /api/agent/revoke` — revoke token by id (session auth).
- [x] **1.2.4** `GET /api/agent/tokens` — list tokens for current user (masked, no secret).
- [x] **1.2.5** Rate-limit agent register (reuse login rate limit patterns). → [sendline-desktop-phase-1-2-agent-auth.md](./sendline-desktop-phase-1-2-agent-auth.md)

### 1.3 Job payload for local runs

- [x] **1.3.1** Extract shared logic from `jobs._write_job_payload` → `jobs.build_job_payload(user_id, job_id)` (run + secrets + proxy).
- [x] **1.3.2** For `local` users on enqueue: write `run.json` on server **optional** (or skip); keep job row in DB.
- [x] **1.3.3** Include in agent work response: `people_names`, `message_template`, attachment metadata.
- [x] **1.3.4** Attachment delivery: signed `GET /api/agent/jobs/<id>/attachment` or time-limited token URL.
- [x] **1.3.5** LinkedIn credentials: decrypt in agent work response only; never log username/password.
- [x] **1.3.6** Proxy: call `egress.assign_proxy(user_id)` when building work bundle (same as today).
- [x] **1.3.7** Ledger: agent uses local path only; document server sync as future work. → [sendline-desktop-phase-1-3-job-payload.md](./sendline-desktop-phase-1-3-job-payload.md)

### 1.4 Agent work lifecycle API

- [x] **1.4.1** `GET /api/agent/work` — if user has `queued` job and no active claim, return work bundle + mark `running` + `started_at`.
- [x] **1.4.2** Idempotency: if same agent polls while already `running` for that user, return in-progress job + cursor.
- [x] **1.4.3** `POST /api/agent/logs` — body `{ job_id, lines: string[] }` → `append_log`.
- [x] **1.4.4** `POST /api/agent/heartbeat` — body `{ worker_version?, agent_version? }` → update `last_seen_at`.
- [x] **1.4.5** `POST /api/agent/complete` — body `{ job_id, exit_code }` → map exit codes like `jobs._watch` (0→done, 75→paused, 130→cancelled, etc.).
- [x] **1.4.6** `GET /api/agent/stop/<job_id>` or flag in work poll — agent sees stop requested. → [sendline-desktop-phase-1-4-agent-lifecycle.md](./sendline-desktop-phase-1-4-agent-lifecycle.md)

### 1.5 Change server worker dispatch

- [x] **1.5.1** In `jobs.start_next` / `_spawn`: skip `Popen(worker.py)` when job user `execution_mode == 'local'`.
- [x] **1.5.2** Adjust global queue: for `local` users, “busy” = open job in `running`/`stopping` for that user (not VPS Chrome FIFO).
- [x] **1.5.3** Keep `server` mode behavior unchanged (`_spawn` on VPS).
- [x] **1.5.4** Update `request_stop`: set DB `stopping`; agent writes local `stop` file (document contract). → [sendline-desktop-phase-1-5-dispatch.md](./sendline-desktop-phase-1-5-dispatch.md)

### 1.6 API / UI feedback

- [x] **1.6.1** `GET /api/status` or `/api/me`: include `execution_mode`, `agent_online` (heartbeat within 60s).
- [x] **1.6.2** `POST /api/run` for `local` + offline agent: return 503 or 202 with clear error message.
- [x] **1.6.3** Optional banner in `static/app.js`: “Open Sendline Desktop to run campaigns.” → [sendline-desktop-phase-1-6-api-ui-feedback.md](./sendline-desktop-phase-1-6-api-ui-feedback.md)

### 1.7 Dev agent script (prove backend before Electron)

- [x] **1.7.1** Add `scripts/agent_dev.py`: register token, poll work, write local dirs, spawn `worker.py`, post logs/complete.
- [x] **1.7.2** README section: run agent_dev + set user to `local`. → [sendline-desktop-phase-1-7-dev-agent.md](./sendline-desktop-phase-1-7-dev-agent.md), [README.md](../README.md)
- [x] **1.7.3** Manual test checklist: Launch, log stream, Stop, attachment, proxy line in log, exit codes. → [sendline-desktop-phase-1-7-dev-agent.md](./sendline-desktop-phase-1-7-dev-agent.md)

---

## Phase 2 — Electron shell (`my-electron-app/`)

**Goal:** `pnpm start` opens hosted UI; main process runs agent loop; dev spawn of `worker.py`.

**Exit criteria:** End-to-end on dev machine: Electron + hosted UI + Chrome on PC + live log.

### 2.1 Project scaffold

- [x] **2.1.1** Add `electron/main.js` (or `main.mjs`), set `package.json` `"main"`.
- [x] **2.1.2** Add scripts: `"start": "electron ."`, `"dev": "electron . --enable-logging"`.
- [x] **2.1.3** Add `electron/preload.js` with `contextIsolation: true`, `nodeIntegration: false`.
- [x] **2.1.4** Add `electron/config.js` — `SENDLINE_API_URL`, paths via `app.getPath('userData')`.
- [x] **2.1.5** Add `.env.example` for API URL (load with dotenv in main only).

### 2.2 Main window

- [x] **2.2.1** Create `BrowserWindow` (min size, title “Sendline”).
- [x] **2.2.2** `loadURL(SENDLINE_API_URL)` — login page first.
- [x] **2.2.3** Handle external links (open in system browser).
- [x] **2.2.4** Optional: single-instance lock (second launch focuses existing window).

### 2.3 Agent runner (main process)

- [x] **2.3.1** Add `electron/agent-runner.js` — state machine: idle | running | stopping.
- [x] **2.3.2** Load agent token from config file or env (Phase 4 moves to secure storage).
- [x] **2.3.3** Poll `GET /api/agent/work` every 2–3s when idle.
- [x] **2.3.4** On job: create `userData/jobs/<job_id>/`, write `run.json`, `secrets.json`.
- [x] **2.3.5** Download attachment to `userData/attachments/`; fix path in `run.json`.
- [x] **2.3.6** Set `userData/chrome` as `--user-data-dir`.
- [x] **2.3.7** Spawn worker: dev = `py -u ../automate/worker.py` with args (configurable path).
- [x] **2.3.8** Pipe stdout/stderr → batch POST `/api/agent/logs`.
- [x] **2.3.9** Poll stop flag; write `job_dir/stop` when server says stopping.
- [x] **2.3.10** On process exit → POST `/api/agent/complete`.
- [x] **2.3.11** Heartbeat every 30s while app open.
- [x] **2.3.12** Handle worker crash / non-zero exit without hanging agent loop.

### 2.4 Tray + status

- [x] **2.4.1** System tray icon (connected / running / error).
- [x] **2.4.2** Tray menu: Open Sendline, Quit (disabled or confirm while running).
- [x] **2.4.3** Expose `agentStatus` to renderer via preload (`connected`, `running`, `job_id`).

### 2.5 First-run / settings (minimal)

- [x] **2.5.1** If no token: show small local settings window OR prompt in tray menu (API URL + paste token).
- [x] **2.5.2** Link to hosted UI: “Generate desktop token” (after Phase 1 register UI or doc).

### 2.6 Integration test (manual)

- [ ] **2.6.1** User `local`, agent token in Electron, Launch from loaded UI.
- [ ] **2.6.2** Chrome visible; Stop works; queue message when another user runs (if applicable).

---

## Phase 3 — Bundle worker for Windows (no Python on client)

**Goal:** Ship `sendline-worker.exe` inside the desktop installer.

**Exit criteria:** Clean Windows VM: Chrome + Sendline Desktop only — full send flow.

### 3.1 PyInstaller spec (`automate/`)

- [x] **3.1.1** Add `sendline-worker.spec` targeting `worker.py` entrypoint.
- [x] **3.1.2** Include hidden imports: `selenium`, `pyperclip`, `egress`, `pace`, `cryptography` if needed.
- [x] **3.1.3** One-file vs one-folder: prefer **one-folder** in `dist/sendline-worker/` for faster startup (document choice).
- [x] **3.1.4** Add `scripts/build-worker.ps1` for CI/local build.
- [x] **3.1.5** Embed `WORKER_VERSION` in build (env or git sha).

### 3.2 Electron consumes bundled worker

- [x] **3.2.1** Copy worker artifact to `my-electron-app/resources/sendline-worker/` in build script.
- [x] **3.2.2** Resolve path: dev → `py worker.py`; prod → `process.resourcesPath/.../sendline-worker.exe`.
- [x] **3.2.3** Pass `--debug-port 9222` (document single-user per machine).
- [x] **3.2.4** Heartbeat sends `worker_version` to server.

### 3.3 Verify on clean machine

- [ ] **3.3.1** VM without Python: install Chrome + desktop build.
- [ ] **3.3.2** First LinkedIn login in automation window; second run reuses session.
- [ ] **3.3.3** Attachment + residential proxy smoke test if used in prod.

---

## Phase 4 — Installer, registration UX, polish

**Goal:** Client-ready Windows `.exe` installer and clear onboarding.

**Exit criteria:** Installer you can send to clients; SmartScreen acceptable if signed.

### 4.1 electron-builder

- [x] **4.1.1** Add `electron-builder` devDependency and `build` config in `package.json`.
- [x] **4.1.2** App ID, product name “Sendline Desktop”, NSIS target.
- [x] **4.1.3** `extraResources` for worker bundle.
- [x] **4.1.4** Icons (`.ico` multi-size).
- [x] **4.1.5** Script: `pnpm run dist:win`. → `my-electron-app/docs/sendline-desktop-phase-4-1-electron-builder.md`

### 4.2 Code signing (Windows)

- [ ] **4.2.1** Obtain code signing cert (EV or standard). → `my-electron-app/docs/sendline-desktop-phase-4-2-code-signing.md` §4.2.1 (operator checklist)
- [x] **4.2.2** Configure `signtool` / electron-builder `certificateSubjectName`. → `my-electron-app/docs/sendline-desktop-phase-4-2-code-signing.md` §4.2.2
- [ ] **4.2.3** Test install on machine without dev tools. → same doc §4.2.3 (after signed build)

### 4.3 Token registration UX

- [x] **4.3.1** Hosted UI page or modal: “Link this computer” → calls `POST /api/agent/register` → show token once. → `my-electron-app/docs/sendline-desktop-phase-4-3-token-ux.md`
- [x] **4.3.2** Electron settings: paste token → store with `safeStorage.encryptString` (or `keytar`).
- [x] **4.3.3** Revoke token from UI lists in `/api/agent/tokens`.

### 4.4 Auto-start & updates

- [ ] **4.4.1** Optional: “Start Sendline when Windows starts” (registry / login item).
- [ ] **4.4.2** Optional v2: `electron-updater` + release channel on GitHub/S3.

### 4.5 Documentation

- [ ] **4.5.1** Update `CLIENT_PITCH.md` / handover: desktop install steps.
- [ ] **4.5.2** Short WhatsApp-style client blurb in docs.
- [ ] **4.5.3** Admin doc: set user to `local`, monitor agent heartbeat.

---

## Phase 5 — macOS (after Windows stable)

**Goal:** `.dmg` for MacBooks with ported worker.

**Exit criteria:** Same flows on macOS 13+ with Chrome installed.

### 5.1 Port `worker.py` OS abstraction

- [x] **5.1.1** Add `platform_chrome.py`: `find_chrome_exe()`, `kill_pid_tree()`, `pids_on_port()`.
- [x] **5.1.2** Windows: keep current `taskkill` / `netstat` / Program Files paths.
- [x] **5.1.3** macOS: Chrome.app path, `kill -9`, `lsof -i :9222`.
- [x] **5.1.4** Replace direct calls in `worker.py` and any agent kill logic in `jobs.py` used locally.
- [ ] **5.1.5** Test pyperclip on macOS.

### 5.2 Build worker on Mac

- [x] **5.2.1** PyInstaller build on macOS CI or local (`scripts/build-worker.sh`).
- [x] **5.2.2** Bundle in Electron `extraResources` per arch (`arm64`, `x64`).

### 5.3 electron-builder macOS

- [x] **5.3.1** Target `dmg`, hardened runtime.
- [ ] **5.3.2** Apple Developer ID signing + notarization (`notarize` in builder).
- [ ] **5.3.3** QA: Gatekeeper install, LinkedIn login, stop, attachments.

---

## Migration & rollout

- [ ] **M.1** Existing VPS-only users: stay `execution_mode=server` until tested.
- [ ] **M.2** Pilot: one operator → `local` + desktop build.
- [ ] **M.3** Flip production operators after pilot sign-off.
- [ ] **M.4** Deprecate server-side Chrome for `local` users (monitor VPS for stray Chrome).

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Secrets in agent payload | HTTPS only; short-lived work token optional v2; delete local `secrets.json` |
| CSRF on agent routes | Bearer auth only; no cookie on agent endpoints |
| Global VPS queue breaks | Per-user busy for `local` in Phase 1.5 |
| Large installer | One-folder worker; document Chrome separate |
| Mac promised too early | Ship Windows first; Phase 5 gate |

---

## Suggested sprint order

| Sprint | Focus |
|--------|--------|
| 1 | Phase 1.1–1.5 + `agent_dev.py` (1.7) |
| 2 | Phase 2 (Electron shell + agent runner) |
| 3 | Phase 3 (PyInstaller + bundled worker) |
| 4 | Phase 4 (installer + token UX) |
| 5 | Phase 5 (macOS) |

---

## Related files (reference)

- Worker spawn today: `automate/jobs.py` → `_spawn`
- Job payload: `automate/jobs.py` → `_write_job_payload`
- Worker CLI: `automate/worker.py` → `--job-dir`, `--user-data-dir`, `--debug-port`
- UI launch: `automate/static/app.js` → `launch()` → `POST /api/run`
- Origin guard: `automate/auth.py` → `origin_allowed()` (UI stays on hosted origin)

---

*Last updated: plan authored for Windows-first Sendline Desktop transition.*
