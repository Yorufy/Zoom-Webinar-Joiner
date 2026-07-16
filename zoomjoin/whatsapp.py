"""WhatsApp Web fallback: scan a named group for a newer Zoom link when the
scheduled direct join fails, so the caller can retry with a recovered link.

Split I/O (Playwright, driving a real headed Chromium session over the user's
persistent WhatsApp Web profile) from pure logic (link extraction, timestamp
parsing, replacement selection) so the logic is unit-testable without a
browser. Playwright is imported lazily inside find_replacement_link() so this
module (and anything importing it) stays importable when Playwright is not
installed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import zoom_url

logger = logging.getLogger(__name__)

# WhatsApp Web selectors. THESE DRIFT — WhatsApp Web changes its DOM/markup
# periodically. If the scan stops finding chats/messages, re-verify these
# against a live logged-in session first. Kept in one block on purpose.
# Verified against live WhatsApp Web on 2026-07-08: the search box is an <input>
# (older builds used a contenteditable <div>), and messages are anchored on the
# data-pre-plain-text attribute — message class names such as .message-in drift
# and are unreliable.
SELECTORS = {
    "logged_in": "#side",                            # chat-list sidebar; present only when a session is active
    "search_box": "input[aria-label*='Search' i]",   # chat search box (an <input> in current WhatsApp Web)
    "chat_row": '#side span[title="{group}"]',       # chat-list row whose title matches the group name exactly (format with group)
    "message": "[data-pre-plain-text]",              # each text message; data-pre-plain-text="[H:MM AM, M/D/YYYY] Name: ", body = inner text
}

PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "zoomjoin" / "wa-profile"
WA_URL = "https://web.whatsapp.com"
DEFAULT_MAX_MESSAGES = 20
NAV_TIMEOUT_MS = 60_000
LOGIN_CHECK_TIMEOUT_MS = 15_000

_LINK_RE = re.compile(r"(https?://[^\s]+|zoommtg://[^\s]+)", re.IGNORECASE)
_TRAILING_PUNCT = ")]>,.'\""


def extract_zoom_links(text: str) -> list[str]:
    """Return every substring of `text` that is a valid Zoom link, in order.

    Validates each candidate with zoom_url.parse (ValueError => not a zoom
    link). Strips common trailing punctuation ()<>,.'" before validating.
    """
    links: list[str] = []
    for match in _LINK_RE.finditer(text or ""):
        candidate = match.group(0).rstrip(_TRAILING_PUNCT)
        try:
            zoom_url.parse(candidate)
        except ValueError:
            continue
        links.append(candidate)
    return links


@dataclass
class Message:
    text: str
    ts: datetime | None  # parsed from data-pre-plain-text; None if unknown


def _normalize_link(link: str) -> str:
    return link.strip().lower().rstrip("/")


def select_replacement_link(
    messages: list[Message],
    *,
    failed_url: str | None,
    since: datetime | None,
) -> str | None:
    """Pick the NEWEST Zoom link across `messages` that (a) differs from
    `failed_url` (compare case-insensitively, ignoring a trailing slash), and
    (b) if the message has a ts AND `since` is given, ts >= since. Messages
    with ts=None are always eligible (recency unknown => don't exclude).

    `messages` are in chat order (oldest first). Selection rule: consider
    every (message index, link) pair that survives the filters above; the
    winner is the one with the greatest ts (None treated as "unknown, not
    excluded, but doesn't win a tie against a known ts"); ties (equal ts, or
    both None) are broken by list order, latest (highest index) wins. This
    keeps the rule simple and deterministic: newest-known-timestamp first,
    falling back to "most recently seen in the chat" otherwise.
    """
    normalized_failed = _normalize_link(failed_url) if failed_url else None

    best_link: str | None = None
    best_key: tuple[int, datetime | None, int] | None = None

    for idx, msg in enumerate(messages):
        if msg.ts is not None and since is not None and msg.ts < since:
            continue
        for link in extract_zoom_links(msg.text):
            if normalized_failed is not None and _normalize_link(link) == normalized_failed:
                continue
            # Sort key: known-ts beats unknown-ts, then by ts value, then by
            # position in the message list (later = newer).
            has_ts = 1 if msg.ts is not None else 0
            key = (has_ts, msg.ts, idx)
            if best_key is None or key > best_key:
                best_key = key
                best_link = link

    return best_link


_TS_FORMATS = ("%I:%M %p, %m/%d/%Y", "%H:%M, %d/%m/%Y")


def parse_pre_plain_text(value: str) -> datetime | None:
    """Parse WhatsApp's data-pre-plain-text, e.g. '[10:30 AM, 7/8/2026] Alice: '.

    Return a naive datetime or None if it can't be parsed (locale variance is
    expected — never raise).
    """
    if not value:
        return None

    match = re.search(r"\[(.+?)\]", value)
    if not match:
        return None
    inner = match.group(1)

    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(inner, fmt)
        except ValueError:
            continue
    return None


@dataclass
class WhatsAppResult:
    status: str  # "found" | "no_link" | "group_not_found" | "logged_out" | "unavailable"
    link: str | None
    detail: str


def find_replacement_link(
    group: str,
    *,
    failed_url: str | None = None,
    since: datetime | None = None,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    headless: bool = False,
    profile_dir: Path | None = None,
) -> WhatsAppResult:
    """Open WhatsApp Web with the persistent profile, open the group by exact
    name, read the last `max_messages` messages, and return the newest usable
    Zoom link. Best-effort; NEVER raises.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright is not installed; WhatsApp fallback unavailable")
        return WhatsAppResult("unavailable", None, "playwright not installed")

    ctx = None
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir or PROFILE_DIR), headless=headless
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(WA_URL, timeout=NAV_TIMEOUT_MS)

                try:
                    page.wait_for_selector(SELECTORS["logged_in"], timeout=LOGIN_CHECK_TIMEOUT_MS)
                except Exception:  # noqa: BLE001 — timeout or any wait failure
                    logger.error(
                        "WhatsApp Web appears logged out (QR screen) — run setup/setup_whatsapp.py"
                    )
                    return WhatsAppResult("logged_out", None, "logged out / QR screen shown")

                try:
                    search_box = page.locator(SELECTORS["search_box"]).first
                    search_box.click()
                    search_box.fill(group)
                    page.wait_for_timeout(1_500)

                    escaped_group = group.replace('"', '\\"')
                    chat_selector = SELECTORS["chat_row"].format(group=escaped_group)
                    chat_row = page.locator(chat_selector).first
                    chat_row.wait_for(timeout=8_000)
                    chat_row.click()
                except Exception:  # noqa: BLE001
                    logger.warning("could not find WhatsApp group %r", group, exc_info=True)
                    return WhatsAppResult(
                        "group_not_found", None, f"group '{group}' not found"
                    )

                messages: list[Message] = []
                try:
                    page.wait_for_timeout(1_500)
                    # Each text message is a node carrying data-pre-plain-text
                    # (timestamp + sender) with the message body as its inner text.
                    msg_elements = page.locator(SELECTORS["message"]).all()
                    for el in msg_elements[-max_messages:]:
                        try:
                            text = el.inner_text()
                        except Exception:  # noqa: BLE001
                            continue
                        ts: datetime | None = None
                        try:
                            pre_plain = el.get_attribute("data-pre-plain-text")
                            ts = parse_pre_plain_text(pre_plain or "")
                        except Exception:  # noqa: BLE001
                            ts = None
                        messages.append(Message(text=text, ts=ts))
                except Exception:  # noqa: BLE001
                    logger.warning("failed reading messages from group %r", group, exc_info=True)

                link = select_replacement_link(messages, failed_url=failed_url, since=since)
                if link:
                    return WhatsAppResult(
                        "found", link, f"recovered link from group '{group}'"
                    )
                return WhatsAppResult(
                    "no_link", None, f"no newer zoom link in last {max_messages} messages"
                )
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    logger.debug("error closing WhatsApp browser context", exc_info=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("WhatsApp fallback failed unexpectedly", exc_info=True)
        return WhatsAppResult("unavailable", None, str(exc))
