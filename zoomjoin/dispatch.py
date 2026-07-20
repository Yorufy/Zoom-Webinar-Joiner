"""Parse a free-text remote ("Claude Dispatch") message into a schedulable meeting.

A user texts a phone message like::

    join https://zoom.us/j/123?pwd=Ab9 at tomorrow 2pm group "Team Sync"

to a headless Claude Dispatch session running on the laptop. This module turns
that raw text into a `ParsedDispatch` (url, when, group, duration_min) that
`cli.cmd_dispatch` hands off to the existing `cmd_add` flow — this module does
*not* write meetings.json or create scheduled tasks itself.

`now` is always injected by the caller (never call `datetime.now()` in here)
so parsing stays deterministic and testable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

from . import zoom_url

logger = logging.getLogger(__name__)


class DispatchParseError(ValueError):
    """Raised when a dispatch message can't be parsed into a schedulable meeting."""


@dataclass(frozen=True)
class ParsedDispatch:
    url: str
    when: datetime  # resolved local start time
    group: str | None
    duration_min: int | None


USAGE_HINT = """\
examples:
  zoomjoin dispatch "join https://zoom.us/j/123?pwd=Ab9 at tomorrow 2pm"
  zoomjoin dispatch "join https://zoom.us/j/123?pwd=Ab9 at 2026-12-31 09:00 group \\"Team Sync\\""
  zoomjoin dispatch "join https://zoom.us/j/123?pwd=Ab9 in 30 minutes for 45 min"
"""

# -- URL extraction ----------------------------------------------------------

_URL_TOKEN_RE = re.compile(r"zoommtg://\S+|https?://\S+", re.IGNORECASE)
_ZOOM_HOST_RE = re.compile(r"(^|\.)zoom\.us$|(^|\.)zoom\.com$", re.IGNORECASE)


def _extract_url(text: str) -> str:
    for m in _URL_TOKEN_RE.finditer(text):
        token = m.group(0)
        if not token.lower().startswith("zoommtg://"):
            host = urlparse(token).hostname or ""
            if not _ZOOM_HOST_RE.search(host):
                continue
        try:
            zoom_url.parse(token)
        except ValueError:
            continue
        return token
    raise DispatchParseError("no valid Zoom link found")


# -- Group extraction ---------------------------------------------------------

_QUOTE_OPEN = "\"'“‘"
_QUOTE_CLOSE = "\"'”’"
_GROUP_QUOTED_RE = re.compile(
    r"(?:--)?group\s+[" + _QUOTE_OPEN + r"]([^" + _QUOTE_CLOSE + r"]+)[" + _QUOTE_CLOSE + r"]",
    re.IGNORECASE,
)
_GROUP_UNQUOTED_RE = re.compile(r"\bgroup[=:]\s*(\S+)", re.IGNORECASE)


def _extract_group(text: str) -> str | None:
    m = _GROUP_QUOTED_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _GROUP_UNQUOTED_RE.search(text)
    if m:
        return m.group(1).strip().strip("\"'")
    return None


# -- Duration extraction -------------------------------------------------------

_DURATION_FOR_RE = re.compile(r"\bfor\s+(\d+)\s*min(?:ute)?s?\b", re.IGNORECASE)
_DURATION_KEYWORD_RE = re.compile(r"\bduration[=:\s]+(\d+)\b", re.IGNORECASE)
_DURATION_BARE_RE = re.compile(r"(?<!in )\b(\d+)\s*minutes\b", re.IGNORECASE)


def _extract_duration(text: str) -> int | None:
    for rx in (_DURATION_FOR_RE, _DURATION_KEYWORD_RE, _DURATION_BARE_RE):
        m = rx.search(text)
        if m:
            return int(m.group(1))
    return None


# -- Time extraction ------------------------------------------------------------

_TIME_STOP_RE = re.compile(r"\b(group|for|duration)\b", re.IGNORECASE)


def _extract_time_text(text: str) -> str:
    """Pull the time expression out of a (url-stripped) dispatch message."""
    m = re.search(r"\bat\s+(.+)", text, re.IGNORECASE)
    if m:
        candidate = m.group(1)
        stop = _TIME_STOP_RE.search(candidate)
        if stop:
            candidate = candidate[: stop.start()]
        return candidate.strip().strip("\"'")

    # No "at" keyword present: locate one of the recognized time forms
    # directly within the (url-stripped) text, in the same priority order
    # as parse_when.
    for rx in (_ISO_DATETIME_RE, _DATE_12H_RE, _TODAY_TOMORROW_RE, _RELATIVE_RE):
        m = rx.search(text)
        if m:
            candidate = m.group(0)
            stop = _TIME_STOP_RE.search(candidate)
            if stop:
                candidate = candidate[: stop.start()]
            return candidate.strip().strip("\"'")

    # Last resort: a bare clock time somewhere in the text.
    m = re.search(r"\d{1,2}:\d{2}|\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", text, re.IGNORECASE)
    if m:
        return m.group(0)

    return text.strip()


_CLOCK_24H_RE = re.compile(r"(\d{1,2}):(\d{2})")
_CLOCK_12H_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", re.IGNORECASE)


def _parse_clock(s: str) -> tuple[int, int]:
    """Parse a bare time-of-day (24h `HH:MM` or 12h `h[:mm]am/pm`) into (hour, minute)."""
    s = s.strip()

    m = _CLOCK_24H_RE.fullmatch(s)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise DispatchParseError(f"invalid time: {s!r}")
        return hour, minute

    m = _CLOCK_12H_RE.fullmatch(s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3).lower()
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            raise DispatchParseError(f"invalid time: {s!r}")
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        return hour, minute

    raise DispatchParseError(f"unrecognized time: {s!r}")


_ISO_DATETIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?"
)
_DATE_12H_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    re.IGNORECASE,
)
_TODAY_TOMORROW_RE = re.compile(r"(today|tomorrow)\s+(.+)", re.IGNORECASE)
_RELATIVE_RE = re.compile(
    r"in\s+(\d+)\s*(minutes?|min|hours?|hrs?|hr)", re.IGNORECASE
)


def parse_when(text: str, *, now: datetime) -> datetime:
    """Parse a time expression (form 1-5, see module spec) into a datetime."""
    s = text.strip()
    if s.lower().startswith("at "):
        s = s[3:].strip()
    if not s:
        raise DispatchParseError("no time expression found")

    # 1. YYYY-MM-DD HH:MM[:SS] / YYYY-MM-DDTHH:MM[:SS]
    m = _ISO_DATETIME_RE.fullmatch(s)
    if m:
        year, month, day, hour, minute = (int(m.group(i)) for i in range(1, 6))
        second = int(m.group(6)) if m.group(6) else 0
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError as exc:
            raise DispatchParseError(f"invalid date/time: {text!r}") from exc

    # 2. YYYY-MM-DD h[:mm] am/pm
    m = _DATE_12H_RE.fullmatch(s)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour, minute = _parse_clock(m.group(4))
        try:
            return datetime(year, month, day, hour, minute)
        except ValueError as exc:
            raise DispatchParseError(f"invalid date/time: {text!r}") from exc

    # 3. today/tomorrow <time>
    m = _TODAY_TOMORROW_RE.fullmatch(s)
    if m:
        day_word = m.group(1).lower()
        hour, minute = _parse_clock(m.group(2))
        base = now.date() if day_word == "today" else now.date() + timedelta(days=1)
        return datetime(base.year, base.month, base.day, hour, minute)

    # 4. in <N> minute(s)/min / hour(s)/hr(s)
    m = _RELATIVE_RE.fullmatch(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("h"):
            return now + timedelta(hours=n)
        return now + timedelta(minutes=n)

    # 5. bare time -> today if still in the future, else tomorrow
    try:
        hour, minute = _parse_clock(s)
    except DispatchParseError:
        raise DispatchParseError(f"could not parse time expression: {text!r}") from None
    candidate = datetime(now.year, now.month, now.day, hour, minute)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


# -- Top-level entry point ------------------------------------------------------


def parse_dispatch(text: str, *, now: datetime) -> ParsedDispatch:
    """Parse a raw remote dispatch message into a `ParsedDispatch`."""
    url = _extract_url(text)
    remainder = text.replace(url, " ", 1)
    group = _extract_group(remainder)
    duration_min = _extract_duration(remainder)
    time_text = _extract_time_text(remainder)
    when = parse_when(time_text, now=now)
    logger.debug(
        "parsed dispatch: url=%s when=%s group=%r duration_min=%r",
        url,
        when,
        group,
        duration_min,
    )
    return ParsedDispatch(url=url, when=when, group=group, duration_min=duration_min)
