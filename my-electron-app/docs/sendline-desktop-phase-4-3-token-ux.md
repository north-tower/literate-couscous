# Phase 4.3 — Token registration UX

## Hosted UI (4.3.1)

On the campaign page (`static/index.html`):

- Nav button **Desktop** opens a modal.
- **Link this computer** → `POST /api/agent/register` with CSRF + optional label (prompt).
- One-time token shown in a read-only field with **Copy token**.
- Banner **Link this computer** shortcut when `execution_mode=local` and agent offline.

## Electron settings (4.3.2)

- Paste token in tray → **Settings…** (unchanged flow).
- `electron/config.js` stores the token with `safeStorage.encryptString` in `agent-config.json` as `token_enc` (base64).
- Legacy plaintext `token` field is migrated to encrypted on next read when OS encryption is available.
- Fallback: plaintext + console warning if `safeStorage.isEncryptionAvailable()` is false.

## Revoke (4.3.3)

- Modal lists `GET /api/agent/tokens` with label, active/revoked, last seen.
- **Revoke** → `POST /api/agent/revoke` with CSRF.

## Manual test

1. Log in at `http://127.0.0.1:5055`, click **Desktop** → **Link this computer**.
2. Copy token → Electron **Settings** → Save.
3. Confirm `/api/status` shows `agent_online: true` within ~60s (heartbeat).
4. Revoke token in modal; agent should stop authenticating until a new token is pasted.
