# Phase 2–3 — Electron agent + bundled worker

## Phase 2 (Electron agent)

### Setup

1. Run Flask: `py app.py` (port 5055).
2. Set user `execution_mode=local` and register agent token (`scripts/agent_dev.py register` or API).
3. Copy `my-electron-app/.env.example` → `.env` with `SENDLINE_API_URL` and optionally `SENDLINE_AGENT_TOKEN`.
4. From `my-electron-app`: `pnpm dev`.

Or paste token in **Settings** (tray → Settings…) on first launch.

Dev worker spawn defaults:

- Python: `py` (override `SENDLINE_PYTHON`)
- Script: `../automate/worker.py` (override `SENDLINE_WORKER_PY`)
- Chrome profile: Electron `userData/chrome`
- Debug port: `9222` (`SENDLINE_DEBUG_PORT`)

### Manual checklist (2.6)

- [ ] Launch campaign from hosted UI in Electron window
- [ ] Chrome opens locally; logs stream in UI
- [ ] Stop from UI writes `jobs/<id>/stop`
- [ ] Tray shows running / idle; Quit disabled while job runs
- [ ] `window.sendlineDesktop.getAgentStatus()` in devtools

## Phase 3 (bundled worker)

### Build worker

```powershell
cd C:\Users\mhki\automate
.\scripts\build-worker.ps1
cd C:\Users\mhki\my-electron-app
.\scripts\copy-worker.ps1
```

Layout: **one-folder** `dist/sendline-worker/` (faster cold start than one-file).

`WORKER_VERSION` = git short SHA, written to `VERSION`; heartbeat sends `worker_version`.

### Packaged path

When `app.isPackaged`, Electron runs:

`process.resourcesPath/sendline-worker/sendline-worker.exe`

(Phase 4 `electron-builder` `extraResources` will copy the same folder.)

### Clean VM checklist (3.3)

- [ ] No Python installed; Google Chrome installed
- [ ] Install desktop build with bundled worker
- [ ] First LinkedIn login; second run reuses `userData/chrome`
- [ ] Attachment + proxy smoke test
