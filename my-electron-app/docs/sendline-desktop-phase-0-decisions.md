# Sendline Desktop — Phase 0 decisions

> **Mirror:** Canonical copy at `automate/docs/sendline-desktop-phase-0-decisions.md`. Edit there first, then sync.

> **Status:** Locked for implementation (Phase 1+).  
> **Date:** 2026-11-02

Phase 0 records product and technical choices **before** backend or Electron coding. Each item maps to a checkbox in [sendline-desktop-transition-plan.md](./sendline-desktop-transition-plan.md).

---

## 0.1 Product model — **Confirmed**

**Decision:** Hosted Sendline API (Flask on VPS or similar) + **local** automation worker on the operator’s PC. Production does **not** bundle Flask inside Electron.

| Component | Where it runs |
|-----------|----------------|
| Auth, users, campaigns, job queue, vault, SQLite | Hosted `automate/` API |
| `worker.py`, Selenium, automation Chrome | Operator Windows PC (Electron main process spawns worker) |
| Sendline UI (`static/`) | Loaded from hosted origin in Electron (`loadURL`) |

**Rejected for production:** “All-in-one” Electron app that embeds Flask/SQLite locally (acceptable only for isolated dev experiments).

---

## 0.2 UI strategy — **Confirmed**

**Decision:** Electron `BrowserWindow` uses `loadURL(<SENDLINE_API_URL>)` against the **same** hosted origin as the browser UI today.

- Session cookies live in Electron’s partition for that origin (login once in the desktop app).
- No fork of `static/` for desktop unless a later phase needs desktop-only UI (tray/settings can stay local HTML in Electron).
- Existing `auth.origin_allowed()` continues to guard mutating API calls from the loaded origin; agent routes use Bearer auth only (Phase 1).

---

## 0.3 Agent authentication — **Confirmed**

**Decision:** Per-device **agent tokens** (`Authorization: Bearer <token>`), issued from the hosted UI after session login (Phase 1 `POST /api/agent/register`).

- Electron stores the token locally (Phase 2: file/env; Phase 4: `safeStorage` / keytar).
- **Do not** store Sendline account passwords in the desktop app.
- Tokens are hashed server-side; plaintext shown once at registration.

---

## 0.4 Execution mode — **Confirmed**

**Decision:** Per-user `execution_mode`: `server` | `local`, default **`server`**.

| Mode | Worker spawn |
|------|----------------|
| `server` | Today’s behavior: VPS runs `worker.py` via `jobs._spawn` |
| `local` | VPS queues job only; desktop agent polls work and spawns worker locally |

Rollout: existing users stay `server` until explicitly set to `local` (see migration M.1–M.3 in transition plan).

---

## 0.5 Chrome — **Confirmed**

**Decision:** Operator installs **Google Chrome** on the PC. Electron installer does **not** bundle Chromium for LinkedIn automation.

- Worker continues to launch Chrome with a dedicated `--user-data-dir` under `%LOCALAPPDATA%\Sendline\` (or Electron `userData` path per Phase 2).
- Document Chrome as a prerequisite in client handover (Phase 4).

---

## 0.6 API URL defaults — **Confirmed**

| Environment | Base URL | Notes |
|-------------|----------|--------|
| **Local dev** | `http://127.0.0.1:5055` | Matches `automate/app.py` default bind |
| **Production** | *No baked-in default* | Set **`SENDLINE_API_URL`** (or equivalent) per deployment at install/build time |

**Electron config contract (Phase 2):**

- `SENDLINE_API_URL` — full origin for UI `loadURL` and agent HTTP client (no trailing path required; normalize in code).
- Example production value: `https://your-sendline-host` (operator-specific).

**Hosted API (optional future env):** `SENDLINE_PUBLIC_URL` for links in emails/docs; not required for Phase 1.

---

## Local data paths (reference for later phases)

Aligned with transition plan target architecture:

- Job dirs, Chrome profile, attachments: under Electron `app.getPath('userData')` on Windows (typically `%APPDATA%\Sendline Desktop\` or product name at ship time).
- Plan diagram uses `%LOCALAPPDATA%\Sendline\`; final directory name follows `electron-builder` `productName` — pick one name in Phase 2 scaffold and document it.

---

## Sign-off

| ID | Decision | Status |
|----|----------|--------|
| 0.1 | Hosted API + local worker | Confirmed |
| 0.2 | Hosted `loadURL` UI | Confirmed |
| 0.3 | Bearer agent token | Confirmed |
| 0.4 | `server` \| `local`, default `server` | Confirmed |
| 0.5 | User-installed Chrome | Confirmed |
| 0.6 | Dev `127.0.0.1:5055`; prod via `SENDLINE_API_URL` | Confirmed |

**Next step:** Phase 1 — backend local execution + agent API (`automate/`).
