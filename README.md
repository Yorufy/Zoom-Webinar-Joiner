# Zoom Webinar Joiner

Automatically joins a Zoom **webinar** at a scheduled time, using the Zoom
desktop client you're already signed into on Windows (not a bot account).
Wakes the machine from sleep if needed, verifies you actually joined
(including on a locked screen), detects when the webinar ends and cleans up,
and if the scheduled link stops working it can recover a replacement link
from a WhatsApp group and, as a last resort, hand the problem to a scoped
Claude agent. 

## Requirements

- Windows, with the interactive user signed in (the screen may be **locked**,
  but the account may not be **signed out**, and the PC may not be fully
  powered off).
- Python 3.11+.
- Zoom desktop client, signed in as your own account.
- Optional: WhatsApp Web login, if you want the WhatsApp fallback.
- Optional: a Claude Code CLI (`claude`) on PATH, if you want escalation.

## Installation & setup

1. **Install dependencies**
   ```
   pip install -r requirements.txt
   playwright install chromium
   ```
   (`playwright` is only used by the WhatsApp fallback and its one-time
   login script: everything else is Python standard library.)

2. **Configure the Zoom desktop client** (Settings):
   - Enable "Automatically join computer audio when joining a meeting".
   - Disable "Always show video preview dialog when joining a video meeting".
   - Disable "Ask me to confirm when I leave a meeting".
   - Join one meeting manually first to dismiss any first-run browser
     "Open Zoom Meetings?" prompt.
   - Setting names vary by Zoom version: see `setup/PREREQS.md` for the
     full, current checklist.

3. **Configure Windows power settings**:
   - Power Options → Advanced settings → Sleep → enable wake timers, both on
     battery and plugged in.
   - Lid-close action = "Do nothing" when plugged in.
   - Keep the laptop plugged in and logged in (locked is fine) during any
     scheduled webinar.
   - Laptops using Windows "Modern Standby" may ignore wake timers. If
     yours does, disable sleep entirely on days you have a scheduled join.

4. **(Optional) Log into WhatsApp Web**, only if you want the fallback:
   ```
   python setup/setup_whatsapp.py
   ```
   Scan the QR code when prompted (WhatsApp app → Linked devices → Link a
   device). This creates a persistent browser profile under
   `%LOCALAPPDATA%\zoomjoin\wa-profile` that the runtime fallback reuses. You only do this once. Note the **exact display name** of the WhatsApp
   group you want scanned; you'll pass it in per-meeting (see Optionality).

5. **(Optional) Set up escalation**, only if you want the Claude-agent
   fallback: see [Optional configuration](#optional-configuration) below.

## Usage

Everything goes through the `zoomjoin` CLI, run from the repo root:

```
python -m zoomjoin <command> ...
```

### `add` -- schedule a meeting

```
python -m zoomjoin add --url <zoom-url> --at "YYYY-MM-DD HH:MM" [--group <name>] [--duration <minutes>]
```

- `--url` (required): a `https://<sub>.zoom.us/j|w|s|my/...` link, with or
  without `?pwd=` / `&tk=` (registration token), or a raw `zoommtg://` link.
- `--at` (required): local time, exactly `YYYY-MM-DD HH:MM` (24h, no
  seconds). Must be in the future.
- `--group` (optional): WhatsApp group name to scan for a replacement link
  if the join fails. Omit to disable the WhatsApp fallback for this meeting.
- `--duration` (optional, integer minutes): only used as a hint to bound
  how far back the WhatsApp scan looks for a replacement link.

On success, prints `scheduled <id> at <time>` and creates a self-deleting
Windows Task Scheduler task (with wake-from-sleep enabled) that runs
`zoomjoin run <id>` at the scheduled time.

### `list` -- show scheduled meetings

```
python -m zoomjoin list
```

Prints id, time, status, group, and a truncated URL for every meeting;
`no meetings scheduled` if there are none.

### `remove` -- cancel a meeting

```
python -m zoomjoin remove <id>
```

Deletes the underlying scheduled task and the stored record.

### `dispatch` -- schedule from a raw text message

```
python -m zoomjoin dispatch "<raw message>"
```

For use by a remote/phone-triggered session (e.g. Claude Dispatch) that
receives free text and shouldn't have to parse it itself. Internally this
parses the message and then runs the same code path as `add`, so success
output and most exit codes match `add`. Examples of supported text:

```
join https://zoom.us/j/123?pwd=Ab9 at tomorrow 2pm
join https://zoom.us/j/123?pwd=Ab9 at 2026-12-31 09:00 group "Team Sync"
join https://zoom.us/j/123?pwd=Ab9 in 30 minutes for 45 min
```

Recognized time formats, in priority order: ISO datetime
(`YYYY-MM-DD HH:MM[:SS]`), date + 12h clock (`2026-12-31 9am`),
`today`/`tomorrow` + time, relative (`in 30 minutes`, `in 2 hours`), or a
bare clock time (`2pm`, `14:00`), which is resolved to today if that time hasn't
passed yet, else tomorrow. Group name: `group "Name"` / `--group "Name"`
(quoted, spaces OK) or `group=Name` / `group:Name` (single token). Duration:
`for 45 min`, `duration=45`, or a bare `45 minutes` (not preceded by `in`,
to avoid colliding with relative time).

If the message can't be parsed, prints an error and a usage hint to
stderr and exits **2**. The caller should ask the user to resend, instead of 
guessing a link or time. `runbooks/dispatch.md` is the full instruction set
a remote Claude session follows when acting on this command's output.

### `run` -- internal: what the scheduled task actually invokes

```
python -m zoomjoin run <id>
```

Not meant to be run manually -- this is what the Task Scheduler task calls
at the scheduled time. Loads the meeting, launches the join, verifies it,
and on failure falls through WhatsApp recovery and then escalation. Useful
manually only for re-triggering a run or for debugging (see below).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (scheduled / listed / removed / joined-and-ended cleanly / escalation agent reported success) |
| 1 | Generic failure: bad URL, bad/past `--at`, scheduler error, unknown id, or join+fallback+escalation all failed |
| 2 | `dispatch`-only: the raw message couldn't be parsed |

## Optionality

Nothing below is required for the basic "join at a scheduled time" path.

- **WhatsApp fallback**, which is entirely opt-in per meeting: omit `--group` (or
  the `group "..."` clause in a dispatch message) and it's skipped. Only
  one group is scanned per meeting, chosen by exact name; there's no
  group-discovery or multi-group scanning.
- **Escalation agent**, which runs automatically whenever a direct join and (if
  configured) the WhatsApp fallback both fail. To disable it entirely,
  don't have a `claude` binary resolvable (see below) -- escalation then
  no-ops and just notifies that it couldn't run.
- **Escalation config** (all optional; env var wins over `config.json` at
  repo root, which is not checked in and not created by default):
  - `ZOOMJOIN_CLAUDE_BIN` / `config.json` key `claude_bin`: path to the
    `claude` binary. Defaults to whatever `claude` resolves to on PATH.
  - `ZOOMJOIN_ESCALATE_MODEL` / `config.json` key `escalate_model`:
    defaults to `sonnet`.
- **Push notifications**: a Windows toast always fires on key events
  (join failure, escalation outcome, etc.). To *also* get a phone push via
  [ntfy.sh](https://ntfy.sh), set `ZOOMJOIN_NTFY_TOPIC` (or `config.json`
  key `ntfy_topic`) to a topic name you've picked; leave unset to skip it.
- **Duration hint**: purely advisory (bounds the WhatsApp scan window);
  omitting it just widens that window.

## Debugging

- **Logs**: every `run` writes to `logs/<meeting-id>/<timestamp>.log`.
  Escalation additionally writes `escalation-context-<timestamp>.json`
  (what was tried) and `escalation-agent-<timestamp>.log` (the full agent
  transcript) next to the run log.
- **Re-trigger a run manually**: `python -m zoomjoin run <id>` -- same code
  path the scheduled task uses; useful for testing without waiting for the
  scheduled time (the task itself isn't deleted by this).
- **Verify the join-detection window signatures still match your Zoom
  version**:
  ```
  python tools/discover_zoom_windows.py            # loop every 2s
  python tools/discover_zoom_windows.py --once
  python tools/discover_zoom_windows.py --interval 5
  ```
  Run before/during/after a real meeting and compare against the classes
  hardcoded in `zoomjoin/verify.py` -- these are known to drift across Zoom
  client updates and are not an official API.
- **Verify join-detection works while the screen is locked**:
  ```
  python tools/lock_test.py
  ```
  Join a test meeting, run this, then lock the screen (Win+L) for at least
  30s; it logs `in_meeting=<bool>` every 2s for 90s to `locktest.txt`.
- **Re-authenticate the WhatsApp fallback** if it starts reporting
  `logged_out`: re-run `python setup/setup_whatsapp.py` and scan the QR
  code again.
- **WhatsApp Web selector drift**: if the fallback stops finding the group
  or its messages, WhatsApp Web's DOM has likely changed. The selectors are
  centralized in one constants block at the top of `zoomjoin/whatsapp.py`
  for exactly this reason.
- **Scheduled task inspection**: tasks are named `zoomjoin-<meeting-id>`;
  inspect/remove them directly with `schtasks /Query /TN zoomjoin-<id>` or
  Task Scheduler's GUI if `zoomjoin remove` can't reach them for some reason.
- **Tests**: `pytest tests/` covers URL parsing, dispatch parsing, the
  store, the scheduler's XML generation, monitor classification,
  notifications, escalation wiring, WhatsApp result handling, and the
  overall `run` control flow. `verify.py` and `joiner.py` have no unit
  tests (they depend on live Win32 window state and `os.startfile`); they're
  validated instead with the two manual tools above against a real meeting.

## Use cases

- **Attend a recurring or one-off Zoom webinar unattended**, including
  overnight or while away from the laptop, as long as it stays plugged in,
  logged in, and (locked is fine) reachable by its scheduled wake timer.
- **Recover automatically from an expired/changed webinar link** by
  scanning a WhatsApp group you already know the host posts replacement
  links to.
- **Schedule a join by texting a link and time** from your phone to a
  Claude Dispatch session, without that session needing to parse anything
  itself (`runbooks/dispatch.md` governs exactly what it's allowed to do).
- **Get notified of the outcome either way** toast always, plus an
  optional ntfy.sh push to your phone, which indicates whether the tool joined cleanly,
  recovered via WhatsApp, or had to escalate.
- **Fall back to a scoped, time-boxed Claude agent** for the rare case
  where both deterministic paths fail, it can retry the join, re-scan
  WhatsApp more loosely, and check Zoom's status page, but cannot change
  system settings, message anyone, or touch its own scheduled tasks; it
  always ends with a JOINED/NOT JOINED verdict that's relayed to you.

## Limitations

- **Zoom Meetings are not supported the same way Webinars are.** The URL
  parser doesn't distinguish meeting vs. webinar links (both convert to a
  `zoommtg://` deep link identically) but the tool has **no logic to
  handle the "join with video/mic on or off?" preview/confirmation dialog**
  that regular Zoom Meetings show attendees. The stated mitigation is a
  Zoom client setting ("Always show video preview dialog..." disabled) done
  once during setup, not anything handled in code. If that setting isn't
  correctly applied (or a future Zoom version reintroduces a similar
  prompt), a Meeting join can hang waiting on a dialog nothing will ever
  dismiss. Webinars generally don't show this dialog at all, which is why
  the tool was designed and tested against webinars.
- **The WhatsApp fallback group is a single, manually-supplied exact
  name**. There is no group picker, no multi-group scan, and no
  auto-discovery of "which group has the link." You note the group's exact
  display name yourself during setup and pass it via `--group`/`group
  "..."` per meeting.
- **Crash-vs-end detection is a heuristic.** A join that drops within 10
  minutes is assumed to be a crash (triggering the fallback chain); one
  that lasts 10+ minutes and then disappears is assumed to be the host
  ending it. A legitimately short (<10 min) webinar would be misclassified
  as a crash.
- **Both the Zoom-window and WhatsApp-Web selectors are UI-version
  fragile**, by nature of not being official APIs. They're centralized and
  documented for re-validation, not guaranteed stable across client/web
  updates.
- **Windows-only.** Relies on `schtasks.exe`, Win32 window enumeration via
  `ctypes`, and `os.startfile`'s URL-protocol handling.
- **The PC must stay logged in and (at most) locked**: a fully signed-out
  or powered-off machine can't run an interactive-session scheduled task,
  and is explicitly out of scope.
- **No input injection anywhere** (by design, so everything still works on
  a locked screen). This also means the tool can't do anything a window
  message or URL-protocol launch can't accomplish; anything Zoom's UI
  requires *clicking or typing* through (beyond what's covered above) isn't
  handled.
- **Escalation depends on a local `claude` CLI being installed and
  authenticated**; if it isn't, escalation just reports that it couldn't
  run rather than actually attempting recovery.
