# Setup Prerequisites

Complete this checklist before the tool is built.

## 1. Python environment

- [ ] Python 3.11+ installed and on PATH (verify with `python --version`)
- [ ] Run `pip install playwright`
- [ ] Run `playwright install chromium`

## 2. Zoom desktop client

- [ ] Zoom installed and signed in to your own account (the tool will join AS this account)
- [ ] In Zoom Settings > Audio: enable "Automatically join computer audio when joining a meeting"
- [ ] In Zoom Settings > Video: disable "Always show video preview dialog when joining a video meeting" (prevents dialogs from blocking automatic joins)
- [ ] In Zoom Settings > General: disable "Ask me to confirm when I leave a meeting"
- [ ] Join a test Zoom meeting manually once to dismiss any first-run prompts (e.g. browser "Open Zoom Meetings?" dialogs)
- [ ] Note: setting names vary slightly between Zoom versions; find the closest equivalent if wording differs

## 3. Windows power & session

- [ ] Enable wake timers: Control Panel > Power Options > Change plan settings > Change advanced power settings > Sleep > Allow wake timers > set to "Enable" (do this for both battery and plugged-in modes)
- [ ] Set lid close action to "Do nothing" when plugged in: Power Options > Choose what closing the lid does
- [ ] Leave the laptop plugged in and logged in (screen can be locked) during scheduled webinars
- [ ] Warning: some laptops with "Modern Standby" may ignore wake timers; Phase 2 testing will verify wake works on this machine—fallback is disabling sleep on meeting days if needed

## 4. WhatsApp Web fallback profile

- [ ] Run `python setup/setup_whatsapp.py` from the project root
- [ ] Scan the QR code with your phone: WhatsApp > Settings > Linked devices > Link a device
- [ ] Wait for the "Done" confirmation message
- [ ] Note the exact display name of the WhatsApp group that receives replacement Zoom links (you will provide this to the CLI later)

## 5. Final confirmation

- [ ] Zoom is signed in with all settings configured
- [ ] Wake timers are enabled for both battery and plugged-in
- [ ] Lid close action is set to "Do nothing" when plugged in
- [ ] WhatsApp Web profile is set up and group name is noted
- [ ] Test meeting has been joined manually at least once
