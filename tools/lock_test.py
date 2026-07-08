"""Locked-session validation for verify.is_in_meeting().

Run from anywhere:  python "<repo_root>\\tools\\lock_test.py"

Logs one line every 2 s for 90 s to locktest.txt next to this repo's root,
flushing each line so the output survives even if the run is interrupted.
Join the Zoom test meeting first, start this, then lock the screen (Win+L)
for at least 30 s.
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from zoomjoin import verify  # noqa: E402

OUT = REPO_ROOT / "locktest.txt"


def main() -> None:
    print(f"logging to {OUT} for 90 s — lock the screen now (Win+L)")
    with OUT.open("w", encoding="utf-8") as f:
        for _ in range(45):
            line = f"{time.strftime('%H:%M:%S')} in_meeting={verify.is_in_meeting()}"
            f.write(line + "\n")
            f.flush()
            print(line)
            time.sleep(2)
    print("done")


if __name__ == "__main__":
    main()
