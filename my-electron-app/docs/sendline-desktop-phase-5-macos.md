# Phase 5 — macOS (Sendline Desktop)

## Local execution mode (same as Windows)

With `execution_mode=local`, the hosted API **only queues** work. Sendline Desktop on the Mac polls, spawns `worker.py` / `sendline-worker`, and **launches Google Chrome on that Mac** (debug port + dedicated profile under app user data). The VPS never opens LinkedIn for local users.

Prerequisites: user set to **local**, agent token linked, Chrome installed, desktop app running.

## Worker (automate)

Chrome and process management live in `automate/platform_chrome.py` (Windows + macOS). Build the bundled worker **on a Mac**:

```bash
cd automate
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
./scripts/build-worker.sh
```

Requires **Google Chrome** installed at `/Applications/Google Chrome.app`.

## Desktop app (my-electron-app)

```bash
cd my-electron-app
pnpm run dist:mac
```

Outputs under `dist/` (gitignored): `Sendline Desktop-1.0.0-arm64.dmg`, `Sendline Desktop-1.0.0.dmg`, and `mac/` unpacked folders.

Dev on Mac without packaging:

```bash
export SENDLINE_PYTHON=python3   # optional; default is python3
pnpm start
```

## Code signing / notarization

`electron-builder.yml` enables hardened runtime for release builds. For distribution outside your team, configure Apple Developer ID signing and notarization (`CSC_LINK`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, or `electron-builder` notarize options). Unsigned local builds are fine for internal QA.

**CI:** See **`automate/docs/sendline-desktop-ci-macos.md`** on the hosted API repo — GitHub Actions builds unsigned `.dmg` artifacts.

## Smoke test

1. Install from `.dmg`; drag to Applications.
2. Link agent token in Settings.
3. Run a campaign from the hosted UI; confirm `sendline-worker` in `Sendline Desktop.app/Contents/Resources/sendline-worker/`.
4. Exercise stop mid-run and attachment send.
