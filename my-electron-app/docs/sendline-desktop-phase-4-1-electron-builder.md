# Phase 4.1 — electron-builder (Windows installer)

## Config

- **`electron-builder.yml`** — app ID `com.sendline.desktop`, product name **Sendline Desktop**, NSIS x64.
- **`extraResources`** — copies `resources/sendline-worker/` → `process.resourcesPath/sendline-worker/` (same layout as Phase 3.2).
- **Icons** — `build/icon.png` (256×256); electron-builder emits multi-size `.ico` for Windows. Regenerate with `pnpm run generate-icon`.

## Build

Prerequisites: Google Chrome on the target PC (not bundled). Worker must be built in `automate/` first.

```powershell
cd C:\Users\mhki\automate
.\scripts\build-worker.ps1

cd C:\Users\mhki\my-electron-app
pnpm run dist:win
```

Outputs (gitignored under `dist/`):

- `Sendline Desktop Setup 1.0.0.exe` — NSIS installer
- `win-unpacked/` — unpacked app for smoke tests

`dist:win` runs `copy-worker` then `electron-builder --win`.

## Code signing

See [sendline-desktop-phase-4-2-code-signing.md](./sendline-desktop-phase-4-2-code-signing.md). Unsigned `dist:win` builds work for dev; use `dist:win:signed` when a cert is configured.

## Manual smoke test

1. Install from `Sendline Desktop Setup *.exe` on a machine without Python.
2. Configure agent token in Settings.
3. Launch a campaign from the hosted UI; confirm `sendline-worker.exe` runs from `%LOCALAPPDATA%\Programs\sendline-desktop\resources\sendline-worker\` (exact path varies with install dir).
