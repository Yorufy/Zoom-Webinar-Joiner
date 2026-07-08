"""Discovery CLI: dump/diff Zoom process window signatures over time.

Run this before, during, and after a live Zoom meeting to confirm the
in-meeting window class/process signature encoded in zoomjoin/verify.py.
No input injection, no screenshots — window/process enumeration only, so
this is safe to run on a locked workstation.

Usage:
    python tools/discover_zoom_windows.py            # loop every 2s
    python tools/discover_zoom_windows.py --once      # single snapshot
    python tools/discover_zoom_windows.py --interval 5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin import verify  # noqa: E402


def _key(win: dict) -> tuple:
    return (win["pid"], win["class"], win["title"])


def _print_snapshot(snap: list[dict]) -> None:
    if not snap:
        print("(no Zoom windows found)")
        return
    for win in snap:
        print(
            f"pid={win['pid']:>6} exe={win['exe']:<20} "
            f"class={win['class']:<30} visible={win['visible']!s:<5} "
            f"title={win['title']!r}"
        )


def _diff(prev: dict[tuple, dict], curr: dict[tuple, dict]) -> None:
    prev_keys = set(prev)
    curr_keys = set(curr)

    for key in curr_keys - prev_keys:
        print(f"[+] {curr[key]}")
    for key in prev_keys - curr_keys:
        print(f"[-] {prev[key]}")
    for key in prev_keys & curr_keys:
        if prev[key] != curr[key]:
            print(f"[~] {prev[key]} -> {curr[key]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="print a single snapshot and exit")
    parser.add_argument("--interval", type=float, default=2.0, help="poll interval in seconds (default: 2)")
    args = parser.parse_args()

    if args.once:
        snap = verify.snapshot()
        print(f"--- snapshot ({len(snap)} window(s)) ---")
        _print_snapshot(snap)
        return 0

    print(f"Polling Zoom windows every {args.interval}s. Ctrl+C to stop.")
    prev: dict[tuple, dict] = {}
    try:
        while True:
            snap = verify.snapshot()
            curr = {_key(w): w for w in snap}
            if not prev:
                print(f"--- initial snapshot ({len(snap)} window(s)) ---")
                _print_snapshot(snap)
            else:
                _diff(prev, curr)
            prev = curr
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
