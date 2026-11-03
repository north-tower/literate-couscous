# Phase 1.2 — Agent authentication

> **Status:** Implemented in `auth.py`, `db.py`, `app.py`.  
> **Related:** [sendline-desktop-transition-plan.md](./sendline-desktop-transition-plan.md) Phase 1.2

---

## Session routes (hosted UI)

All mutating agent **management** routes require login + CSRF header `X-CSRF-Token` (value from `/api/session` or `/api/me` after login).

| Method | Path | Body | Response |
|--------|------|------|----------|
| `POST` | `/api/agent/register` | `{ "label": "Office PC" }` | `201` — `{ "token": "<plaintext once>", "agent_token": { ... } }` |
| `POST` | `/api/agent/revoke` | `{ "id": 1 }` | Revokes token owned by current user |
| `GET` | `/api/agent/tokens` | — | List tokens (masked; no secret) |

Register shares the login rate limiter (`8` attempts / `15` minutes per IP).

---

## Agent routes (desktop / scripts)

Use **`Authorization: Bearer <token>`** only — no session cookie. Phase 1.4 adds work/logs/heartbeat/complete.

Decorator: `@auth.agent_required` sets `g.agent_user` and `g.agent_token`.

Token storage: SHA-256 hash in `agent_tokens.token_hash`; plaintext shown once at register.

---

## Manual test (curl)

```bash
# 1) Log in (save session cookie)
curl -c cookies.txt -X POST http://127.0.0.1:5055/api/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"your-password"}'

# 2) Read CSRF from session
CSRF=$(curl -b cookies.txt http://127.0.0.1:5055/api/me | jq -r .csrf)

# 3) Register agent
curl -b cookies.txt -X POST http://127.0.0.1:5055/api/agent/register \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"label":"Dev laptop"}'

# 4) List tokens
curl -b cookies.txt http://127.0.0.1:5055/api/agent/tokens

# 5) Revoke
curl -b cookies.txt -X POST http://127.0.0.1:5055/api/agent/revoke \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $CSRF" \
  -d '{"id":1}'
```

After Phase 1.4, verify Bearer auth with `GET /api/agent/work`.

---

## Next steps (Phase 1.3+)

- ~~Job payload + attachment URLs for agent work bundle.~~ (Phase 1.3 — `jobs.build_agent_work_bundle`, attachment route)
- Agent lifecycle API using `@auth.agent_required` (Phase 1.4).
