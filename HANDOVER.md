# Sendline — Handover Document

**Project:** local LinkedIn messaging automation   
**UI name:** Sendline  
**Status (as of 14 Aug 2026):** Working end-to-end after one manual LinkedIn login in the automation Chrome window. Session is reused on later runs.

---

## 1. What this is

Sendline is a small local app that:

1. Lets you edit a recipient list, message template, and optional attachment path in a browser UI.
2. Saves that config to `run_config.json`.
3. Launches `worker.py`, which opens Chrome, attaches to LinkedIn Messaging, and sends a personalized message to each person.

It is **not** a cloud service. Everything runs on the same Windows machine.

---

## 2. What has already been built

### UI + control server (`app.py` + `static/`)

| Piece | Purpose |
|--------|---------|
| Flask app on `http://127.0.0.1:5055` | Serves the UI and APIs |
| People textarea | One full name per line |
| Message textarea | Must include `{name}` (filled with first name) |
| Attachment path | Optional absolute Windows path to a file |
| **Save** | Writes `run_config.json` |
| **Launch worker** | Saves config, starts `worker.py`, streams logs |
| **Stop** | Terminates the worker process |
| Live log panel | Polls `/api/logs` while a run is active |

**API endpoints**

- `GET /` — UI
- `GET/POST /api/config` — read/write `run_config.json`
- `GET /api/status` — running flag / exit code
- `GET /api/logs?after=N` — incremental log lines
- `POST /api/run` — save config + spawn worker
- `POST /api/stop` — stop worker

### Worker (`worker.py`)

| Capability | Notes |
|------------|--------|
| Chrome launch / attach | Starts Chrome with remote debugging on `127.0.0.1:9222`, Selenium attaches |
| Isolated profile | Uses `chrome_automation_data\` (not your everyday Chrome profile while messaging) |
| Optional profile sync | Can copy from a source Chrome profile (historically “Mike” / Profile 7) via robocopy |
| LinkedIn login | Opens feed; if not logged in, waits up to ~5 minutes for **manual** sign-in |
| Session reuse | After first successful login, later runs keep the automation profile (`RESYNC_FROM_MIKE_EACH_RUN = False`) |
| Messaging flow | Opens new message → typeahead name → Enter → paste/type message → optional attach → Send |
| Config override | Prefers `run_config.json` from the UI over hardcoded lists in `worker.py` |
| Fallbacks | Clipboard paste with human-typing fallback; multiple CSS/XPath selectors |

### Config file (`run_config.json`)

Written by the UI / `/api/run`. Example shape:

```json
{
  "people_names": ["Test Person"],
  "message_template": "Hi {name}, test",
  "attachment_path": null
}
```

### Dependencies (`requirements.txt`)

- `flask>=3.0.0`
- `selenium>=4.20.0`
- `pyperclip>=1.8.2`

Selenium Manager (bundled with modern Selenium) resolves ChromeDriver automatically.

---

## 3. Repository layout

```
automate/
├── app.py                  # Sendline Flask UI + worker launcher
├── worker.py               # LinkedIn Chrome automation
├── run_config.json         # Active campaign config (edited by UI)
├── requirements.txt
├── HANDOVER.md             # This document
├── static/
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── chrome_automation_data/ # Automation Chrome user-data-dir (keeps LinkedIn session)
└── chrome_profile/         # Local copy / related profile data (optional; see worker paths)
```

---

## 4. Prerequisites to run

### Required

1. **Windows** machine with Google Chrome installed.
2. **Python 3** available as `py` or `python` (project was run with the Windows Python launcher).
3. Packages from `requirements.txt` installed into the interpreter that will run the worker.
4. Network access to LinkedIn.
5. A LinkedIn account you can sign into once in the automation Chrome window.

### Important behavioural notes

- **Launch worker kills all Chrome processes** (`taskkill /F /IM chrome.exe`). Close / save other Chrome work first.
- First run (or after clearing `chrome_automation_data`) may require **manual LinkedIn login** in the opened window. Wait until the worker log says logged in.
- Message template **must** contain `{name}`. The worker formats with the **first word** of each full name.
- UI validates: at least one name, `{name}` present, and only one worker at a time.
- Hardcoded `EMAIL` / `PASSWORD` still exist near the top of `worker.py` but login is **manual** now — treat those as leftover secrets and remove/rotate them; do not rely on auto-fill login.

### Optional / environment-specific

`app.py` prefers a Python that can `import selenium, pyperclip`. It checks, in order:

1. Current interpreter (`sys.executable`)
2. `%USERPROFILE%\hailmary\venv\Scripts\python.exe`
3. `.\venv\Scripts\python.exe`

If none work, it falls back to `sys.executable` and the worker may fail on missing packages.

Worker Chrome path constants (in `worker.py`) point at the machine’s Chrome User Data and a named source profile for optional resync. Current defaults assume a profile named like “Mike” / Profile 7 under the local Chrome user-data directory. If you move machines or profiles, update:

- `CHROME_USER_DATA`
- `CHROME_PROFILE`
- `AUTOMATION_USER_DATA`
- `RESYNC_FROM_MIKE_EACH_RUN` (keep `False` after a good LinkedIn login unless you intentionally want to re-copy)

---

## 5. How to install

In PowerShell from the project folder:

```powershell
cd C:\Users\mhki\automate
py -m pip install -r requirements.txt
```

If you use a venv:

```powershell
cd C:\Users\mhki\automate
py -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 6. How to run (normal path — UI)

1. Close important Chrome windows (worker will kill Chrome).
2. Start the UI server:

```powershell
cd C:\Users\mhki\automate
py app.py
```

3. Open **http://127.0.0.1:5055**
4. Enter people (one full name per line), message with `{name}`, optional attachment path.
5. Click **Save** (optional; Launch also saves).
6. Click **Launch worker**.
7. Watch:
   - Chrome open and navigate to LinkedIn
   - **Live run** log in the UI
8. If prompted / stuck on login: sign into LinkedIn **in that automation Chrome window**. Later runs should skip this.
9. Click **Stop** to terminate an in-progress run.

Console when healthy:

```text
Sendline UI -> http://127.0.0.1:5055
```

---

## 7. How to run (worker only — no UI)

Useful for debugging:

```powershell
cd C:\Users\mhki\automate
# Edit run_config.json first, then:
py worker.py
```

Worker still reads `run_config.json` if present; otherwise falls back to the hardcoded list/template in `worker.py`.

---

## 8. Typical first-run checklist

1. `pip install -r requirements.txt` succeeds.
2. `py app.py` starts without port errors (port **5055** free).
3. UI loads at `http://127.0.0.1:5055`.
4. Put **one test name** and a short message containing `{name}`.
5. Launch worker → Chrome opens → log shows attach / LinkedIn open.
6. Complete LinkedIn login if needed → log shows “Logged in” / “Already logged in”.
7. Confirm a message attempt appears in the log for that person.
8. Re-launch later and confirm login is reused (no fresh manual login).

---

## 9. Known issues / ops tips

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Launch does nothing / “Cannot reach Sendline server” | `app.py` not running | Start `py app.py`, hard-refresh the page |
| Worker fails importing selenium/pyperclip | Wrong Python | Install into the interpreter `app.py` selects; check log line `[sendline] python: ...` |
| Always asks to log in again | Profile wiped or resync on | Keep `RESYNC_FROM_MIKE_EACH_RUN = False`; don’t delete `chrome_automation_data` |
| Wrong person selected | LinkedIn typeahead picks top hit | Use distinctive full names; future dry-run confirm would help |
| Chrome won’t start / debug port timeout | Stale Chrome / locks | Worker already force-kills Chrome; retry; clear Singleton* locks under automation profile |
| Login timeout | No manual sign-in within ~5 min | Sign in when the window opens; check `login_debug.png` if created |

Debug artifact on login failure: `login_debug.png` in the project root (screenshot of the Chrome window).

---

## 10. Security / compliance notes for the next owner

- This automates LinkedIn messaging. Use only in line with LinkedIn terms and your organisation’s policies.
- Do **not** commit real credentials. Remove or blank hardcoded email/password in `worker.py`.
- `chrome_automation_data` contains browser session data — treat as sensitive; don’t zip/share casually.
- App binds to **localhost only** (`127.0.0.1:5055`) — not exposed to the LAN by default.

---

## 11. What is intentionally not built yet

Useful backlog (not implemented):

- Per-person sent/failed status in the UI
- Dry-run / confirm recipient before send
- Rate limits / daily caps / scheduled runs
- CSV import
- Attachment file picker (path is typed today)
- Run history / dedupe against previous sends
- Multi-account support
- Non-Windows packaging

---

## 12. Quick command card

| Action | Command |
|--------|---------|
| Install deps | `py -m pip install -r requirements.txt` |
| Start UI | `py app.py` |
| Open UI | http://127.0.0.1:5055 |
| Run worker alone | `py worker.py` |
| Config file | `run_config.json` |

---

*Generated as a handover for the current Sendline setup in `C:\Users\mhki\automate`.*
