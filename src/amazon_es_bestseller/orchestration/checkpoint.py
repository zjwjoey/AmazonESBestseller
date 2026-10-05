"""Task-specific adapters around the shared versioned checkpoint store."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..runtime_state import VersionedCheckpointStore, atomic_write_json


def write_json_atomic(path: Path, value: object) -> None:
    """Persist derived task output with the same fail-closed writer as state."""
    atomic_write_json(path, value)


class TaskCheckpointRepository:
    """Own task-run and category checkpoint naming plus legacy migration."""

    def __init__(self, output: str | Path) -> None:
        self.output = Path(output)

    @property
    def run_store(self) -> VersionedCheckpointStore:
        return VersionedCheckpointStore(self.output / "checkpoints", "batch_state_v2")

    def load_run(self) -> dict:
        return self.run_store.load_or_migrate(
            legacy_path=self.output / "batch_state_v2.json", default={})

    def save_run(self, value: Mapping) -> Path:
        return self.run_store.save(value)

    def category_store(self, category_dir: Path, name: str) -> VersionedCheckpointStore:
        return VersionedCheckpointStore(category_dir / "checkpoints", name)

    def load_category_mapping(self, category_dir: Path, name: str, legacy_name: str) -> dict:
        return self.category_store(category_dir, name).load_or_migrate(
            legacy_path=category_dir / legacy_name, default={})

    def load_category_records(self, category_dir: Path, name: str, legacy_name: str) -> list[dict]:
        payload = self.load_category_mapping(category_dir, name, legacy_name)
        records = (payload.get("records") or []) if isinstance(payload, Mapping) else []
        return [dict(row) for row in records if isinstance(row, Mapping)]

    def load_category_state(self, category_dir: Path) -> dict:
        return self.load_category_mapping(category_dir, "category_state", "category_state.json")

    def save_category(self, category_dir: Path, category_state: Mapping,
                      rankings: list[Mapping], details: list[Mapping]) -> None:
        self.category_store(category_dir, "category_state").save(category_state)
        self.category_store(category_dir, "rankings").save({"records": rankings})
        self.category_store(category_dir, "details").save({"records": details})

    def legacy_category_state(self, group: str) -> dict:
        category_dir = self.output / "categories" / group
        return self.load_category_state(category_dir)


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["TaskCheckpointRepository", "read_json", "write_json_atomic"]
