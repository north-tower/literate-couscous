# Phase 1.6 — API / UI feedback

> **Status:** Implemented in `db.py`, `jobs.py`, `app.py`, `static/app.js`.  
> **Related:** [sendline-desktop-transition-plan.md](./sendline-desktop-transition-plan.md) Phase 1.6

---

## Summary

The hosted UI and session APIs expose whether the account runs on **Sendline Desktop** and whether an agent is **online** (recent heartbeat). Launch is blocked with **503** when the desktop agent is required but offline.

---

## 1.6.1 — `execution_mode` and `agent_online`

**Window:** `db.AGENT_ONLINE_WINDOW_SEC` (60s). Any non-revoked `agent_tokens.last_seen_at` within that window counts as online (heartbeat, work poll, or both).

**Responses include:**

| Field | Values |
|-------|--------|
| `execution_mode` | `server` \| `local` |
| `agent_online` | `true` / `false` — for `server` users always `false` (desktop not used) |

**Endpoints:**

- `GET /api/me` — after login
- `GET /api/status` — same fields merged into job snapshot (`jobs.status_for_user`)

Helper: `db.get_desktop_feedback(user_id)`.

---

## 1.6.2 — `POST /api/run` when local + offline

Before creating a job, `jobs.enqueue` checks `execution_mode=local` and `db.user_agent_online(user_id)`.

If offline → **`503`** JSON:

```json
{ "ok": false, "error": "Open Sendline Desktop on your computer to run campaigns." }
```

No queued row is created (fail fast).

Other enqueue errors (e.g. duplicate open job) remain **`409`**.

---

## 1.6.3 — Campaign UI banner

When `execution_mode === "local"` and `agent_online === false`:

- Banner: **“Open Sendline Desktop to run campaigns.”**
- **Start sending** disabled until the agent is online
- Status poll every 15s refreshes `agent_online` while in local mode

---

## Manual checks

1. User on `server` — no banner; `agent_online: false` in `/api/status`.
2. User on `local`, no agent — banner visible; `POST /api/run` → 503.
3. Run `POST /api/agent/heartbeat` with Bearer token — within 60s: banner hides, launch succeeds (job stays `queued` until work poll).

```powershell
# After setting user to local and registering a token
curl -s -H "Authorization: Bearer $TOKEN" -X POST http://127.0.0.1:5055/api/agent/heartbeat -d "{}"
curl -s -b cookies.txt http://127.0.0.1:5055/api/status | jq .execution_mode,.agent_online
```
