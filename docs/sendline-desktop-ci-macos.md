# CI — macOS Sendline Desktop (GitHub Actions)

Workflow: [`.github/workflows/dist-mac-desktop.yml`](../.github/workflows/dist-mac-desktop.yml)

Builds the PyInstaller worker from this repo and packages **Sendline Desktop** as `.dmg` on a GitHub-hosted Mac runner. You do not need a local macOS VM.

---

## One-time setup (monorepo)

### 1. Add the desktop app to this repo

On your PC, copy the Electron project **into** the automate repo (keep the folder name `my-electron-app`):

```powershell
# From PowerShell (adjust paths if yours differ)
Copy-Item -Recurse C:\Users\mhki\my-electron-app C:\Users\mhki\automate\my-electron-app
```

Then remove bulky folders that `.gitignore` already excludes (safe if copy is fresh):

```powershell
Remove-Item -Recurse -Force C:\Users\mhki\automate\my-electron-app\node_modules -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force C:\Users\mhki\automate\my-electron-app\dist -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force C:\Users\mhki\automate\my-electron-app\resources\sendline-worker\* -Exclude .gitkeep -ErrorAction SilentlyContinue
```

Commit from the **automate** repo root:

```powershell
cd C:\Users\mhki\automate
git add my-electron-app .github docs
git status   # should NOT list node_modules/ or dist/
git commit -m "Add Sendline Desktop app and macOS CI workflow"
git push origin master
```

What gets committed: `electron/`, `package.json`, `pnpm-lock.yaml`, `build/icon.png`, scripts, docs, etc.  
What stays local only: `node_modules/`, `dist/`, copied `resources/sendline-worker/` binaries (see `my-electron-app/.gitignore`).

### 2. Enable Actions on GitHub

Repo **Settings → Actions → General** → allow workflows.

---

## How to get the build

### Option A — Run workflow by hand (easiest first time)

1. Open **https://github.com/north-tower/literate-couscous/actions** (your repo’s Actions tab).
2. Click **Build Sendline Desktop (macOS)** in the left sidebar.
3. Click **Run workflow** → choose branch (e.g. `master`) → **Run workflow**.
4. Wait until the job shows a **green check** (often ~15–25 minutes).
5. Open that workflow run.
6. Scroll to **Artifacts** at the bottom.
7. Download **`sendline-desktop-macos-<run number>`** (a `.zip`).
8. Unzip on your Mac (or transfer the zip to a Mac). Inside you’ll find files like:
   - `Sendline Desktop-1.0.0-arm64.dmg`
   - `Sendline Desktop-1.0.0.dmg` (Intel x64, if built)

Install: open the `.dmg`, drag **Sendline Desktop** to Applications.  
These CI builds are **unsigned** — macOS may warn on first open (right‑click → Open, or System Settings → Privacy & Security).

### Option B — Tag push (same artifact, good for “releases”)

```powershell
cd C:\Users\mhki\automate
git tag sendline-desktop-v1.0.0
git push origin sendline-desktop-v1.0.0
```

Then follow steps 4–8 above on the workflow run triggered by that tag.

### Option C — GitHub CLI (download from terminal)

After a successful run:

```bash
gh run list --workflow=dist-mac-desktop.yml
gh run download <RUN_ID> -n sendline-desktop-macos-<N>
```

Requires [GitHub CLI](https://cli.github.com/) logged in (`gh auth login`).

---

## Alternative: desktop app in a second repo

If you do **not** use `my-electron-app/` inside this repo:

1. Push the Electron project to another GitHub repo.
2. In **this** repo: **Settings → Secrets and variables → Actions → Variables** → add **`SENDLINE_DESKTOP_REPO`** = `owner/repo-name`.
3. If that repo is private, add secret **`SENDLINE_DESKTOP_PAT`** (PAT with read access).
4. Run the workflow as in Option A (leave **desktop_repository** empty if the variable is set).

---

## What the job does

1. `pip install` + `scripts/build-worker.sh` → `dist/sendline-worker/sendline-worker`
2. `my-electron-app`: `pnpm run copy-worker` + `electron-builder --mac`
3. Uploads `dist/*.dmg` and related files as artifacts (kept **14 days** on GitHub)

Signing is **off** in CI (`CSC_IDENTITY_AUTO_DISCOVERY=false`) so no Apple Developer certificate is required.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Missing `my-electron-app/package.json` | Copy desktop folder into repo and push, or set `SENDLINE_DESKTOP_REPO`. |
| `pnpm install --frozen-lockfile` fails | Commit an updated `pnpm-lock.yaml` from the desktop app. |
| Private desktop repo 404 | Add `SENDLINE_DESKTOP_PAT` with read access. |
| No **Artifacts** section | Job failed — open failed step logs. |
| Artifact expired | Re-run workflow; artifacts delete after 14 days. |

More on local Mac builds: `my-electron-app/docs/sendline-desktop-phase-5-macos.md`.
