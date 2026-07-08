"""Launch a Zoom join via the desktop client and verify it succeeded."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from . import verify, zoom_url

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 2


@dataclass
class JoinResult:
    joined: bool
    attempts: int
    elapsed_s: float
    detail: str


def _launch(url: str) -> None:
    logger.info("launching url via os.startfile: %s", url)
    os.startfile(url)  # type: ignore[attr-defined]  # Windows-only


def _launch_url_for(link: zoom_url.ZoomLink) -> str:
    protocol_url = zoom_url.to_protocol_url(link)
    if protocol_url:
        return protocol_url
    logger.info("no numeric meeting id extracted; falling back to https url")
    return link.original_url


def join(url: str, timeout_s: int = 90) -> JoinResult:
    """Join a Zoom meeting/webinar and verify the client actually joined.

    Steps:
      1. Parse `url`, build the zoommtg:// protocol url (falls back to the
         original https url if no numeric meeting id could be extracted).
      2. Launch it via os.startfile().
      3. Poll verify.is_in_meeting() every POLL_INTERVAL_S seconds until
         `timeout_s` elapses.
      4. On timeout, retry the launch once and poll for another
         `timeout_s` seconds.

    Returns a JoinResult with attempts (1 or 2), total elapsed_s, and a
    human-readable detail string.
    """
    start = time.monotonic()

    try:
        link = zoom_url.parse(url)
    except ValueError as exc:
        logger.error("failed to parse url %r: %s", url, exc)
        return JoinResult(
            joined=False,
            attempts=0,
            elapsed_s=time.monotonic() - start,
            detail=f"parse error: {exc}",
        )

    launch_url = _launch_url_for(link)

    for attempt in (1, 2):
        logger.info("join attempt %d/2", attempt)
        try:
            _launch(launch_url)
        except OSError as exc:
            logger.error("os.startfile failed on attempt %d: %s", attempt, exc)
            if attempt == 2:
                return JoinResult(
                    joined=False,
                    attempts=attempt,
                    elapsed_s=time.monotonic() - start,
                    detail=f"launch error: {exc}",
                )
            continue

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if verify.is_in_meeting():
                elapsed = time.monotonic() - start
                logger.info("joined after attempt %d, elapsed=%.1fs", attempt, elapsed)
                return JoinResult(
                    joined=True,
                    attempts=attempt,
                    elapsed_s=elapsed,
                    detail=f"joined on attempt {attempt}",
                )
            if verify.has_join_failed_dialog():
                logger.warning("attempt %d: Zoom join-failed dialog detected", attempt)
                break
            time.sleep(POLL_INTERVAL_S)
        else:
            logger.warning("attempt %d timed out after %ds without detecting join", attempt, timeout_s)

    elapsed = time.monotonic() - start
    logger.error("join failed after 2 attempts, elapsed=%.1fs", elapsed)
    return JoinResult(
        joined=False,
        attempts=2,
        elapsed_s=elapsed,
        detail="timed out after 2 attempts",
    )
