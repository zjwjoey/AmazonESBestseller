"""JSON/JSONL persistence boundary for Production V1 history.

The workflow deliberately depends on this small interface instead of a
database.  Product identity remains ASIN-based, ranking snapshots are append
only, and the Spanish Master is the only mutable current projection.  This
makes an initial/incremental run portable and keeps operator-owned notes out
of collector state.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Mapping, Sequence


class HistoryRepository(ABC):
    """Business-facing history interface; implementations need no database."""

    @abstractmethod
    def latest_snapshot(self) -> Mapping[str, Any] | None: ...

    @abstractmethod
    def append_snapshot(self, snapshot: Mapping[str, Any]) -> None: ...

    @abstractmethod
    def load_details(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_details(self, records: Sequence[Mapping[str, Any]]) -> None: ...

    @abstractmethod
    def load_master(self) -> Mapping[str, Any] | None: ...

    @abstractmethod
    def save_master(self, master: Mapping[str, Any]) -> None: ...


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
                         encoding="utf-8")
    os.replace(temporary, path)


class JsonHistoryRepository(HistoryRepository):
    """Filesystem implementation with immutable JSONL snapshot receipts."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def snapshots_path(self) -> Path:
        return self.root / "ranking_snapshots.jsonl"

    @property
    def details_path(self) -> Path:
        return self.root / "details.json"

    @property
    def master_path(self) -> Path:
        return self.root / "spanish_master.json"

    def _load_object(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("HISTORY_JSON_INVALID:%s" % path) from exc
        if not isinstance(value, dict):
            raise ValueError("HISTORY_JSON_OBJECT_REQUIRED:%s" % path)
        return value

    def latest_snapshot(self) -> Mapping[str, Any] | None:
        if not self.snapshots_path.exists():
            return None
        try:
            lines = self.snapshots_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError("HISTORY_SNAPSHOT_READ_FAILED:%s" % self.snapshots_path) from exc
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ValueError("HISTORY_JSONL_INVALID:%s" % self.snapshots_path) from exc
            if isinstance(item, dict):
                return item
        return None

    def append_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        item = dict(snapshot)
        snapshot_id = str(item.get("snapshot_id") or "")
        if not snapshot_id:
            raise ValueError("HISTORY_SNAPSHOT_ID_REQUIRED")
        existing = self.latest_snapshot()
        if existing and str(existing.get("snapshot_id") or "") == snapshot_id:
            if existing != item:
                raise ValueError("HISTORY_SNAPSHOT_IMMUTABLE_CONFLICT:%s" % snapshot_id)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with self.snapshots_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    def load_details(self) -> list[dict[str, Any]]:
        if not self.details_path.exists():
            return []
        try:
            value = json.loads(self.details_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("HISTORY_DETAILS_INVALID:%s" % self.details_path) from exc
        if not isinstance(value, list):
            raise ValueError("HISTORY_DETAILS_LIST_REQUIRED:%s" % self.details_path)
        return [dict(row) for row in value if isinstance(row, Mapping)]

    def save_details(self, records: Sequence[Mapping[str, Any]]) -> None:
        """Merge by ASIN, retaining records not touched by an incremental run."""
        merged = {str(row.get("asin") or "").upper(): dict(row)
                  for row in self.load_details() if str(row.get("asin") or "").strip()}
        for row in records:
            asin = str(row.get("asin") or "").upper()
            if asin:
                merged[asin] = dict(row)
        _atomic_json(self.details_path, [merged[asin] for asin in sorted(merged)])

    def load_master(self) -> Mapping[str, Any] | None:
        return self._load_object(self.master_path)

    def save_master(self, master: Mapping[str, Any]) -> None:
        value = dict(master)
        if not isinstance(value.get("records"), list):
            raise ValueError("HISTORY_MASTER_RECORDS_REQUIRED")
        _atomic_json(self.master_path, value)


__all__ = ["HistoryRepository", "JsonHistoryRepository"]
