"""Durable cumulative budgets. Reservations survive interrupted requests."""
from __future__ import annotations

from pathlib import Path
from collections import Counter
from .contracts import SOURCE_BUDGETS, PHASE_TARGETS, ORIGINS, timestamp
from .storage import atomic_json, read_sealed, sealed, file_lock, digest


class BudgetLedger:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "budget_ledger.json"

    def _load(self):
        if self.path.exists():
            return read_sealed(self.path)
        return {"source_budgets": dict(SOURCE_BUDGETS), "adjustments": [],
                "requests": {}, "observation_batches": {}}

    def _write(self, data):
        atomic_json(self.path, sealed(data), backup=True)

    def counters(self, origin):
        data = self._load()
        requests = [r for r in data["requests"].values() if r["origin"] == origin]
        batches = [b for b in data["observation_batches"].values() if b["origin"] == origin]
        sources = Counter()
        for batch in batches:
            # Empty page batches are retained for immutable/idempotent replay,
            # but carry source_type=None and consume no observation quota.
            # Counter[None] += 0 otherwise creates a non-string JSON key.
            if not batch["observation_ids"]:
                continue
            sources[batch["source_type"]] += len(batch["observation_ids"])
        return {"request_count": len(requests), "page_visit_count": sum(r["kind"] == "PAGE" for r in requests),
                "reserved_request_count": sum(r["state"] == "RESERVED" for r in requests),
                "requests_not_sent": sum(r["state"] == "PAUSED_BEFORE_REQUEST" for r in requests),
                "completed_request_count": sum(r["state"] not in {"RESERVED", "PAUSED_BEFORE_REQUEST"} for r in requests),
                "daily_requests": dict(Counter(r["local_date"] for r in requests)),
                "observation_count": sum(sources.values()), "source_counts": dict(sources),
                "source_budgets": data["source_budgets"], "adjustments": data["adjustments"]}

    def reserve_request(self, request_id, *, origin, source_type, url, kind,
                        observed_at, caps):
        if origin not in ORIGINS or kind not in {"PAGE", "ACP"}:
            raise ValueError("INVALID_REQUEST_RESERVATION")
        instant = timestamp(observed_at)
        with file_lock(self.root / "budget.lock"):
            data = self._load()
            if request_id in data["requests"]:
                raise ValueError("REQUEST_ALREADY_RESERVED: ambiguous requests cannot be retried silently")
            rows = [r for r in data["requests"].values() if r["origin"] == origin]
            daily = sum(r["local_date"] == instant["local_date"] for r in rows)
            if len(rows) >= caps["max_requests"] or daily >= caps["max_requests_per_day"]:
                raise ValueError("REQUEST_BUDGET_EXHAUSTED")
            if kind == "PAGE" and sum(r["kind"] == "PAGE" for r in rows) >= caps["max_pages"]:
                raise ValueError("PAGE_BUDGET_EXHAUSTED")
            data["requests"][request_id] = {"origin": origin, "source_type": source_type,
                "url": url, "kind": kind, **instant, "state": "RESERVED"}
            self._write(data)

    def finish_request(self, request_id, status):
        with file_lock(self.root / "budget.lock"):
            data = self._load()
            data["requests"][request_id]["state"] = status
            self._write(data)

    def accept(self, batch_id, observations, *, origin, phase, max_observations=None):
        """Idempotent immutable batch reservation before committing a page."""
        if origin not in ORIGINS or phase not in PHASE_TARGETS:
            raise ValueError("INVALID_BUDGET_SCOPE")
        types = {o["source_type"] for o in observations}
        if len(types) > 1:
            raise ValueError("BATCH_MIXES_SOURCE_TYPES")
        ids = sorted(o["observation_id"] for o in observations)
        if len(ids) != len(set(ids)):
            raise ValueError("DUPLICATE_OBSERVATION_SLOT")
        payload = {"origin": origin, "source_type": next(iter(types), None), "observation_ids": ids}
        with file_lock(self.root / "budget.lock"):
            data = self._load()
            if batch_id in data["observation_batches"]:
                if data["observation_batches"][batch_id] != payload:
                    raise ValueError("IMMUTABLE_BATCH_CONFLICT")
                return False
            batches = [b for b in data["observation_batches"].values() if b["origin"] == origin]
            prior_ids = {oid for b in batches for oid in b["observation_ids"]}
            if prior_ids.intersection(ids):
                raise ValueError("OBSERVATION_ALREADY_COUNTED_IN_ANOTHER_BATCH")
            cap = min(30000, PHASE_TARGETS[phase], max_observations if max_observations is not None else 30000)
            if len(prior_ids) + len(ids) > cap:
                raise ValueError("OBSERVATION_BUDGET_EXHAUSTED")
            source = payload["source_type"]
            source_count = sum(len(b["observation_ids"]) for b in batches if b["source_type"] == source)
            if source and source_count + len(ids) > data["source_budgets"][source]:
                raise ValueError("SOURCE_BUDGET_EXHAUSTED")
            data["observation_batches"][batch_id] = payload
            self._write(data)
            return True

    def adjust(self, source_budgets, *, actor, reason, replacement_sources, approved=False):
        if not approved or not actor or not reason or not replacement_sources:
            raise ValueError("BUDGET_ADJUSTMENT_REQUIRES_EXPLICIT_REVIEW")
        for row in replacement_sources:
            if isinstance(row, dict) and "quantity" not in row and "replacement_quantity" in row:
                row = {**row, "quantity": row["replacement_quantity"]}
            if (not isinstance(row, dict) or not row.get("source_id")
                    or row.get("source_type") not in SOURCE_BUDGETS
                    or not row.get("research_category") or not row.get("verification_evidence")
                    or not row.get("review_evidence") or not row.get("page_urls")
                    or not isinstance(row.get("quantity"), int) or row["quantity"] <= 0
                    or row.get("tier") not in {1, 2, 3}):
                raise ValueError("TYPED_REVIEWED_REPLACEMENT_PLAN_REQUIRED")
            if row["tier"] == 3 and row.get("cross_source_approved") is not True:
                raise ValueError("CROSS_SOURCE_REPLACEMENT_REQUIRES_EXPLICIT_APPROVAL")
        if set(source_budgets) != set(SOURCE_BUDGETS) or any(not isinstance(v, int) or v < 0 for v in source_budgets.values()):
            raise ValueError("INVALID_SOURCE_BUDGETS")
        if sum(source_budgets.values()) > 30000:
            raise ValueError("GLOBAL_BUDGET_EXCEEDED")
        with file_lock(self.root / "budget.lock"):
            data = self._load()
            completed = Counter()
            for batch in data["observation_batches"].values():
                if batch["origin"] == "LIVE_CAPTURE" and batch["observation_ids"]:
                    completed[batch["source_type"]] += len(batch["observation_ids"])
            if any(source_budgets.get(k, 0) < v for k, v in completed.items()):
                raise ValueError("BUDGET_BELOW_ALREADY_COMPLETED")
            data["adjustments"].append({"old_budget": data["source_budgets"], "new_budget": source_budgets,
                "actual_completed": dict(completed), "gaps": {k: max(0, data["source_budgets"][k]-completed[k]) for k in SOURCE_BUDGETS},
                "replacement_sources": replacement_sources, "replacement_quantity": {k: source_budgets[k]-data["source_budgets"][k] for k in SOURCE_BUDGETS},
                "actor": actor, "reason": reason, "approved": True, **timestamp()})
            data["source_budgets"] = dict(source_budgets)
            self._write(data)
