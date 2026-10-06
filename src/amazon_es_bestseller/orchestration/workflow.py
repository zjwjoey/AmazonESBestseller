"""Production V1 stage adapters.

This is intentionally an adapter layer, not a second parser or collector.  It
connects the reviewed V1 snapshot/detail planner/source-master modules to the
hash-bound :class:`ProductionRun` and only accepts saved evidence in offline
mode.  A task configuration can point at facts; it cannot inject a stage
``READY`` result.
"""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ..collection.detail import CURRENT_DETAIL_SCHEMA_VERSION, reparse_saved_details
from ..collection.detail_executor import NETWORK_ACTIONS, execute_detail_plan
from ..collection.planning import DetailState
from ..models import merge_ranking_and_detail, normalize_asin
from ..monitoring.detail_planner import DetailRefreshPolicy, build_detail_plan, write_detail_plan
from ..monitoring.snapshot import build_ranking_snapshot
from ..production.spanish_master import build_spanish_master
from ..quality.source_fields import audit_source_fields
from ..quality.source_gate import evaluate_source_gate
from ..translation.production import (build_production_input, build_production_state,
                                      build_release_translation_candidate, compute_release_status,
                                      records_for_preclean)
from ..translation.preclean import audit_records
from ..translation.dictionary_sync import dictionary_manifest, normalize_context, sync_evidence
from ..translation.rerender import rerender_affected_fields
from ..translation.repair_queue import apply_repair, build_repair_queue
from ..translation.schemas import TRANSLATION_SCHEMA_VERSION
from ..translation.service import TranslationService
from ..translation.cache import TranslationCache

from .history import HistoryRepository, JsonHistoryRepository
from .production_run import STAGES, artifact_hash
from .task_config import TaskConfig
from .translation_batch import TranslationBatchError, load_selection_manifest


class ProductionWorkflowError(RuntimeError):
    pass


class RankingSnapshotCollector(Protocol):
    """Explicit seam for the existing conservative V1 browser collector."""

    def collect(self, task: TaskConfig, output_root: Path) -> Mapping[str, Any]: ...


class ExistingV1SnapshotCollector:
    """Thin adapter; it never changes browser pacing or Access Gate policy."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def collect(self, task: TaskConfig, output_root: Path) -> Mapping[str, Any]:
        from ..monitoring.snapshot import collect_ranking_snapshot
        return collect_ranking_snapshot(task.source_urls, self.session, output_root,
                                        pages_per_url=task.pages_per_url, parser_version="v1")


class ExistingV1DetailCollector:
    """Callable adapter for the existing serial V1 detail collector.

    The session is deliberately supplied by the reviewed caller rather than
    constructed from a task file.  ``collect_details`` retains ownership of
    its Access Gate, pacing, saved-HTML and checkpoint behavior.
    """

    def __init__(self, session: Any) -> None:
        self.session = session

    def __call__(self, asins: list[str], _session: Any, output_root: str, **kwargs: Any) -> list[dict]:
        from ..collection.detail import collect_details
        return collect_details(asins, self.session, output_root, **kwargs)


class _TargetedRefreshPolicy(DetailRefreshPolicy):
    """Explicit task-scoped refreshes; unchanged cache stays untouched."""

    def __init__(self, asins: frozenset[str]) -> None:
        self.asins = asins

    def should_refresh(self, ranking_record: Mapping, detail_record: Mapping | None, now=None) -> bool:
        return normalize_asin(ranking_record.get("asin") or ranking_record.get("ranking_asin")) in self.asins


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
                         encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProductionWorkflowError("ARTIFACT_JSON_INVALID:%s" % path) from exc


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _merge_by_asin(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for row in records:
        asin = normalize_asin(row.get("asin"))
        if asin:
            values[asin] = dict(row)
    return [values[asin] for asin in sorted(values)]


def _snapshot_changes(previous: Mapping[str, Any] | None, current_records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Emit only the immutable Initial/Incremental statuses used by history."""
    before = {normalize_asin(row.get("asin") or row.get("ranking_asin")): row
              for row in ((previous or {}).get("records") or []) if isinstance(row, Mapping)}
    after = {normalize_asin(row.get("asin") or row.get("ranking_asin")): row
             for row in current_records if isinstance(row, Mapping)}
    events: list[dict[str, Any]] = []
    for asin in sorted(set(before) | set(after)):
        old, new = before.get(asin), after.get(asin)
        if not old:
            events.append({"asin": asin, "status": "NEW"}); continue
        if not new:
            events.append({"asin": asin, "status": "REMOVED"}); continue
        def contexts(row: Mapping[str, Any]) -> set[str]:
            data = row.get("ranking_contexts") or [row]
            return {json.dumps({"source": item.get("ranking_source_url"),
                                "page": item.get("ranking_page_number"),
                                "rank": item.get("bestseller_rank", item.get("ranking_rank"))},
                               ensure_ascii=False, sort_keys=True)
                    for item in data if isinstance(item, Mapping)}
        def best_rank(row: Mapping[str, Any]) -> int | None:
            values = []
            for item in row.get("ranking_contexts") or [row]:
                try: values.append(int(item.get("bestseller_rank", item.get("ranking_rank"))))
                except (TypeError, ValueError): pass
            return min(values) if values else None
        old_rank, new_rank = best_rank(old), best_rank(new)
        if old_rank is not None and new_rank is not None and new_rank < old_rank:
            events.append({"asin": asin, "status": "RANK_UP", "from_rank": old_rank, "to_rank": new_rank})
        elif old_rank is not None and new_rank is not None and new_rank > old_rank:
            events.append({"asin": asin, "status": "RANK_DOWN", "from_rank": old_rank, "to_rank": new_rank})
        old_contexts, new_contexts = contexts(old), contexts(new)
        if new_contexts - old_contexts:
            events.append({"asin": asin, "status": "CONTEXT_ADDED", "count": len(new_contexts - old_contexts)})
        if old_contexts - new_contexts:
            events.append({"asin": asin, "status": "CONTEXT_REMOVED", "count": len(old_contexts - new_contexts)})
        if not events or events[-1].get("asin") != asin:
            events.append({"asin": asin, "status": "UNCHANGED"})
    return events


class ProductionWorkflow:
    """Concrete V1 stage producer backed by saved files and existing modules."""

    def __init__(self, task: TaskConfig, run_dir: str | Path, *, run_id: str,
                 history: HistoryRepository | None = None,
                 snapshot_collector: RankingSnapshotCollector | None = None,
                 detail_collector: Callable[..., list[dict]] | None = None,
                 detail_session: Any = None,
                 translation_provider: Any | None = None) -> None:
        self.task, self.run_dir, self.run_id = task, Path(run_dir), str(run_id)
        self.history = history or JsonHistoryRepository(task.history_dir or (self.run_dir / "history"))
        self.snapshot_collector, self.detail_collector = snapshot_collector, detail_collector
        self.detail_session, self.translation_provider = detail_session, translation_provider
        self.artifacts = self.run_dir / "artifacts"
        self.work = self.run_dir / "work"

    def _store(self, stage: str, value: Mapping[str, Any]) -> dict[str, Any]:
        """Persist producer output independently from the runner manifest."""
        path = self.artifacts / (stage + ".json")
        payload = dict(value)
        payload.setdefault("producer", "production-v1-workflow")
        payload.setdefault("stage", stage)
        payload.setdefault("task_id", self.task.task_id)
        payload.setdefault("run_id", self.run_id)
        if path.exists():
            existing = _read_json(path)
            if existing != payload:
                raise ProductionWorkflowError("ARTIFACT_IMMUTABLE_CONFLICT:%s" % stage)
        else:
            _atomic_json(path, payload)
        return {"artifact_path": str(path.relative_to(self.run_dir)),
                "artifact_file_hash": _sha_file(path), **payload}

    def _prior(self, context: Mapping[str, Any], stage: str) -> Mapping[str, Any]:
        item = ((context.get("manifest") or {}).get("stages") or {}).get(stage) or {}
        payload = item.get("payload") if isinstance(item, Mapping) else None
        if not isinstance(payload, Mapping):
            raise ProductionWorkflowError("UPSTREAM_ARTIFACT_MISSING:%s" % stage)
        return payload

    def _artifact_data(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        path = self.run_dir / str(payload.get("artifact_path") or "")
        if not path.is_file() or _sha_file(path) != payload.get("artifact_file_hash"):
            raise ProductionWorkflowError("ARTIFACT_FILE_HASH_MISMATCH:%s" % payload.get("stage"))
        value = _read_json(path)
        if not isinstance(value, Mapping):
            raise ProductionWorkflowError("ARTIFACT_OBJECT_REQUIRED:%s" % path)
        return value

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]]:
        result = {stage: getattr(self, "stage_" + stage.replace("-", "_")) for stage in STAGES}
        return result

    def stage_preflight(self, _context: Mapping[str, Any]) -> Mapping[str, Any]:
        evidence = self.task.evidence_fingerprints()
        if self.task.network_mode == "live" and self.snapshot_collector is None:
            # CLI has no implicit browser/profile construction. Callers must
            # hand in the same reviewed V1 session used by the collector.
            raise ProductionWorkflowError("LIVE_TRANSPORT_UNCONFIGURED")
        provider_mode = str(self.task.translation.get("provider_mode") or "fake").lower()
        if self.task.network_mode == "offline" and provider_mode != "fake":
            raise ProductionWorkflowError("OFFLINE_PROVIDER_MUST_BE_FAKE")
        return self._store("preflight", {"status": "READY", "offline": self.task.network_mode == "offline",
            "parser": {"ranking": "V1", "detail": "V1"}, "evidence_fingerprints": evidence,
            "translation_provider": provider_mode,
            "counts": {"network_requests": 0},
            "manifest_counts": {"status": "running", "config_hash": artifact_hash(self.task.runner_config())}})

    def stage_ranking_authority(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        preflight = self._prior(context, "preflight")
        root = self.work / "ranking_snapshots"
        if self.task.network_mode == "live":
            result = self.snapshot_collector.collect(self.task, root) if self.snapshot_collector else None
            if not isinstance(result, Mapping):
                raise ProductionWorkflowError("LIVE_SNAPSHOT_RESULT_INVALID")
            manifest, frozen, found = result.get("manifest"), result.get("records"), result.get("path")
            source_hash = artifact_hash({"urls": self.task.source_urls, "pages_per_url": self.task.pages_per_url,
                                         "manifest": manifest})
        else:
            raw = self.task.load_ranking_evidence()
            records = [dict(row) for row in raw.get("records") or [] if isinstance(row, Mapping)]
            statuses = raw.get("source_statuses") or raw.get("page_statuses")
            planned = raw.get("planned_sources") or raw.get("sources")
            if not statuses or not planned:
                raise ProductionWorkflowError("RANKING_EVIDENCE_SOURCE_STATUS_REQUIRED")
            source_hash = _sha_file(self.task.ranking_evidence) if self.task.ranking_evidence else ""
            snapshot_id = "snapshot_%s_%s" % (self.run_id, source_hash[:16])
            found = next(root.glob("**/%s" % snapshot_id), None) if root.exists() else None
            if found:
                manifest = _read_json(found / "manifest.json"); frozen = _read_json(found / "rankings.json")
            else:
                result = build_ranking_snapshot(records, root, planned_sources=planned,
                    source_statuses=statuses, snapshot_id=snapshot_id,
                    parser_version="collection.ranking", publish_authoritative_pointer=False)
                manifest, frozen = result["manifest"], result["records"]
                found = result["path"]
        if not isinstance(manifest, Mapping) or not isinstance(frozen, list) or not isinstance(found, (str, Path)):
            raise ProductionWorkflowError("RANKING_SNAPSHOT_RESULT_INVALID")
        if str(manifest.get("snapshot_status")) != "AUTHORITATIVE":
            raise ProductionWorkflowError("RANKING_SNAPSHOT_NOT_AUTHORITATIVE")
        previous = self.history.latest_snapshot() if self.task.mode == "incremental" else None
        changes = _snapshot_changes(previous, frozen)
        receipt = {"snapshot_id": manifest.get("snapshot_id"), "records": frozen,
                   "snapshot_path": str(Path(found).relative_to(self.run_dir)),
                   "records_hash": artifact_hash(frozen), "changes": changes,
                   "manifest_hash": artifact_hash(manifest)}
        self.history.append_snapshot(receipt)
        return self._store("ranking-authority", {"status": "READY", "snapshot": receipt,
            "snapshot_manifest": manifest, "input_artifact_hashes": {"preflight": preflight.get("artifact_file_hash")},
            "source_evidence_hash": source_hash,
            "counts": {"ranking_records": len(frozen), "unique_asins": len({r.get("asin") for r in frozen}),
                       "changes": len(changes)},
            "manifest_counts": {"ranking_pages_requested": manifest.get("expected_page_count", 0),
                                "ranking_pages_completed": manifest.get("completed_page_count", 0),
                                "ranking_records": len(frozen), "unique_asins": len({r.get("asin") for r in frozen})}})

    def _snapshot_bundle(self, context: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        payload = self._prior(context, "ranking-authority")
        data = self._artifact_data(payload)
        snapshot = data.get("snapshot") or {}
        root = self.run_dir / str(snapshot.get("snapshot_path") or "")
        manifest, records = _read_json(root / "manifest.json"), _read_json(root / "rankings.json")
        if not isinstance(manifest, dict) or not isinstance(records, list):
            raise ProductionWorkflowError("RANKING_SNAPSHOT_CONTENT_INVALID")
        return manifest, [dict(row) for row in records if isinstance(row, Mapping)]

    def stage_detail_evidence(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest, rankings = self._snapshot_bundle(context)
        detail_root = self.work / "details"
        detail_root.mkdir(parents=True, exist_ok=True)
        existing = self.history.load_details()
        state = DetailState(detail_root / "state" / "details_state.json")
        state.update(existing)
        working_rankings = rankings
        sample_limit = self.task.diagnostic_sample_detail_limit
        if sample_limit is not None:
            selected: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rankings:
                asin = normalize_asin(row.get("asin") or row.get("ranking_asin"))
                if asin and asin not in seen:
                    seen.add(asin); selected.append(row)
                if len(selected) >= sample_limit:
                    break
            working_rankings = selected
        wanted = [normalize_asin(row.get("asin") or row.get("ranking_asin")) for row in working_rankings]
        # Reparse is the only legal way old local HTML becomes new detail
        # evidence; no filename or caller-supplied product dict is trusted.
        reparsed = reparse_saved_details(self.task.detail_html_dirs, state, asins=wanted, parser_version="v1")
        cache = _merge_by_asin(existing + reparsed)
        _atomic_json(detail_root / "details.json", cache)
        html_index = {path.stem.upper(): True for root in self.task.detail_html_dirs for path in root.glob("*.html")}
        plan = build_detail_plan({**manifest, "records": working_rankings}, detail_cache=cache,
            saved_html=html_index, current_schema_version=CURRENT_DETAIL_SCHEMA_VERSION,
            target_parser_version="collection.detail_v1",
            refresh_policy=_TargetedRefreshPolicy(self.task.refresh_due_asins))
        paths = write_detail_plan(plan, detail_root / "plans")
        network = [row.get("ranking_asin") for row in plan.get("records") or []
                   if row.get("detail_action") in NETWORK_ACTIONS]
        if network and self.task.network_mode == "offline":
            raise ProductionWorkflowError("OFFLINE_NETWORK_ACTION_REQUIRED:%s" % ",".join(str(x) for x in network))
        if network and self.detail_collector is None:
            raise ProductionWorkflowError("LIVE_DETAIL_TRANSPORT_UNCONFIGURED:%s" % ",".join(str(x) for x in network))
        return self._store("detail-evidence", {"status": "READY", "plan_path": str(paths["json"].relative_to(self.run_dir)),
            "plan_hash": plan.get("plan_hash"), "seed_reparsed_asins": sorted({r.get("asin") for r in reparsed}),
            "working_ranking_asins": [normalize_asin(row.get("asin") or row.get("ranking_asin"))
                                      for row in working_rankings],
            "diagnostic_sample": sample_limit is not None,
            "snapshot_id": manifest.get("snapshot_id"), "input_artifact_hashes": {"ranking-authority": self._prior(context, "ranking-authority").get("artifact_file_hash")},
            "counts": {"detail_planned": len(plan.get("records") or []), "detail_offline_reparsed": len(reparsed),
                       "detail_network_required": len(network)},
            "manifest_counts": {"detail_planned": len(plan.get("records") or []), "detail_offline_reparsed": len(reparsed)}})

    def stage_offline_reparse(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        upstream = self._prior(context, "detail-evidence")
        plan = _read_json(self.run_dir / str(upstream.get("plan_path") or ""))
        execution = {"offline": self.task.network_mode == "offline",
                     "saved_html": self.task.detail_html_dirs, "parser_version": "v1"}
        if self.detail_collector is not None:
            execution["collector"] = self.detail_collector
        result = execute_detail_plan(plan, self.detail_session, str(self.work / "details"), **execution)
        detail_rows = _read_json(self.work / "details" / "details.json")
        self.history.save_details(detail_rows)
        pending = [row for row in result.get("records") or [] if str(row.get("status")) not in {"REUSED", "SUCCESS", "ALREADY_SUCCESS"}]
        if pending:
            raise ProductionWorkflowError("DETAIL_EVIDENCE_INCOMPLETE")
        return self._store("offline-reparse", {"status": "READY", "details_path": str((self.work / "details" / "details.json").relative_to(self.run_dir)),
            "detail_execution": result, "input_artifact_hashes": {"detail-evidence": upstream.get("artifact_file_hash")},
            "counts": {"detail_cached": sum(1 for row in result.get("records") or [] if row.get("status") == "REUSED"),
                       "detail_offline_reparsed": len(upstream.get("seed_reparsed_asins") or []),
                       "detail_requested": int(result.get("requested_count") or 0),
                       "detail_success": len(detail_rows), "detail_failed": len(pending)},
            "manifest_counts": {"detail_cached": sum(1 for row in result.get("records") or [] if row.get("status") == "REUSED"),
                                "detail_offline_reparsed": len(upstream.get("seed_reparsed_asins") or []),
                                "detail_requested": int(result.get("requested_count") or 0),
                                "detail_success": len(detail_rows), "detail_failed": len(pending)}})

    def stage_normalize(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        ranking_manifest, rankings = self._snapshot_bundle(context)
        detail_stage = self._prior(context, "detail-evidence")
        selected = {str(asin or "").upper() for asin in detail_stage.get("working_ranking_asins") or []}
        if selected:
            rankings = [row for row in rankings if normalize_asin(row.get("asin") or row.get("ranking_asin")) in selected]
        details = _read_json(self.run_dir / str(self._prior(context, "offline-reparse").get("details_path") or ""))
        products = merge_ranking_and_detail(rankings, details)
        for row in products:
            asin = normalize_asin(row.get("asin")); row["asin"] = asin
            row.setdefault("requested_asin", asin); row.setdefault("ranking_asin", asin)
            row.setdefault("detail_asin", asin); row.setdefault("final_asin", asin)
            row.setdefault("ranking_schema_version", ranking_manifest.get("ranking_schema_version"))
            row.setdefault("detail_schema_version", CURRENT_DETAIL_SCHEMA_VERSION)
            row.setdefault("parser_version", row.get("detail_parser_version") or "collection.detail_v1")
            if row.get("ranking_rank") is not None and row.get("bestseller_rank") in (None, ""):
                row["bestseller_rank"] = row["ranking_rank"]
        path = self.work / "normalized_products.json"; _atomic_json(path, products)
        return self._store("normalize", {"status": "READY", "products_path": str(path.relative_to(self.run_dir)),
            "products_hash": artifact_hash(products), "input_artifact_hashes": {
                "ranking-authority": self._prior(context, "ranking-authority").get("artifact_file_hash"),
                "offline-reparse": self._prior(context, "offline-reparse").get("artifact_file_hash")},
            "counts": {"records": len(products), "unique_asins": len({r.get("asin") for r in products})}})

    def stage_source_audit(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = self._artifact_data(self._prior(context, "normalize"))
        products = _read_json(self.run_dir / str(normalized.get("products_path") or ""))
        audit = audit_source_fields(products); gate = evaluate_source_gate(audit)
        if not gate.get("ready"):
            raise ProductionWorkflowError("SOURCE_AUDIT_NOT_READY:%s" % gate.get("status"))
        return self._store("source-audit", {"status": "READY", "audit": audit, "source_gate": gate,
            "input_artifact_hashes": {"normalize": self._prior(context, "normalize").get("artifact_file_hash")},
            "counts": {"records": len(products), "issues": len(audit.get("issues") or [])}})

    def stage_spanish_master(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        products = _read_json(self.run_dir / str(self._artifact_data(self._prior(context, "normalize")).get("products_path") or ""))
        source = self._artifact_data(self._prior(context, "source-audit"))
        master = build_spanish_master(products, source["audit"], source["source_gate"],
                                      existing_master=self.history.load_master(), run_id=self.run_id,
                                      refresh_strategy="preserve_prior")
        # These reports are produced before release from the capabilities that
        # own the checked evidence.  They bind the immutable Master only after
        # checking actual snapshot/detail results; release merely consumes them.
        from ..production.release import STAGE_EVIDENCE_VERSION, build_stage_evidence
        ranking = self._artifact_data(self._prior(context, "ranking-authority"))
        replay = self._artifact_data(self._prior(context, "offline-reparse"))
        expected_asins = {str(row.get("asin") or "").upper() for row in master.get("records") or []}
        ranking_rows = {str(row.get("asin") or row.get("ranking_asin") or "").upper()
                        for row in (ranking.get("snapshot") or {}).get("records") or []}
        execution = (replay.get("detail_execution") or {}).get("records") or []
        detail_status = {str(row.get("asin") or row.get("ranking_asin") or "").upper(): str(row.get("status") or "")
                         for row in execution if isinstance(row, Mapping)}

        def owned_report(check: str, issues: list[dict[str, Any]]) -> dict[str, Any]:
            evidence = build_stage_evidence(master, check)
            passed = not issues
            return {"check": check, "status": "PASS" if passed else "BLOCK", "produced_stage": check,
                    "report_schema_version": STAGE_EVIDENCE_VERSION, "evidence_ref": evidence,
                    "summary": {"records_checked": evidence["record_count"]},
                    "records": [{"asin": asin, "status": "PASS" if passed else "BLOCK", "record_hash": record_hash}
                                for asin, record_hash in evidence["record_hashes"].items()],
                    "issues": issues, "report_produced_by": "spanish-master"}

        ranking_issues = []
        if (ranking.get("snapshot_manifest") or {}).get("snapshot_status") != "AUTHORITATIVE":
            ranking_issues.append({"code": "RANKING_SNAPSHOT_NOT_AUTHORITATIVE"})
        missing_ranking = sorted(expected_asins - ranking_rows)
        if missing_ranking:
            ranking_issues.append({"code": "RANKING_ASIN_SCOPE_MISMATCH", "asins": missing_ranking})
        detail_issues = [{"code": "DETAIL_EXECUTION_NOT_SUCCESS", "asin": asin,
                          "status": detail_status.get(asin, "MISSING")}
                         for asin in sorted(expected_asins)
                         if detail_status.get(asin) not in {"REUSED", "SUCCESS", "ALREADY_SUCCESS"}]
        reports = {"ranking_authority": owned_report("ranking_authority", ranking_issues),
                   "detail_identity": owned_report("detail_identity", detail_issues),
                   "offline_replay": owned_report("offline_replay", detail_issues)}
        self.history.save_master(master)
        return self._store("spanish-master", {"status": "READY", "master": master,
            "stage_reports": reports,
            "input_artifact_hashes": {"source-audit": self._prior(context, "source-audit").get("artifact_file_hash")},
            "counts": {"records": len(master.get("records") or [])}})

    # The remaining handlers do call their existing business modules.  An
    # offline fake provider is deliberately not eligible for formal release;
    # this still leaves auditable artifacts for translation/QA development.
    def _master(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._artifact_data(self._prior(context, "spanish-master"))["master"]

    def _translation_batch(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Build the only master that later translation/release stages may use.

        The collection master remains complete and DRAFT-capable.  A billable
        run instead operates on a separately hash-bound <=1500-ASIN subset,
        so unselected source records can never accidentally appear in a
        bilingual READY export.
        """
        parent_payload = self._prior(context, "spanish-master")
        master = self._artifact_data(parent_payload)["master"]
        records = [dict(row) for row in master.get("records") or [] if isinstance(row, Mapping)]
        available = {str(row.get("asin") or "").upper(): row for row in records}
        provider_mode = str(self.task.translation.get("provider_mode") or "fake").lower()
        path = self.task.translation_selection_manifest
        if path is None:
            if provider_mode == "qwen-mt" or self.task.task_id == "amazon_es_bestseller_5500_202610":
                raise ProductionWorkflowError("TRANSLATION_SELECTION_MANIFEST_REQUIRED")
            selected_asins = sorted(available)
            selection = {"selection_schema_version": "implicit-fake-selection-v1",
                         "selected_asins": selected_asins, "selected_count": len(selected_asins),
                         "max_unique_asins": len(selected_asins), "manifest_file_hash": ""}
        else:
            try:
                selection = load_selection_manifest(
                    path,
                    parent_artifact_hash=str(parent_payload.get("artifact_file_hash") or ""),
                    parent_content_hash=str(master.get("artifact_hash") or ""),
                    available_asins=available,
                )
            except TranslationBatchError as exc:
                raise ProductionWorkflowError(str(exc)) from exc
            selected_asins = list(selection["selected_asins"])
        selected = [available[asin] for asin in selected_asins]
        # Rebuild source facts only for the releaseable subset.  This avoids
        # reusing a full-collection PASS report to bless records that have not
        # entered the selected translation batch.
        batch_audit = audit_source_fields(selected)
        batch_gate = evaluate_source_gate(batch_audit)
        if not batch_gate.get("ready"):
            raise ProductionWorkflowError("TRANSLATION_BATCH_SOURCE_AUDIT_NOT_READY:%s" % batch_gate.get("status"))
        batch_master = build_spanish_master(selected, batch_audit, batch_gate,
                                            run_id=self.run_id, refresh_strategy="preserve_prior")
        return {"parent_spanish_master_artifact_hash": parent_payload.get("artifact_file_hash"),
                "selection_manifest": selection, "translation_batch_master": batch_master,
                "translation_batch_source_audit": batch_audit,
                "translation_batch_source_gate": batch_gate}

    def _translation_master(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        value = self._artifact_data(self._prior(context, "translation-input"))
        master = value.get("translation_batch_master")
        if not isinstance(master, Mapping):
            raise ProductionWorkflowError("TRANSLATION_BATCH_MASTER_MISSING")
        return master

    def stage_translation_input(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        batch = self._translation_batch(context)
        master = batch["translation_batch_master"]
        value = build_production_input(master.get("records") or [], source_run_id=self.run_id,
                                       source_schema_version=str(master.get("master_schema_version") or "master-v1"),
                                       translation_schema_version=TRANSLATION_SCHEMA_VERSION, run_id=self.run_id)
        return self._store("translation-input", {"status": "READY", "translation_input": value, **batch,
            "input_artifact_hashes": {"spanish-master": self._prior(context, "spanish-master").get("artifact_file_hash")},
            "counts": {"records": len(value.get("records") or []),
                       "selected_asins": len(value.get("records") or [])}})

    def stage_preclean(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        value = self._artifact_data(self._prior(context, "translation-input"))["translation_input"]
        audit = audit_records(records_for_preclean(value))
        return self._store("preclean", {"status": "READY", "preclean": audit,
            "input_artifact_hashes": {"translation-input": self._prior(context, "translation-input").get("artifact_file_hash")},
            "counts": dict(audit.get("summary") or {})})

    def stage_dictionary(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Prepare a dictionary baseline; promotion waits for translated QA facts."""
        preclean = self._artifact_data(self._prior(context, "preclean"))["preclean"]
        manifest = dictionary_manifest(source_run_id=self.run_id,
                                       translation_schema_version=TRANSLATION_SCHEMA_VERSION)
        candidates = [
            {"asin": row.get("asin"), "field": field, "source_text": value.get("clean_text", ""),
             "translate_allowed": bool(value.get("translate_allowed"))}
            for row in preclean.get("translation_input_records") or []
            for field, value in (row.get("fields") or {}).items() if isinstance(value, Mapping)
        ]
        return self._store("dictionary", {"status": "READY", "dictionary_manifest": manifest,
            "candidate_input": candidates,
            "input_artifact_hashes": {"preclean": self._prior(context, "preclean").get("artifact_file_hash")},
            "counts": {"candidate_fields": len(candidates)}})

    def stage_translation(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        from ..commands.translation import FakeTranslationProvider
        translation_input = self._artifact_data(self._prior(context, "translation-input"))["translation_input"]
        preclean = self._artifact_data(self._prior(context, "preclean"))["preclean"]
        provider_mode = str(self.task.translation.get("provider_mode") or "fake").lower()
        provider = self.translation_provider
        if provider is None and provider_mode == "fake":
            provider = FakeTranslationProvider()
        if provider is None:
            raise ProductionWorkflowError("QWEN_PROVIDER_UNCONFIGURED")
        if provider_mode == "qwen-mt":
            from ..translation.budget import BudgetedProvider
            if not isinstance(provider, BudgetedProvider):
                raise ProductionWorkflowError("QWEN_PROVIDER_MUST_BE_BUDGETED")
        dictionary = self._artifact_data(self._prior(context, "dictionary"))
        dictionary_manifest = dictionary.get("dictionary_manifest") or {}
        service = TranslationService(provider, TranslationCache(self.work / "translation_cache.json"),
                                     schema_version=TRANSLATION_SCHEMA_VERSION,
                                     dictionary_manifest=dictionary_manifest)
        translated = service.translate_records(records_for_preclean(translation_input))
        state = build_production_state(translation_input, preclean.get("translation_input_records") or [],
                                       translated.get("records") or {})
        return self._store("translation", {"status": "READY", "execution": translated, "state": state,
            "provider_provenance": {"provider": provider.name, "model": provider.model,
                                    "verified": provider_mode == "qwen-mt",
                                    "request_count": int((translated.get("summary") or {}).get("total", 0))},
            "input_artifact_hashes": {"preclean": self._prior(context, "preclean").get("artifact_file_hash"),
                                        "dictionary": self._prior(context, "dictionary").get("artifact_file_hash")},
            "counts": dict(translated.get("summary") or {})})

    @staticmethod
    def _field_type(field: Mapping[str, Any]) -> str:
        name = str(field.get("field") or "")
        if name in {"category_l1", "category_l2", "category_l3", "leaf_category"}:
            return "category"
        if name == "selected_variation_raw":
            return "packaging"
        return "technical"

    @staticmethod
    def _translation_map(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(row.get("asin") or "").upper(): {
            "asin": row.get("asin"), "source_record_hash": row.get("source_record_hash"),
            "fields": {field.get("target_field"): deepcopy(field) for field in row.get("fields") or []}
        } for row in state.get("records") or [] if row.get("asin")}

    @staticmethod
    def _replace_state_fields(state: Mapping[str, Any], changed: Mapping[str, Any]) -> dict[str, Any]:
        """Merge only rerender/repair envelopes; source and good fields stay immutable."""
        result = deepcopy(dict(state))
        by_asin = {str(row.get("asin") or "").upper(): row for row in result.get("records") or []}
        for asin, record in changed.items():
            target = by_asin.get(str(asin).upper())
            if not target:
                continue
            by_target = {field.get("target_field"): field for field in target.get("fields") or []}
            for target_field, envelope in (record.get("fields") or {}).items():
                current = by_target.get(target_field)
                if not isinstance(current, Mapping) or not isinstance(envelope, Mapping):
                    continue
                if envelope.get("rerender_status") == "READY" or envelope.get("repair_status") == "READY":
                    current.update(deepcopy(dict(envelope)))
                    current["final_zh"] = current.get("translated_text") or current.get("final_zh")
                    current["promotion_status"] = "PROMOTED"
        result["release_candidate"] = build_release_translation_candidate(result)
        by_asin = {str(row.get("asin") or "").upper(): row
                   for row in result["release_candidate"].get("records") or []}
        for record in result.get("records") or []:
            candidate = by_asin.get(str(record.get("asin") or "").upper(), {})
            record["release_status"] = candidate.get("release_status", "BLOCKED")
        return result

    def _dictionary_evidence(self, state: Mapping[str, Any], *, dictionary_version: str) -> tuple[list[dict], dict[str, dict]]:
        from ..quality.chinese import audit_field
        evidence, qa_results = [], {}
        for record in state.get("records") or []:
            source_row = record.get("source_record") or {}
            category = (source_row.get("leaf_category") or source_row.get("category_l3")
                        or source_row.get("category_l2") or source_row.get("category_l1"))
            for field in record.get("fields") or []:
                target = str(field.get("final_zh") or "")
                source = str(field.get("source_text") or "")
                if not target or not source or field.get("promotion_status") != "PROMOTED":
                    continue
                field_type = self._field_type(field)
                context = {"category": str(category or ""), "field": str(field.get("target_field") or "")}
                context_key, reason = normalize_context(context)
                evidence_id = "%s:%s" % (record.get("asin"), field.get("target_field"))
                qa = audit_field(asin=str(record.get("asin") or ""), field=str(field.get("field") or ""),
                                 source_es=source, translated_zh=target, source_hash=str(field.get("source_hash") or ""),
                                 dictionary_version=dictionary_version,
                                 brand=str(source_row.get("brand") or ""),
                                 target_field=str(field.get("target_field") or ""),
                                 field_type=str(field.get("target_field") or ""), context=context,
                                 translation_schema_version=TRANSLATION_SCHEMA_VERSION)
                evidence_row = {"evidence_id": evidence_id, "asin": record.get("asin"),
                    "source_record_hash": record.get("source_record_hash"), "source": source, "target": target,
                    "source_hash": field.get("source_hash"), "field_type": field_type, "context": context,
                    "affected_field": field.get("field"), "target_field": field.get("target_field")}
                evidence.append(evidence_row)
                qa_results[evidence_id] = {**qa, "qa_status": qa.get("status"), "target": target,
                    "field_type": field_type, "context_key": context_key or "", "dictionary_version": dictionary_version,
                    "schema_version": TRANSLATION_SCHEMA_VERSION, "context_error": reason}
        return evidence, qa_results

    def stage_dictionary_rerender(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        master = self._translation_master(context); translation = self._artifact_data(self._prior(context, "translation"))
        dictionary = self._artifact_data(self._prior(context, "dictionary"))
        prior_manifest = dictionary["dictionary_manifest"]
        state = translation["state"]
        evidence, qa_results = self._dictionary_evidence(state, dictionary_version=str(prior_manifest.get("dictionary_version") or "0"))
        sync = sync_evidence(evidence, qa_results=qa_results, source_run_id=self.run_id,
                             translation_schema_version=TRANSLATION_SCHEMA_VERSION, previous=prior_manifest)
        records = self._translation_map(state)
        def rerender_qa(**kwargs):
            from ..quality.chinese import audit_field
            result = audit_field(asin=kwargs["asin"], field=kwargs["field"], source_es=kwargs["source_text"],
                                 translated_zh=kwargs["translated_text"], source_hash=kwargs["source_hash"],
                                 dictionary_version=kwargs["dictionary_version"],
                                 target_field=kwargs["target_field"], field_type=kwargs["target_field"],
                                 context={"context_key": kwargs["context_key"], "field": kwargs["field"],
                                          "target_field": kwargs["target_field"]},
                                 translation_schema_version=kwargs["schema_version"])
            return {**result, "target": kwargs["translated_text"], "context_key": kwargs["context_key"]}
        rerender = rerender_affected_fields(master.get("records") or [], records, sync.get("manifest") or {},
                                            qa_callback=rerender_qa)
        updated_state = self._replace_state_fields(state, rerender.get("records") or {})
        return self._store("dictionary-rerender", {"status": "READY", "dictionary_sync": sync,
            "dictionary_qa": qa_results, "rerender": rerender, "translation_state": updated_state,
            "input_artifact_hashes": {"translation": self._prior(context, "translation").get("artifact_file_hash"),
                                        "dictionary": self._prior(context, "dictionary").get("artifact_file_hash")},
            "counts": {"evidence": len(evidence), "updates": len(rerender.get("updates") or []),
                       "repair": len(rerender.get("selective_repair") or []),
                       **dict(sync.get("counts") or {})}})

    def _chinese_qa(self, context: Mapping[str, Any], state: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        from ..quality.chinese import audit_field
        if state is None:
            state = self._artifact_data(self._prior(context, "dictionary-rerender")).get("translation_state")
        if not isinstance(state, Mapping):
            state = self._artifact_data(self._prior(context, "translation"))["state"]
        rows = []
        for record in state.get("records") or []:
            for field in record.get("fields") or []:
                rows.append(audit_field(asin=record.get("asin", ""), field=field.get("field", ""),
                    source_es=field.get("source_text", ""), translated_zh=field.get("final_zh") or "",
                    source_hash=field.get("source_hash", ""), dictionary_version=str(field.get("dictionary_version") or ""),
                    target_field=str(field.get("target_field") or ""), field_type=str(field.get("target_field") or ""),
                    context=field.get("context") if isinstance(field.get("context"), Mapping) else None,
                    translation_schema_version=str(field.get("translation_schema_version") or field.get("schema_version") or TRANSLATION_SCHEMA_VERSION)))
        return rows

    def stage_chinese_qa(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        state = self._artifact_data(self._prior(context, "dictionary-rerender")).get("translation_state")
        rows = self._chinese_qa(context, state)
        return self._store("chinese-qa", {"status": "READY", "fields": rows, "translation_state": state,
            "input_artifact_hashes": {"dictionary-rerender": self._prior(context, "dictionary-rerender").get("artifact_file_hash")},
            "counts": {"fields": len(rows), "passed": sum(r.get("status") == "PASS" for r in rows)}})

    def stage_field_repair(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        from ..commands.translation import FakeTranslationProvider
        from ..translation.budget import BudgetedProvider

        qa_stage = self._artifact_data(self._prior(context, "chinese-qa"))
        state = qa_stage.get("translation_state") or {}
        qa_rows = qa_stage.get("fields") or []
        queue = build_repair_queue(qa_rows, max_attempts=2)
        field_lookup = {(str(record.get("asin") or ""), str(field.get("field") or "")): field
                        for record in state.get("records") or [] for field in record.get("fields") or []}
        results, changed = [], self._translation_map(state)
        provider_mode = str(self.task.translation.get("provider_mode") or "fake").lower()
        provider = self.translation_provider
        if provider is None and provider_mode == "fake":
            provider = FakeTranslationProvider()
        if provider_mode == "qwen-mt" and not isinstance(provider, BudgetedProvider):
            raise ProductionWorkflowError("QWEN_PROVIDER_MUST_BE_BUDGETED")
        for item in queue:
            field = field_lookup.get((str(item.get("asin") or ""), str(item.get("field") or "")))
            if not field or item.get("strategy") == "manual_review":
                results.append({**item, "status": "MANUAL_REVIEW", "code": "NO_SAFE_AUTOMATIC_REPAIR"}); continue
            # Dictionary rerender happened as a real preceding stage.  If its
            # canonical QA did not accept a replacement, a provider must not
            # guess over that evidence mismatch.
            if item.get("strategy") == "dictionary_rerender":
                results.append({**item, "status": "MANUAL_REVIEW", "code": "RERENDER_QA_NOT_PASS"}); continue
            if provider is None:
                raise ProductionWorkflowError("REPAIR_PROVIDER_UNCONFIGURED")
            current = dict(item)
            successful = None
            while int(current.get("attempt") or 0) < int(current.get("max_attempts") or 0):
                attempt_number = int(current.get("attempt") or 0) + 1
                response = provider.translate(
                    str(field.get("source_text") or ""), asin=str(item.get("asin") or ""),
                    field=str(item.get("field") or ""),
                    context={"repair": True,
                             "budget_request_id": "repair:%s:%s:%s:%s:%d" % (
                                 self.run_id, item.get("asin"), item.get("field"),
                                 field.get("source_hash"), attempt_number)},
                )
                outcome = apply_repair(
                    current, source_hash=str(field.get("source_hash") or ""),
                    candidate=str(response.text or ""),
                    qa_result=next((row for row in qa_rows if row.get("asin") == item.get("asin")
                                    and row.get("field") == item.get("field")), {}),
                    provider=provider.name, model=provider.model,
                )
                if response.status != "success":
                    outcome.update(status="MANUAL_REVIEW", code="PROVIDER_RETRY_FAILED",
                                   provider_error=str(response.error or ""))
                results.append(outcome)
                if outcome.get("status") == "PASS":
                    successful = outcome
                    break
                current = {**outcome, "status": "PENDING"}
            if successful is not None:
                envelope = changed.get(str(item.get("asin") or "").upper(), {}).get("fields", {}).get(field.get("target_field"))
                if envelope is not None:
                    envelope.update(translated_text=successful.get("repaired_translation"),
                                    candidate_text=successful.get("repaired_translation"),
                                    final_zh=successful.get("repaired_translation"), repair_status="READY")
        updated_state = self._replace_state_fields(state, changed)
        return self._store("field-repair", {"status": "READY", "repair_queue": queue, "repair_results": results,
            "translation_state": updated_state,
            "input_artifact_hashes": {"chinese-qa": self._prior(context, "chinese-qa").get("artifact_file_hash")},
            "counts": {"queued": len(queue), "attempted": sum(row.get("attempt", 0) > 0 for row in results),
                       "repaired": sum(row.get("status") == "PASS" for row in results)}})

    def stage_re_qa(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        state = self._artifact_data(self._prior(context, "field-repair")).get("translation_state")
        rows = self._chinese_qa(context, state)
        return self._store("re-qa", {"status": "READY", "fields": rows, "translation_state": state,
            "input_artifact_hashes": {"field-repair": self._prior(context, "field-repair").get("artifact_file_hash")},
            "counts": {"fields": len(rows), "passed": sum(r.get("status") == "PASS" for r in rows)}})

    def stage_field_closure(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        from ..quality.field_closure import audit_field_closure_quality
        master = self._translation_master(context); _manifest, rankings = self._snapshot_bundle(context)
        selected = {str(row.get("asin") or "").upper() for row in master.get("records") or []}
        rankings = [row for row in rankings if str(row.get("asin") or "").upper() in selected]
        details = [row for row in self.history.load_details() if str(row.get("asin") or "").upper() in selected]
        report = audit_field_closure_quality(master.get("records"), details, rankings)
        closure = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        return self._store("field-closure", {"status": "READY", "field_closure": closure,
            "input_artifact_hashes": {"re-qa": self._prior(context, "re-qa").get("artifact_file_hash")},
            "counts": dict(closure.get("summary") or {})})

    def stage_release(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Build ReleaseGate input only from earlier producer artifacts.

        ``ProductionRun`` invokes the sole formal gate.  Under the offline
        fake provider that gate must (and does) reject this artifact; no stage
        ever supplies a caller-authored PASS decision.
        """
        from ..production.release import seal_artifact
        from ..quality.chinese_gate import evaluate_chinese_gate

        ranking = self._artifact_data(self._prior(context, "ranking-authority"))
        details = self._artifact_data(self._prior(context, "offline-reparse"))
        translation_input = self._artifact_data(self._prior(context, "translation-input"))
        master = self._translation_master(context)
        translation = self._artifact_data(self._prior(context, "translation"))
        rerender = self._artifact_data(self._prior(context, "dictionary-rerender"))
        qa = self._artifact_data(self._prior(context, "re-qa"))
        final_state = qa.get("translation_state") or rerender.get("translation_state") or translation["state"]
        closure = self._artifact_data(self._prior(context, "field-closure"))
        # Release may verify reports but must never manufacture a PASS from a
        # successful detail command or a snapshot flag.  Each owner must emit
        # its own hash-bound report; missing evidence remains an explicit
        # blocked artifact for the formal gate.
        def missing_report(check: str) -> dict[str, Any]:
            return {"check": check, "status": "BLOCK", "produced_stage": "",
                    "issues": [{"code": "UPSTREAM_STAGE_REPORT_MISSING"}]}
        report_source = self._artifact_data(self._prior(context, "spanish-master")).get("stage_reports") or {}
        authority_report = report_source.get("ranking_authority") or missing_report("ranking_authority")
        detail_report = report_source.get("detail_identity") or missing_report("detail_identity")
        replay_report = report_source.get("offline_replay") or missing_report("offline_replay")
        final_state = deepcopy(final_state)
        final_state["release_candidate"] = build_release_translation_candidate(final_state)
        chinese_rows = []
        translations_by_asin = {str(row.get("asin") or "").upper(): row
                                for row in final_state.get("records") or []}
        for source_row in master.get("records") or []:
            row = dict(source_row)
            translated = translations_by_asin.get(str(row.get("asin") or "").upper(), {})
            for field in translated.get("fields") or []:
                if field.get("final_zh"):
                    row[field.get("target_field")] = field["final_zh"]
            chinese_rows.append(row)
        artifacts = {
            "spanish_master": master,
            "ranking_authority": seal_artifact("ranking_authority", authority_report),
            "source_audit": seal_artifact("source_audit", translation_input["translation_batch_source_audit"]),
            "source_gate": seal_artifact("source_gate", translation_input["translation_batch_source_gate"]),
            "detail_identity": seal_artifact("detail_identity", detail_report),
            "offline_replay": seal_artifact("offline_replay", replay_report),
            "field_closure": seal_artifact("field_closure", closure["field_closure"]),
            "translation": seal_artifact("translation", {"state": final_state,
                "execution": translation["execution"], "provider_provenance": translation["provider_provenance"]}),
            "dictionary_sync": seal_artifact("dictionary_sync", {"completed": True,
                "manifest": rerender["dictionary_sync"].get("manifest") or {}, "rerender": rerender["rerender"]}),
            "chinese_qa": seal_artifact("chinese_qa", {"fields": qa["fields"]}),
            "chinese_gate": seal_artifact("chinese_gate", evaluate_chinese_gate(qa["fields"])),
            "spanish_output": seal_artifact("spanish_output", {"records": master.get("records") or []}),
            "chinese_output": seal_artifact("chinese_output", {"records": chinese_rows}),
        }
        return self._store("release", {"status": "READY", "artifacts": artifacts,
            "input_artifact_hashes": {"field-closure": self._prior(context, "field-closure").get("artifact_file_hash"),
                                        "translation-input": self._prior(context, "translation-input").get("artifact_file_hash"),
                                        "spanish-master": self._prior(context, "spanish-master").get("artifact_file_hash")},
            "translation_batch": translation_input.get("selection_manifest"),
            "counts": {"records": len(chinese_rows), "selected_asins": len(chinese_rows)}})

    def stage_excel(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """The only producer path to the frozen three-sheet Excel exporter."""
        from ..export.excel import export_workbook
        from ..production.release import export_ready
        release = self._artifact_data(self._prior(context, "release"))
        output = self.run_dir / "output" / "selection.xlsx"
        result = export_ready(release["artifacts"],
                              lambda rows, *, output_path: export_workbook(rows, out_path=output_path),
                              str(output))
        return self._store("excel", {"status": "READY", "output": str(output.relative_to(self.run_dir)),
            "export_result": str(result.get("export_result") or ""),
            "input_artifact_hashes": {"release": self._prior(context, "release").get("artifact_file_hash")},
            "counts": {"workbooks": 1}})


__all__ = ["ProductionWorkflow", "ProductionWorkflowError", "RankingSnapshotCollector",
           "ExistingV1SnapshotCollector", "ExistingV1DetailCollector"]
