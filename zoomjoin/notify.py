"""Best-effort notifications: a Windows toast (always attempted) plus an
optional ntfy.sh push. Every public function swallows its own errors and
never raises — a failed notification must never break a join/monitor run.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.json"

_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:ZJ_TOAST_TITLE)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:ZJ_TOAST_MESSAGE)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Zoom Joiner').Show($toast)
"""


def resolve_ntfy_topic() -> str | None:
    """Return the ntfy.sh topic to push to, or None if not configured.

    Precedence: ZOOMJOIN_NTFY_TOPIC env var (if set and non-empty), else
    "ntfy_topic" in config.json at the repo root, else None. A missing or
    malformed config.json is tolerated.
    """
    env_topic = os.environ.get("ZOOMJOIN_NTFY_TOPIC")
    if env_topic:
        return env_topic

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        topic = data.get("ntfy_topic")
        if topic:
            return topic
    except Exception:  # noqa: BLE001
        logger.debug("resolve_ntfy_topic: no usable config.json at %s", CONFIG_PATH, exc_info=True)

    return None


def _toast(title: str, message: str) -> None:
    try:
        env = {**os.environ, "ZJ_TOAST_TITLE": title, "ZJ_TOAST_MESSAGE": message}
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_SCRIPT],
            env=env,
            timeout=10,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001
        logger.warning("toast notification failed", exc_info=True)


def _ntfy(topic: str, title: str, message: str) -> None:
    try:
        ascii_title = title.encode("ascii", errors="ignore").decode("ascii")
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": ascii_title},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:  # noqa: BLE001
        logger.warning("ntfy notification failed", exc_info=True)


def notify(title: str, message: str) -> None:
    """Fire a Windows toast (always) and an ntfy push (if configured).

    Best-effort: never raises.
    """
    _toast(title, message)

    topic = resolve_ntfy_topic()
    if topic:
        _ntfy(topic, title, message)
