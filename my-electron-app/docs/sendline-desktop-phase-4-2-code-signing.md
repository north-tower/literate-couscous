# Phase 4.2 — Windows code signing

**Goal:** Signed NSIS installer and app binaries so SmartScreen and enterprise policies treat Sendline Desktop as a known publisher.

Unsigned builds (`pnpm run dist:win`) remain valid for internal dev; clients should get signed builds.

## 4.2.1 Obtain a code signing certificate

This step is **outside the repo** — purchase and complete CA validation before signing.

| Type | SmartScreen | Notes |
|------|-------------|--------|
| **Standard / OV** | Reputation builds over time; early installs may still warn | Lower cost; export to `.pfx` for CI |
| **EV** | Best first-run experience | USB/HSM token; often no easy PFX export — may need SignTool + vendor tooling |

**Suggested CAs:** DigiCert, Sectigo, SSL.com (compare EV vs OV for your rollout timeline).

**After issuance:**

1. **PFX path:** Export or receive `.pfx` + password → store offline; never commit (see `.gitignore`).
2. **Windows store path:** Import cert to `Current User → Personal` (or machine store on build VM). Note exact **Subject** string for `certificateSubjectName`.
3. Optional: register with [Microsoft Trusted Root Program](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/get-started) if you distribute widely (CA handles most of this).

**Checklist (operator):**

- [ ] Legal entity name matches cert Subject
- [ ] PFX or token access limited to release engineers / CI secret store
- [ ] Timestamp URL reachable from build machine (default: DigiCert in `electron-builder.yml`)

## 4.2.2 Configure signing for electron-builder

electron-builder uses **Authenticode** via `@electron/windows-sign` (bundled). Two supported patterns:

### Option A — PFX file (CI-friendly)

1. Copy `.env.signing.example` → `.env.signing` (gitignored).
2. Set:

   ```env
   CSC_LINK=C:\secure\sendline-code-sign.pfx
   CSC_KEY_PASSWORD=...
   ```

3. Build:

   ```powershell
   pnpm run dist:win:signed
   ```

`WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD` override on Windows only if you need different creds than macOS (not used for Sendline Desktop yet).

### Option B — Certificate in Windows store

1. Copy `electron-builder.local.example.yml` → `electron-builder.local.yml` (gitignored).
2. Set `win.certificateSubjectName` to match **Subject** in certmgr (e.g. `CN=Sendline Ltd, O=...`).
3. Ensure `.env.signing` does **not** set `CSC_LINK` (otherwise PFX wins).
4. Run `pnpm run dist:win:signed`.

### Shared builder settings

In `electron-builder.yml`:

- `signDlls: true` — signs bundled DLLs under `win-unpacked`
- `signingHashAlgorithms: [sha256]`
- `timeStampServer: http://timestamp.digicert.com`

### Manual SignTool (debug only)

If electron-builder fails, sign one file with the Windows SDK:

```powershell
signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a "dist\win-unpacked\Sendline Desktop.exe"
```

Prefer fixing electron-builder env/config over post-signing manually (NSIS installer must also be signed — builder handles both).

### Verify after build

```powershell
pnpm run verify:win-signature
# CI / release gate:
pnpm run verify:win-signature:signed
```

Expect `Status: Valid` and your publisher Subject on:

- `dist/Sendline Desktop Setup *.exe`
- `dist/win-unpacked/Sendline Desktop.exe`

## 4.2.3 Test install (clean machine)

Run on a **VM or PC without** Node, Python, or repo checkout — only Google Chrome + signed installer.

| Step | Pass criteria |
|------|----------------|
| Download / copy signed `Sendline Desktop Setup *.exe` | File shows your publisher in Properties → Digital Signatures |
| Run installer | No “Unknown publisher” from unsigned binary; SmartScreen acceptable for your cert type |
| Launch app | Hosted UI loads; tray icon present |
| Settings | Paste agent token; agent polls API |
| Launch campaign (`local` user) | Chrome opens; logs stream; worker from `resources/sendline-worker/` |
| Reboot optional | App starts from Start Menu shortcut |

**Unsigned dev build:** same flow works but SmartScreen may show “Windows protected your PC” — use **More info → Run anyway** for internal tests only.

## Scripts reference

| Command | Purpose |
|---------|---------|
| `pnpm run dist:win` | Unsigned installer (default dev) |
| `pnpm run dist:win:signed` | Loads `.env.signing` + optional `electron-builder.local.yml` |
| `pnpm run verify:win-signature` | Report Authenticode status on latest `dist/` artifacts |
| `pnpm run verify:win-signature:signed` | Exit 1 if any artifact is `NotSigned` |

## Related

- Phase 4.1: [sendline-desktop-phase-4-1-electron-builder.md](./sendline-desktop-phase-4-1-electron-builder.md)
