# Sendline (`automate/`)

Hosted Flask API + browser UI for LinkedIn campaign automation. See [HANDOVER.md](./HANDOVER.md) for day-to-day dev setup.

**Desktop transition:** [docs/sendline-desktop-transition-plan.md](./docs/sendline-desktop-transition-plan.md)

---

## Dev desktop agent (Phase 1.7)

Use this to prove **local execution** on your PC before the Electron app exists.

1. Start the API: `py app.py`
2. Set your user to `execution_mode = 'local'` ([SQL doc](./docs/sendline-desktop-phase-1-1-data-model.md))
3. In the campaign UI: **Desktop** → **Link this computer**, copy the token into Sendline Desktop Settings (or `py scripts/agent_dev.py register ...`).
4. Run Electron: `cd ../my-electron-app && pnpm dev` — do **not** run `agent_dev.py run` at the same time.
5. Click **Start sending** in the loaded UI — Chrome runs locally; logs stream in the UI.

CLI-only agent (no Electron): see [Phase 1.7 doc](./docs/sendline-desktop-phase-1-7-dev-agent.md) for `agent_dev.py run`.

---

## Sendline Desktop (Phase 2–3)

Electron shell in `../my-electron-app/` — hosted UI + local agent loop. Worker bundle:

```powershell
.\scripts\build-worker.ps1
cd ..\my-electron-app
pnpm copy-worker
pnpm dev
```

Details: [docs/sendline-desktop-phase-2-3-electron-worker.md](./docs/sendline-desktop-phase-2-3-electron-worker.md)
