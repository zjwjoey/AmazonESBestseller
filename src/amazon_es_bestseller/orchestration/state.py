"""Durable scheduler and category-state models for reviewed task collection."""
from __future__ import annotations

from datetime import datetime
import threading
from typing import Mapping

from .checkpoint import TaskCheckpointRepository
from .plan import normalize_url


class TaskRuntimeState:
    """Own the resumable run shape, ASIN claims, and worker-slot deadlines."""

    def __init__(self, repository: TaskCheckpointRepository, plan: Mapping, *, mode: str,
                 slots: int, raw: Mapping, claimed_asins: set[str]) -> None:
        self.repository = repository
        self.plan = plan
        self.mode = mode
        self.slots = slots
        self.raw = dict(raw)
        self.category_states = dict(raw.get("categories") or {})
        self.completed_source_urls = list(raw.get("completed_source_urls") or [])
        self.claimed = claimed_asins
        self.claim_lock = threading.Lock()
        self.worker_ready_at = [float(value) for value in raw.get("worker_ready_at", [])]
        self.worker_ready_at = (self.worker_ready_at + [0.0] * slots)[:slots]
        self.phase_fingerprints = dict(raw.get("phase_fingerprints") or {})
        self.phase_status = dict(raw.get("phase_status") or {})
        self.prepare_resume()

    @classmethod
    def load(cls, repository: TaskCheckpointRepository, plan: Mapping, *, mode: str,
             slots: int) -> "TaskRuntimeState":
        raw = repository.load_run()
        details = read_detail_asins(repository)
        # Claims are process-local. Retaining an interrupted claim would block
        # a persisted pending-detail ASIN forever on resume; successful detail
        # records remain claimed so they are never fetched twice.
        claimed = details
        return cls(repository, plan, mode=mode, slots=slots, raw=raw, claimed_asins=claimed)

    def prepare_resume(self) -> None:
        for _group, value in list(self.category_states.items()):
            if not isinstance(value, Mapping):
                continue
            value = dict(value)
            if value.get("status") == "RUNNING":
                value["status"] = "PENDING"
            elif value.get("status") == "DETAIL_INCOMPLETE":
                value["status"] = "PENDING"
                value["attempts"] = 0
            self.category_states[_group] = value

    def claim_asins(self, asins: list[str]) -> list[str]:
        with self.claim_lock:
            fresh = [asin for asin in asins if asin and asin not in self.claimed]
            self.claimed.update(fresh)
            return fresh

    def release_asins(self, asins: list[str]) -> None:
        with self.claim_lock:
            for asin in asins:
                self.claimed.discard(asin)

    def save(self, run_status: str, active_workers: list[str]) -> None:
        self.repository.save_run({
            "schema_version": 2, "task_id": self.plan["task_id"], "mode": self.mode,
            "run_status": run_status, "categories": self.category_states,
            "worker_ready_at": self.worker_ready_at, "active_workers": active_workers,
            "claimed_asins": sorted(self.claimed),
            "completed_source_urls": sorted(set(self.completed_source_urls)),
            "phase_fingerprints": self.phase_fingerprints,
            "phase_status": self.phase_status,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def bind_phase(self, phase: str, *, plan_sha256: str,
                   candidate_manifest_sha256: str = "", code_head: str = "UNKNOWN") -> None:
        """Persist and verify the immutable resume contract for one phase."""
        current = self.phase_fingerprints.get(phase)
        if isinstance(current, Mapping):
            if str(current.get("plan_sha256") or "") != str(plan_sha256):
                raise ValueError("PLAN_FINGERPRINT_MISMATCH")
            if (phase == "detail" and
                    str(current.get("candidate_manifest_sha256") or "") != str(candidate_manifest_sha256)):
                raise ValueError("CANDIDATE_FINGERPRINT_MISMATCH")
            previous_head = str(current.get("code_head") or "UNKNOWN")
            current_head = str(code_head or "UNKNOWN")
            if previous_head != "UNKNOWN" and current_head != "UNKNOWN" and previous_head != current_head:
                raise ValueError("CODE_FINGERPRINT_MISMATCH")
        self.phase_fingerprints[phase] = {
            "phase": phase,
            "plan_sha256": str(plan_sha256),
            "candidate_manifest_sha256": str(candidate_manifest_sha256 or ""),
            "code_head": str(code_head or "UNKNOWN"),
            "schema_version": 1,
        }

    def has_unfinished_reserve(self, group: str, category: Mapping) -> bool:
        current = self.category_states.get(group, {})
        source_status = current.get("source_status") or {}
        if not source_status:
            source_status = self.repository.legacy_category_state(group).get("source_status") or {}
        completed = {url for url, status in source_status.items() if status == "completed"}
        return any(str(source.get("role") or "primary").lower() == "reserve"
                   and normalize_url(source.get("source_url")) not in completed
                   for source in category.get("sources", []))


def read_detail_asins(repository: TaskCheckpointRepository) -> set[str]:
    from .checkpoint import read_json
    details = read_json(repository.output / "details.json", [])
    return {str(row.get("asin") or "").upper() for row in details
            if isinstance(row, Mapping) and row.get("asin")}


class CategoryRuntimeState:
    """Own one category's ranking/detail checkpoint and retry queue."""

    def __init__(self, repository: TaskCheckpointRepository, category_dir, group: str,
                 worker_id: int) -> None:
        self.repository = repository
        self.category_dir = category_dir
        self.group = group
        self.worker_id = worker_id
        self.persisted = repository.load_category_state(category_dir)
        self.rankings = repository.load_category_records(category_dir, "rankings", "rankings.json")
        details = repository.load_category_records(category_dir, "details", "details.json")
        self.detail_map = {str(row.get("asin") or "").upper(): row for row in details
                           if row.get("asin")}
        self.source_status = dict(self.persisted.get("source_status") or {})
        self.ranking_page_statuses = list(self.persisted.get("ranking_page_statuses") or [])
        self.ranking_source_status = dict(self.persisted.get("ranking_source_status") or self.source_status)
        self.detail_phase_status = str(self.persisted.get("detail_phase_status") or "")
        self.pending_detail_asins = {
            str(asin).strip().upper() for asin in self.persisted.get("pending_detail_asins") or []
            if str(asin).strip()
        }

    def save(self, status: str, active_url: str = "", *, phase: str = "all") -> None:
        if phase in {"ranking", "all"}:
            self.ranking_source_status = dict(self.source_status)
        if phase in {"detail", "all"}:
            self.detail_phase_status = status
        self.repository.save_category(self.category_dir, {
            "schema_version": 1, "research_category": self.group, "worker_id": self.worker_id,
            "status": status, "active_source_url": active_url,
            "source_status": self.source_status,
            "ranking_source_status": self.ranking_source_status,
            "ranking_page_statuses": self.ranking_page_statuses,
            "detail_phase_status": self.detail_phase_status,
            "raw_ranking_records": len(self.rankings),
            "unique_asins": len({str(row.get("asin") or "").upper() for row in self.rankings if row.get("asin")}),
            "detail_records": len(self.detail_map),
            "pending_detail_asins": sorted(self.pending_detail_asins),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }, self.rankings, list(self.detail_map.values()))


__all__ = ["CategoryRuntimeState", "TaskRuntimeState"]
