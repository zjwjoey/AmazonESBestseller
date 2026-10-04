from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from ..access.detector import AccessStopError
from .graph import CategoryPlacementGraph, placement_id_for
from .models import AmazonCategoryPlacement, PlacementStatus


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CategoryCrawlerState:
    """Atomic JSON state for placement-level resume, not a CSV-only state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.graph = CategoryPlacementGraph()
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.graph = CategoryPlacementGraph.from_records(data.get("placements") or [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "updated_at": _now(),
                   "placements": self.graph.to_records()}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with tmp.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def write_authoritative_graph(self) -> Path | None:
        """Publish the latest graph only after the complete tree validates."""
        errors = self.graph.validate()
        incomplete = [
            f"{placement_id} status is {placement.status.value}"
            for placement_id, placement in self.graph.placements.items()
            if placement.status is not PlacementStatus.DONE
        ]
        if errors or incomplete:
            return None
        target = self.path.parent / "latest_authoritative_category_graph.json"
        payload = {
            "schema_version": 1,
            "tree_valid": True,
            "placements": self.graph.to_records(),
        }
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with tmp.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        return target


class CategoryCrawler:
    """Serial category crawler using the project's transport boundary."""

    def __init__(self, state: CategoryCrawlerState,
                 fetch_children: Callable[[AmazonCategoryPlacement], Mapping], *,
                 max_attempts: int = 3):
        self.state = state
        self.fetch_children = fetch_children
        self.max_attempts = max_attempts

    def seed(self, *, marketplace: str, category_id: str, category_name: str,
             canonical_url: str, observed_at: str | None = None) -> AmazonCategoryPlacement:
        observed_at = observed_at or _now()
        placement = AmazonCategoryPlacement(
            placement_id=placement_id_for(marketplace, (category_id,)),
            marketplace=marketplace.upper(), category_id=category_id,
            category_name=category_name, canonical_url=canonical_url,
            parent_placement_id=None, parent_category_id=None, depth=0,
            category_path=(category_id,), category_name_path=(category_name,),
            first_seen_at=observed_at, last_seen_at=observed_at,
            source_url=canonical_url)
        self.state.graph.add(placement)
        self.state.save()
        return placement

    def _enqueue_children(self, parent: AmazonCategoryPlacement, rows: list[Mapping], now: str) -> None:
        for child in rows:
            category_id = str(child.get("category_id") or "").strip()
            if not category_id or category_id in parent.category_path:
                continue
            path = parent.category_path + (category_id,)
            placement = AmazonCategoryPlacement(
                placement_id=placement_id_for(parent.marketplace, path),
                marketplace=parent.marketplace, category_id=category_id,
                category_name=str(child.get("category_name") or category_id),
                canonical_url=str(child.get("canonical_url") or child.get("url") or ""),
                parent_placement_id=parent.placement_id,
                parent_category_id=parent.category_id, depth=parent.depth + 1,
                category_path=path,
                category_name_path=parent.category_name_path + (str(child.get("category_name") or category_id),),
                first_seen_at=now, last_seen_at=now,
                source_url=str(child.get("source_url") or child.get("canonical_url")
                               or child.get("url") or ""))
            if placement.placement_id not in self.state.graph.placements:
                self.state.graph.add(placement)

    def run(self, *, max_placements: int | None = None) -> dict:
        processed = 0
        while True:
            pending = [p for p in self.state.graph.placements.values()
                       if p.status in {PlacementStatus.PENDING, PlacementStatus.RETRY_WAIT}]
            if not pending or (max_placements is not None and processed >= max_placements):
                break
            row = sorted(pending, key=lambda p: (p.depth, p.category_path))[0]
            row.status = PlacementStatus.IN_PROGRESS
            row.attempt_count += 1
            row.last_attempt_at = _now()
            self.state.save()
            blocked = False
            try:
                result = dict(self.fetch_children(row) or {})
                self._enqueue_children(row, list(result.get("children") or []), _now())
                row.status = PlacementStatus.DONE
                row.last_error_type = row.last_error_message = row.next_retry_at = None
            except Exception as exc:
                row.last_error_type = type(exc).__name__
                row.last_error_message = str(exc)
                error_kind = str(getattr(exc, "kind", "") or "").upper()
                error_text = str(exc).upper()
                blocked = isinstance(exc, AccessStopError)
                blocked = blocked or error_kind in {"BOT_BLOCK", "CAPTCHA", "INTERSTITIAL", "RATE_LIMIT"}
                blocked = blocked or any(marker in error_text for marker in
                                         ("CAPTCHA", "ROBOT CHECK", "ACCESS DENIED", "HTTP 403", "HTTP 429"))
                if blocked:
                    row.status = PlacementStatus.BLOCKED
                    row.next_retry_at = None
                elif row.attempt_count >= self.max_attempts:
                    row.status = PlacementStatus.FAILED_FINAL
                else:
                    row.status = PlacementStatus.RETRY_WAIT
                    row.next_retry_at = _now()
            row.last_seen_at = _now()
            self.state.save()
            processed += 1
            if blocked:
                break
        errors = self.state.graph.validate()
        completion_errors = [
            f"{placement_id} status is {placement.status.value}"
            for placement_id, placement in self.state.graph.placements.items()
            if placement.status is not PlacementStatus.DONE
        ]
        authoritative_path = self.state.write_authoritative_graph()
        return {"processed": processed, "placements": len(self.state.graph.placements),
                "tree_valid": not errors, "crawl_complete": not completion_errors,
                "tree_errors": errors, "completion_errors": completion_errors,
                "latest_authoritative_category_graph": authoritative_path}
