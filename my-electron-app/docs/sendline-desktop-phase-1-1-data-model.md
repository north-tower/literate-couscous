# Phase 1.1 — Data model (local execution + agent tokens)

> **Status:** Implemented in `automate/db.py` (`init_db` migrations + helpers).  
> **Related:** [sendline-desktop-transition-plan.md](./sendline-desktop-transition-plan.md) Phase 1.1

---

## `users.execution_mode`

| Value | Meaning |
|-------|---------|
| `server` | Default. VPS runs `worker.py` via `jobs._spawn` (today’s behavior). |
| `local` | Job stays queued on the API; Sendline Desktop agent runs worker on the operator PC. |

Column: `TEXT NOT NULL DEFAULT 'server'`. Invalid values are treated as `server` by `db.get_execution_mode()`.

---

## `agent_tokens` table

Stores **hashes** of desktop agent bearer tokens (plaintext shown once at registration in Phase 1.2).

| Column | Type | Notes |
|--------|------|--------|
| `id` | INTEGER PK | |
| `user_id` | INTEGER FK → `users.id` | CASCADE on delete |
| `label` | TEXT | Operator-visible name (e.g. “Office PC”) |
| `token_hash` | TEXT UNIQUE | Never store plaintext token |
| `created_at` | REAL | Unix timestamp |
| `last_seen_at` | REAL | Updated by agent heartbeat (Phase 1.4) |
| `revoked_at` | REAL | Non-null when revoked |

---

## Set a user to `local` (pilot / admin)

**Admin UI:** not built yet (Phase 4.3 / optional admin panel). Use SQL or a one-off script until then.

### SQLite (dev)

Stop the Flask app or use a read-only window; prefer stopping the app to avoid WAL locks.

```bash
cd automate
sqlite3 data/sendline.db
```

```sql
-- List users
SELECT id, email, execution_mode FROM users;

-- Enable desktop execution for one operator
UPDATE users SET execution_mode = 'local' WHERE id = 2;

-- Revert to VPS worker
UPDATE users SET execution_mode = 'server' WHERE id = 2;
```

### Python (same DB as the app)

```python
import db

db.init_db()
db.set_user_execution_mode(2, "local")   # admin-only in API later
print(db.get_execution_mode(2))
```

### Rollout

Keep existing production users on `server` until pilot sign-off (transition plan **M.1–M.3**). Only flip operators who have Sendline Desktop installed and a registered agent token.

---

## Next steps (Phase 1.2+)

- `POST /api/agent/register` writes `agent_tokens` rows.
- `auth.agent_required` validates Bearer token against `token_hash`.
- `jobs._spawn` skips VPS worker when `get_execution_mode(user_id) == 'local'`.
