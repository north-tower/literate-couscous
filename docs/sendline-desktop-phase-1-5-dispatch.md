# Phase 1.5 — Server worker dispatch & queue

> **Status:** Implemented in `jobs.py`, `db.py`.  
> **Related:** [sendline-desktop-transition-plan.md](./sendline-desktop-transition-plan.md) Phase 1.5

---

## Summary

| Mode | Who runs `worker.py` | VPS Chrome FIFO |
|------|----------------------|-----------------|
| `server` (default) | `jobs._spawn` on VPS | One global slot (`running_server_job` / `next_queued_server_job`) |
| `local` | Desktop agent (Phase 1.4 `GET /api/agent/work`) | Not on VPS; user “busy” = own job in `queued` / `running` / `stopping` |

---

## 1.5.1 — Skip VPS spawn for local users

- `jobs._spawn`: returns immediately when `get_execution_mode(user_id) == 'local'`.
- `jobs.start_next`: only considers **server** users via `db.next_queued_server_job()`; never `Popen(worker.py)` for local rows.

Local jobs stay `queued` until the agent claims them in `agent_poll_work` (sets `running`).

---

## 1.5.2 — Queue semantics

**VPS (server users only)**

- Busy if any job with `status IN ('running', 'stopping')` for a `execution_mode=server` user → `db.running_server_job()`.
- Next worker: oldest `queued` job for a server user → `db.next_queued_server_job()`.
- A local user running on their PC does **not** block server users on the VPS.

**Local users**

- At most one open job per user (`db.open_job_for_user`: `queued` / `running` / `stopping`).
- No VPS queue position: `status_for_user` sets `queue_position: null` for local users.
- `machine_busy` is always `false` for local users (they do not wait on VPS Chrome).

**Admin snapshot** (`queue_snapshot`)

- `running` / `queued`: server FIFO only.
- `local_running`: jobs on desktop agents (`running` / `stopping`, `execution_mode=local`).

---

## 1.5.3 — Server mode unchanged

Server users still get `run.json` / `secrets.json` on the VPS, `worker.py` subprocess, stop file on server job dir, and force-stop after 15s if needed.

---

## 1.5.4 — Stop contract (local)

**UI / session:** `POST /api/run` stop → `jobs.request_stop`:

1. Sets job `status` to `stopping` in the DB.
2. Does **not** write `stop` on the server job dir for local users (agent has no access to VPS paths).
3. Does **not** run VPS `_force_stop_if_needed` for local users.

**Desktop agent** (Electron / `agent_dev.py`):

1. Poll `GET /api/agent/work`.
2. When `stop_requested: true` (job status `stopping`), write **`{job_dir}/stop`** on the **local** machine (same file name as VPS: one line `stop`).
3. `worker.py` watches that file and exits; agent then `POST /api/agent/complete`.

Server-mode stop is unchanged: `write_stop_flag` on VPS + optional force kill.

---

## Startup recovery

`recover_and_start` marks stale **server** `running`/`stopping` jobs failed after a VPS restart (kill local PIDs, clear debug port). **Local** jobs are left as-is so a desktop agent can still complete or report via `/api/agent/complete`.

---

## Manual checks

1. User A `local`, user B `server`: A queued + agent running → B Launch still starts VPS Chrome.
2. User A `local`, Launch without agent → job stays `queued`; no `worker.py` on VPS (`ps` / Task Manager).
3. Stop while local job running → DB `stopping`, work poll `stop_requested: true`; no server `data/users/.../stop` required.
4. User B `server` only → same behavior as before Phase 1.
