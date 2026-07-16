"""One-time WhatsApp Web login for the Zoom joiner's fallback module.

Opens a visible Chromium window using the same persistent browser profile the
joiner will use at runtime. On first run, scan the QR code with your phone
(WhatsApp > Settings > Linked devices > Link a device). The session persists
in the profile, so this only needs re-running if WhatsApp logs the device out.

Usage:  python setup/setup_whatsapp.py
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(os.environ["LOCALAPPDATA"]) / "zoomjoin" / "wa-profile"

# The chat-list sidebar only exists once a linked session is active.
LOGGED_IN_SELECTOR = "#side"
LOGIN_TIMEOUT_MS = 5 * 60 * 1000


def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://web.whatsapp.com")

        try:
            page.wait_for_selector(LOGGED_IN_SELECTOR, timeout=10_000)
            print("Already logged in - profile is ready. Closing in 5s.")
            page.wait_for_timeout(5_000)
            ctx.close()
            return 0
        except Exception:
            pass

        print("Scan the QR code with your phone (WhatsApp > Linked devices).")
        print("Waiting up to 5 minutes for login...")
        try:
            page.wait_for_selector(LOGGED_IN_SELECTOR, timeout=LOGIN_TIMEOUT_MS)
        except Exception:
            print("ERROR: login not detected within 5 minutes. Re-run this script.")
            ctx.close()
            return 1

        # Give WhatsApp Web a moment to finish syncing before the profile is saved.
        print("Logged in. Letting the session settle for 15s before closing...")
        page.wait_for_timeout(15_000)
        ctx.close()

    print(f"Done. Profile saved at: {PROFILE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
