# Sendline — Client Overview

**One-page briefing for demos and stakeholder meetings**  
**Product type:** Local LinkedIn Messaging outreach tool (operator MVP)  
**Runs on:** Windows PC with Google Chrome (localhost only — not a cloud SaaS)

---

## Elevator pitch

Sendline lets an operator paste a curated list of people, write one personalized message template, optionally attach a file, and launch a worker that opens Chrome and sends those LinkedIn messages one by one — with a live log so you can watch and stop at any time.

It proves a practical workflow: **queue → personalize → send → observe**. It is a working tool for controlled outreach, not a finished multi-user platform.

---

## How it works

1. Open the Sendline UI in the browser (`http://127.0.0.1:5055`).
2. Enter **full names** (one per line).
3. Write the message using `{name}` (filled with the person’s first name).
4. Optionally set an **attachment path** (e.g. a PDF deck).
5. Click **Launch worker**.
6. Chrome opens on a dedicated automation profile and walks LinkedIn’s normal “New message” flow:
   - search recipient → insert message → attach (if any) → send.
7. **First run on a machine:** sign into LinkedIn once in that automation window. Later runs reuse the session.
8. Watch the **live log**; click **Stop** to end the run early.

**Behind the scenes:** a small local web UI saves the campaign config; a Python worker drives Chrome (Selenium). Everything stays on the operator’s machine.

---

## Value for the client

| Benefit | Detail |
|--------|--------|
| Speed | Faster than sending the same outreach by hand |
| Personalization | `{name}` template + optional attachment |
| Control | Visible Chrome window, start/stop, live progress log |
| Local by design | No Sendline cloud account; session stays on the PC |
| Fit | Best for **small, curated** lists — not mass blasting |

---

## Drawbacks and risks (important)

Be upfront in the meeting — credibility matters more than gloss.

| Area | Reality |
|------|---------|
| **LinkedIn terms & account risk** | Automating messaging may violate LinkedIn’s terms and can lead to warnings or restrictions. Use only with clear policy/consent. |
| **Not an official API** | The tool drives the LinkedIn website UI. UI changes can break the flow until selectors are updated. |
| **Name matching risk** | Uses LinkedIn’s top typeahead suggestion. Similar names can address the wrong person if nobody is watching. |
| **Single-machine** | One Windows PC, one operator. Not multi-user, not mobile, not “set and forget from anywhere.” |
| **Chrome disruption** | Launch closes other Chrome windows on that PC. |
| **First-time login** | New machine or wiped profile needs a one-time LinkedIn sign-in in the automation window. |
| **MVP gaps** | No per-person sent/failed dashboard, no confirm-before-send, no rate limits/daily caps, no scheduler, no CRM/CSV pipeline, no run history/dedupe. |
| **Scale** | High volume increases detection and failure risk. Designed for controlled batches. |
| **Maintenance** | Needs technical support when Chrome/LinkedIn changes or a new PC is set up. |

---

## Positioning statement (use this line)

> Sendline is a **working operator tool / MVP**, not a finished SaaS product. It proves the outreach workflow end-to-end. Hardening — recipient confirmation, limits, history, and safer matching — is the natural next phase.

---

## Suggested demo script (5 minutes)

1. Show the UI: names, message with `{name}`, optional attachment.
2. Use **one** safe test contact and a short message.
3. Click **Launch** → show Chrome + live log.
4. If needed, complete LinkedIn login once; show session reuse story for later runs.
5. Click **Stop** (or let one send complete).
6. Immediately cover **drawbacks** (ToS, wrong-name risk, single PC, UI fragility).
7. Close with **next phase** options if they want to invest further.

---

## Natural next phase (if they ask “what’s next?”)

1. **Per-person status** + confirm recipient before send  
2. **Delays / daily caps** to reduce risk  
3. **CSV import** for campaign lists  
4. **Run history / dedupe** so people aren’t messaged twice  

---

## Quick facts

| Item | Value |
|------|--------|
| UI | Sendline (local browser) |
| Stack | Python, Flask, Selenium, Chrome |
| Config | Names, message template, optional attachment |
| Hosting | Localhost only |
| Status | End-to-end working after initial LinkedIn login |

---

*For technical setup and run instructions, see `HANDOVER.md`.*
