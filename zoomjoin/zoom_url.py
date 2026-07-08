"""Parse Zoom meeting/webinar URLs and convert them to zoommtg:// deep links.

Accepted shapes:
  - https://<sub>.zoom.us/j/<id>?pwd=...          (meeting)
  - https://<sub>.zoom.us/w/<id>?pwd=...          (webinar)
  - https://<sub>.zoom.us/s/<id>?pwd=...          (short/vanity)
  - Any of the above with a registration token: ...&tk=...
  - zoommtg://zoom.us/join?action=join&confno=...&pwd=...&tk=...  (passthrough)

Vanity subdomains (e.g. https://mycompany.zoom.us/j/123...) are supported —
we only require the host to end with ".zoom.us" or equal "zoom.us".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse

# Path shapes that carry a numeric meeting/webinar id as their last segment.
_ID_PATH_RE = re.compile(r"^/(?:j|w|s|my)/([0-9]{3,})(?:/.*)?$")

# Zoom hosts we accept for https(s) URLs: zoom.us, <vanity>.zoom.us, and the
# regional zoom.com.cn / us04web.zoom.us style hosts (anything ending in the
# recognized zoom domains).
_ZOOM_HOST_RE = re.compile(r"(^|\.)zoom\.us$|(^|\.)zoom\.com$", re.IGNORECASE)


@dataclass(frozen=True)
class ZoomLink:
    confno: str | None
    pwd: str | None
    tk: str | None
    original_url: str


def _is_zoom_host(host: str) -> bool:
    return bool(_ZOOM_HOST_RE.search(host or ""))


def parse(url: str) -> ZoomLink:
    """Parse a Zoom URL (https or zoommtg) into a ZoomLink.

    Raises ValueError if the input is not a recognizable Zoom URL.
    """
    if not url or not isinstance(url, str):
        raise ValueError(f"empty or non-string url: {url!r}")

    url = url.strip()
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme == "zoommtg":
        # Passthrough scheme: still try to extract confno/pwd/tk from the
        # query string so callers can reason about the meeting id, but the
        # url itself is already launchable as-is.
        qs = parse_qs(parsed.query)
        confno = qs.get("confno", [None])[0]
        pwd = qs.get("pwd", [None])[0]
        tk = qs.get("tk", [None])[0]
        return ZoomLink(
            confno=confno,
            pwd=unquote(pwd) if pwd else None,
            tk=unquote(tk) if tk else None,
            original_url=url,
        )

    if scheme not in ("http", "https"):
        raise ValueError(f"unrecognized url scheme: {scheme!r} in {url!r}")

    if not _is_zoom_host(parsed.hostname or ""):
        raise ValueError(f"not a zoom.us/zoom.com host: {parsed.hostname!r}")

    match = _ID_PATH_RE.match(parsed.path or "")
    confno = match.group(1) if match else None

    qs = parse_qs(parsed.query)
    pwd_raw = qs.get("pwd", [None])[0]
    tk_raw = qs.get("tk", [None])[0]
    pwd = unquote(pwd_raw) if pwd_raw else None
    tk = unquote(tk_raw) if tk_raw else None

    if confno is None and pwd is None and tk is None:
        raise ValueError(f"no meeting id, password, or token found in: {url!r}")

    return ZoomLink(confno=confno, pwd=pwd, tk=tk, original_url=url)


def to_protocol_url(link: ZoomLink) -> str | None:
    """Build a zoommtg:// deep link from a ZoomLink.

    Returns None if no numeric meeting id could be extracted — callers
    should fall back to opening `link.original_url` (the https link) in
    that case, letting Zoom's web page hand off to the client.
    """
    if not link.confno or not link.confno.isdigit():
        return None

    parts = [f"zoommtg://zoom.us/join?action=join&confno={link.confno}"]
    if link.pwd:
        parts.append(f"&pwd={quote(link.pwd, safe='')}")
    if link.tk:
        parts.append(f"&tk={quote(link.tk, safe='')}")
    return "".join(parts)
