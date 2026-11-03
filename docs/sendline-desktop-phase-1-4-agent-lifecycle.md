# Phase 1.4 — Agent work lifecycle API

> **Status:** Implemented in `jobs.py`, `app.py`.  
> **Related:** [sendline-desktop-transition-plan.md](./sendline-desktop-transition-plan.md) Phase 1.4

All routes use `@auth.agent_required` (`Authorization: Bearer <token>`). No CSRF on agent routes.

---

## Endpoints

| Method | Path | Body | Behavior |
|--------|------|------|----------|
| `GET` | `/api/agent/work` | — | Claim `queued` job for `execution_mode=local` → full work bundle; or return in-progress metadata + `stop_requested` + `log_cursor`. Updates token `last_seen_at`. |
| `POST` | `/api/agent/logs` | `{ "job_id", "lines": string[] }` | Append lines via `jobs.append_log`. |
| `POST` | `/api/agent/heartbeat` | `{ "worker_version?", "agent_version?" }` | Touch token `last_seen_at` (versions reserved). |
| `POST` | `/api/agent/complete` | `{ "job_id", "exit_code" }` | Same exit mapping as VPS `jobs._watch`; calls `start_next()`. |

Stop is surfaced on work poll: `"stop_requested": true` when job status is `stopping` (no separate stop route).

---

## `GET /api/agent/work` responses

**Idle** (no open job, or `server` user with queued job waiting on VPS):

```json
{ "ok": true, "work": null }
```

**New claim** (`queued` → `running`): `work` is `build_agent_work_bundle(...)` plus:

```json
{
  "ok": true,
  "work": { "...": "full bundle with secrets" },
  "stop_requested": false,
  "log_cursor": 4
}
```

**In progress** (`running` / `stopping`): lightweight `work` (no secrets; avoids re-assigning proxy):

```json
{
  "ok": true,
  "work": {
    "job_id": 12,
    "status": "running",
    "people_count": 2
  },
  "stop_requested": false,
  "log_cursor": 42
}
```

---

## Exit codes (`POST /api/agent/complete`)

Same as `jobs.finalize_job_from_exit_code`:

| Code | Status |
|------|--------|
| `0` | `done` |
| `75` | `paused` |
| `1`, `130`, or stop while `stopping` | `cancelled` |
| other | `failed` |

---

## Manual smoke test

Prerequisites: user `execution_mode=local`, campaign + LinkedIn saved, agent token from `POST /api/agent/register`.

```bash
# Queue a run from UI, then:
curl -s -H "Authorization: Bearer TOKEN" http://127.0.0.1:5055/api/agent/work

curl -s -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d "{\"job_id\":1,\"lines\":[\"[test] hello\"]}" \
  http://127.0.0.1:5055/api/agent/logs

curl -s -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d "{}" http://127.0.0.1:5055/api/agent/heartbeat

curl -s -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d "{\"job_id\":1,\"exit_code\":0}" \
  http://127.0.0.1:5055/api/agent/complete
```

After Launch + work claim, `/api/logs` in the browser should show the same lines as server-side runs.

---

## Next steps (Phase 1.5–1.7)

- Skip VPS spawn for `local` users (partially done); per-user “busy” for local queue.
- `agent_dev.py` script using these routes end-to-end with `worker.py`.
