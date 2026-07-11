"""Post-join monitoring: detect webinar end (vs. an early Zoom drop) and
best-effort clean up leftover meeting windows.

Dependency-injected so it is unit testable without real Win32 or real
sleeping (see tests/test_monitor.py).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import verify

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 30
CRASH_THRESHOLD_S = 600  # 10 min; drop before this = crash, not a normal end
CONFIRM_DELAY_S = 5  # debounce a single transient enumeration miss


@dataclass
class MonitorResult:
    outcome: str  # "ended" | "crashed_early"
    duration_s: float
    detail: str


def monitor(
    *,
    poll_interval_s: float = POLL_INTERVAL_S,
    crash_threshold_s: float = CRASH_THRESHOLD_S,
    confirm_delay_s: float = CONFIRM_DELAY_S,
    in_meeting_fn=verify.is_in_meeting,
    cleanup_fn=verify.close_meeting_windows,
    sleep_fn=time.sleep,
    clock=time.monotonic,
) -> MonitorResult:
    """Block until the webinar ends or Zoom drops, then report the outcome.

    Loops sleep_fn(poll_interval_s) -> in_meeting_fn() until in_meeting_fn()
    returns False. A single False is debounced with one more check after
    confirm_delay_s (to ignore a transient enumeration miss) before treating
    the meeting as confirmed-gone. On confirmed-gone, cleanup_fn() is called
    best-effort and duration is compared to crash_threshold_s to decide
    between "ended" (host ended it) and "crashed_early" (Zoom dropped).
    """
    start = clock()
    logger.info("monitoring started")

    while True:
        sleep_fn(poll_interval_s)

        if in_meeting_fn():
            continue

        logger.info("in-meeting check returned False; debouncing")
        sleep_fn(confirm_delay_s)

        if in_meeting_fn():
            logger.info("transient miss — still in meeting, resuming poll")
            continue

        duration = clock() - start
        logger.info("meeting confirmed gone after %.1fs", duration)

        try:
            cleanup_fn()
        except Exception:  # noqa: BLE001
            logger.warning("cleanup_fn raised during monitor cleanup", exc_info=True)

        if duration >= crash_threshold_s:
            detail = f"meeting ended normally after {duration:.1f}s"
            logger.info(detail)
            return MonitorResult(outcome="ended", duration_s=duration, detail=detail)

        detail = f"Zoom dropped after {duration:.1f}s (< {crash_threshold_s}s threshold)"
        logger.warning(detail)
        return MonitorResult(outcome="crashed_early", duration_s=duration, detail=detail)
