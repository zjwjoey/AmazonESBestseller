from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Iterable

from .models import AmazonCategoryPlacement, PlacementStatus


def placement_id_for(marketplace: str, category_path: Iterable[str]) -> str:
    """Stable marketplace-aware identity for one category placement."""
    path = tuple(str(part).strip() for part in category_path if str(part).strip())
    material = f"{marketplace.upper()}\x1f" + "\x1f".join(path)
    return "pl_" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


class CategoryPlacementGraph:
    def __init__(self, placements: Iterable[AmazonCategoryPlacement] = ()):
        self.placements = {p.placement_id: p for p in placements}

    def add(self, placement: AmazonCategoryPlacement) -> AmazonCategoryPlacement:
        existing = self.placements.get(placement.placement_id)
        if existing is not None and existing.marketplace != placement.marketplace:
            raise ValueError("placement_id cannot cross marketplaces")
        self.placements[placement.placement_id] = placement
        return placement

    def children(self, placement_id: str) -> list[AmazonCategoryPlacement]:
        return sorted((p for p in self.placements.values() if p.parent_placement_id == placement_id),
                      key=lambda p: (p.depth, p.category_name, p.placement_id))

    def roots(self) -> list[AmazonCategoryPlacement]:
        return [p for p in self.placements.values() if not p.parent_placement_id]

    def by_category_id(self, category_id: str) -> list[AmazonCategoryPlacement]:
        return [p for p in self.placements.values() if p.category_id == category_id]

    def to_records(self) -> list[dict]:
        return [p.to_dict() for p in sorted(self.placements.values(), key=lambda p: (p.depth, p.category_path))]

    @classmethod
    def from_records(cls, rows: Iterable[dict]) -> "CategoryPlacementGraph":
        return cls(AmazonCategoryPlacement.from_dict(row) for row in rows)

    def validate(self) -> list[str]:
        from .validation import validate_tree
        return validate_tree(self)
