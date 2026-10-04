from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlacementStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED_FINAL = "FAILED_FINAL"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class AmazonCategory:
    marketplace: str
    category_id: str
    category_name: str
    canonical_url: str
    first_seen_at: str
    last_seen_at: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class AmazonCategoryPlacement:
    placement_id: str
    marketplace: str
    category_id: str
    category_name: str
    canonical_url: str
    parent_placement_id: str | None
    parent_category_id: str | None
    depth: int
    category_path: tuple[str, ...]
    category_name_path: tuple[str, ...]
    first_seen_at: str
    last_seen_at: str
    status: PlacementStatus = PlacementStatus.PENDING
    attempt_count: int = 0
    last_attempt_at: str | None = None
    last_error_type: str | None = None
    last_error_message: str | None = None
    next_retry_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        row = self.__dict__.copy()
        row["status"] = self.status.value
        row["category_path"] = list(self.category_path)
        row["category_name_path"] = list(self.category_name_path)
        return row

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "AmazonCategoryPlacement":
        data = dict(row)
        data["status"] = PlacementStatus(str(data.get("status", "PENDING")))
        data["category_path"] = tuple(data.get("category_path") or ())
        data["category_name_path"] = tuple(data.get("category_name_path") or ())
        return cls(**data)
