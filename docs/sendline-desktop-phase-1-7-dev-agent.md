# Phase 1.7 — Dev agent script

> **Status:** `scripts/agent_dev.py`  
> **Related:** [sendline-desktop-transition-plan.md](./sendline-desktop-transition-plan.md) Phase 1.7

Proves the Phase 1 agent API end-to-end **before** building the Electron shell. Same contract as Phase 2 `agent-runner.js`: poll work, local job dirs under `%LOCALAPPDATA%\Sendline\`, spawn `worker.py`, stream logs, honor stop, post complete.

---

## Prerequisites

1. Flask running: `py app.py` → `http://127.0.0.1:5055`
2. Operator account with campaign + LinkedIn credentials saved in the UI
3. User set to **`local`** — see [sendline-desktop-phase-1-1-data-model.md](./sendline-desktop-phase-1-1-data-model.md)
4. Google Chrome installed on the PC
5. Python venv with `requirements.txt` (selenium, etc.)

---

## Register token + run agent

From `automate/`:

```powershell
py scripts/agent_dev.py register --email you@example.com --password "YOUR_PASSWORD" --label "Dev PC"
$env:SENDLINE_AGENT_TOKEN = "<paste token>"
py scripts/agent_dev.py run
```

Optional env:

| Variable | Purpose |
|----------|---------|
| `SENDLINE_API_URL` | API base (default `http://127.0.0.1:5055`) |
| `SENDLINE_AGENT_TOKEN` | Bearer token for `run` |
| `SENDLINE_AGENT_DATA` | Override data root (default `%LOCALAPPDATA%\Sendline`) |

Set execution mode (SQLite):

```sql
UPDATE users SET execution_mode = 'local' WHERE id = 1;
```

---

## Manual test checklist (1.7.3)

Use the hosted UI while `agent_dev.py run` is active in a terminal.

| # | Test | Expected |
|---|------|----------|
| 1 | **Launch** | UI Launch queues job; agent claims it; log shows `[sendline] desktop agent claimed job` and worker lines in UI log panel |
| 2 | **Log stream** | Worker stdout appears in UI via `/api/agent/logs` (same as VPS runs) |
| 3 | **Stop** | UI Stop → agent writes local `jobs/<id>/stop`; Chrome closes; job `cancelled` or exit `130`/`1` |
| 4 | **Attachment** | Campaign with attachment → file downloaded to `%LOCALAPPDATA%\Sendline\attachments\`; send flow attaches file |
| 5 | **Proxy line** | If residential proxy configured: log contains `[sendline] residential exit <label>`; else “no residential proxy…” |
| 6 | **Exit codes** | Normal finish → `done` (0); limit/pause path → `paused` (75) if worker returns 75; hard failure → `failed` |

**Offline agent:** With user `local` and **no** `agent_dev run`, Launch should fail with a clear message (503) — see Phase 1.6.

**VPS check:** No `worker.py` / automation Chrome on the server for `local` users (`jobs._spawn` no-op).

---

## Local layout

```text
%LOCALAPPDATA%\Sendline\
  chrome\                 # --user-data-dir for worker
  send_ledger.json        # local dedupe ledger (not synced to server)
  attachments\            # downloaded from GET /api/agent/jobs/<id>/attachment
  jobs\<job_id>\
    run.json
    secrets.json          # deleted by worker after read
    stop                    # written when UI requests stop
```

---

## Next step

Phase 2 — port this loop into `my-electron-app/electron/agent-runner.js` with tray + BrowserWindow loading the hosted UI.
