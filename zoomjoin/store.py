"""meetings.json read/write — a tiny atomic JSON-backed store.

Layout on disk: {"meetings": {<id>: {...record...}}}

Record fields: id, url, at (ISO 8601 local time string), group
(WhatsApp group name, may be None), duration_min (may be None),
created (ISO timestamp string), status ("scheduled" initially, later
"joined" / "join_failed").
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_STORE_PATH = Path(__file__).resolve().parent.parent / "meetings.json"


class Store:
    """A meetings.json-backed store. Point `path` at a temp file in tests."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DEFAULT_STORE_PATH

    # -- low-level load/save -------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"meetings": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"meetings": {}}
        if not isinstance(data, dict) or "meetings" not in data:
            return {"meetings": {}}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".meetings-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # -- id generation ---------------------------------------------------------

    def _make_id(self, at: str, existing: dict[str, Any]) -> str:
        try:
            dt = datetime.fromisoformat(at)
            base = f"m-{dt.strftime('%Y%m%d-%H%M')}"
        except ValueError:
            base = "m-" + "".join(c for c in at if c.isalnum())
        if base not in existing:
            return base
        n = 2
        while f"{base}-{n}" in existing:
            n += 1
        return f"{base}-{n}"

    # -- CRUD --------------------------------------------------------------

    def add(self, record: dict[str, Any]) -> str:
        """Add a record, assigning an id if not already present. Returns id."""
        data = self._load()
        meetings = data["meetings"]
        rid = record.get("id") or self._make_id(record["at"], meetings)
        record = dict(record)
        record["id"] = rid
        meetings[rid] = record
        self._save(data)
        return rid

    def get(self, meeting_id: str) -> dict[str, Any] | None:
        data = self._load()
        return data["meetings"].get(meeting_id)

    def remove(self, meeting_id: str) -> bool:
        data = self._load()
        if meeting_id in data["meetings"]:
            del data["meetings"][meeting_id]
            self._save(data)
            return True
        return False

    def list_all(self) -> list[dict[str, Any]]:
        data = self._load()
        return list(data["meetings"].values())

    def update(self, meeting_id: str, **fields: Any) -> dict[str, Any] | None:
        data = self._load()
        meetings = data["meetings"]
        if meeting_id not in meetings:
            return None
        meetings[meeting_id].update(fields)
        self._save(data)
        return meetings[meeting_id]
