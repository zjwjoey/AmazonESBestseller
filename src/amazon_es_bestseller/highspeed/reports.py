"""Immutable research exports from the isolated link-candidate store."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import uuid
import time
from datetime import datetime, timezone

from ..multisource.contracts import RESEARCH_CATEGORIES, SOURCE_ROOTS
from .performance import estimate_capacity


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _rows(directory, name, rows):
    rows = list(rows)
    with (directory / (name + ".jsonl")).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (directory / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})


def group_statistics(observations, field, expected, eligible_ids=None, candidate_groups=None):
    grouped = defaultdict(list)
    for row in observations:
        grouped[row.get(field) or "UNMAPPED"].append(row)
    result = []
    for label in dict.fromkeys([*expected, *grouped]):
        rows = grouped[label]
        valid = [r for r in rows if r.get("candidate_id")]
        all_candidates = {r["candidate_id"] for r in valid}
        candidates = {candidate_groups[cid] for cid in all_candidates if cid in candidate_groups} if candidate_groups is not None else (
            all_candidates if eligible_ids is None else all_candidates & eligible_ids)
        result.append({field: label, "raw_observations": len(rows),
                       "valid_link_observations": len(valid), "unique_link_candidates": len(candidates),
                       "normalized_unique_url_count": len(all_candidates),
                       "duplicate_observations": len(valid) - len(all_candidates),
                       "invalid_or_missing_links": len(rows) - len(valid),
                       "coverage_status": "OBSERVED_NOT_CAPACITY_VERIFIED" if rows else "UNVERIFIED"})
    return result


def source_coverage(sources):
    from .sources import source_coverage as evidence_coverage
    return [{**cell, "sources": [s for s in sources if s.get("source_type") == cell["source_type"]
                                  and s.get("research_category") == cell["research_category"]]}
            for cell in evidence_coverage(sources)]


def _strict_identity_verified(candidate):
    """A label alone cannot establish independent purchasing identity."""
    if candidate.get("url_identity_status") != "VERIFIED_INDEPENDENT_PURCHASABLE":
        return False
    if candidate.get("review_evidence"):
        from .reviews import validate_destination_review
        evidence = candidate["review_evidence"]
        try:
            projection = validate_destination_review({"review_id": candidate.get("review_id"),
                "decision": "VERIFIED_INDEPENDENT_PURCHASABLE", "reviewed_by": candidate.get("reviewed_by"),
                "reviewed_at": candidate.get("reviewed_at"), "candidate_ids": [candidate["candidate_id"]],
                "evidence": [{"candidate_id": candidate["candidate_id"], "receipt_path": evidence.get("receipt_path"),
                              "receipt_sha256": evidence.get("receipt_sha256")}]}, [candidate])
            return candidate["candidate_id"] in projection
        except (ValueError, OSError, KeyError, TypeError):
            return False
    return False


def eligible_candidate_ids(candidates):
    """Do not let unknown parameters create unproven extra target slots."""
    result, seen = set(), set()
    for row in candidates:
        if row.get("target_eligible") is False:
            continue
        key = row.get("possible_duplicate_key") or row.get("normalized_product_url") or row["candidate_id"]
        if key in seen and not _strict_identity_verified(row):
            continue
        seen.add(key)
        result.add(row["candidate_id"])
    return result


def _continuation_metrics(metrics, run, observations, pages, requests, events, parent_started_at):
    seeds = [e for e in events if (e.get("kind") or e.get("event_type")) == "HISTORICAL_CONTINUATION_SEED"]
    if not seeds and not run.get("is_continuation"):
        return metrics
    result = dict(metrics)
    seed = int(seeds[-1]["seed_unique_links"]) if seeds and seeds[-1].get("seed_unique_links") is not None else None
    total = result["unique_link_candidates"]
    added = max(total - seed, 0) if seed is not None else None
    phase_start = run.get("task_started_at") or run.get("plan", {}).get("task_started_at") or result["started_at"]
    start = datetime.fromisoformat(phase_start.replace("Z", "+00:00"))
    sample_at = result.get("finished_at") or datetime.now(timezone.utc).isoformat()
    end = datetime.fromisoformat(sample_at.replace("Z", "+00:00"))
    wall = max((end - start).total_seconds(), 0)
    fresh = [r for r in observations if r.get("origin") == "LIVE_CAPTURE" and not r.get("is_derivation")]
    def within_phase(value):
        try:
            instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return instant.tzinfo is not None and start <= instant <= end
        except (ValueError, TypeError):
            return False
    live_pages = [p for p in pages if p.get("origin") == "LIVE_CAPTURE" and not p.get("is_derivation")
                  and p.get("raw_card_count", p.get("observation_count", 0)) > 0 and within_phase(p.get("observed_at"))]
    live_requests = [r for r in requests if r.get("kind", r.get("request_kind")) in {"PAGE", "ACP"}
                     and r.get("run_id", run["run_id"]) == run["run_id"]
                     and r.get("origin", run.get("origin")) == "LIVE_CAPTURE" and not r.get("is_derivation")]
    attributable = added is not None and added <= len(fresh) and fresh and live_requests
    rate = added / wall if attributable and wall > 0 else None
    remaining = max(result["target_unique_links"] - total, 0)
    cumulative_wall = None
    if parent_started_at:
        try:
            parent = datetime.fromisoformat(parent_started_at.replace("Z", "+00:00"))
            if parent.tzinfo is not None and parent <= start:
                cumulative_wall = (end - parent).total_seconds()
        except (ValueError, TypeError):
            pass
    result.update(is_continuation=True, inherited_unique_links=seed, cumulative_unique_links=total,
        phase_new_unique_links=added, started_at=phase_start, phase_started_at=phase_start,
        phase_wall_seconds=wall, phase_sample_observed_at=sample_at,
        parent_run_id=run.get("parent_run_id") or (seeds[-1].get("parent_run_id") if seeds else None),
        parent_started_at=parent_started_at, cumulative_wall_seconds_including_idle_gap=cumulative_wall,
        cumulative_timing_scope="PARENT_FIRST_START_THROUGH_SUCCESSOR_SAMPLE_INCLUDING_USER_IDLE_GAPS",
        raw_observations_by_origin=dict(Counter(r.get("origin", "UNKNOWN") for r in observations)),
        raw_observations_by_origin_and_derivation=[{"origin": origin, "is_derivation": derived, "raw_observations": count}
            for (origin, derived), count in Counter((r.get("origin", "UNKNOWN"), bool(r.get("is_derivation"))) for r in observations).items()],
        derived_raw_observations=sum(bool(r.get("is_derivation")) for r in observations),
        phase_live_raw_observations=len(fresh),
        phase_live_valid_link_observations=sum(bool(r.get("candidate_id")) for r in fresh),
        fresh_live_product_pages=len(live_pages), request_count=len(live_requests),
        acp_request_count=sum(r.get("kind", r.get("request_kind")) == "ACP" for r in live_requests),
        fresh_live_request_count=len(live_requests), unique_per_hour=rate * 3600 if rate is not None else None,
        unique_per_minute=rate * 60 if rate is not None else None,
        eta_remaining_seconds=remaining / rate if rate and remaining else (0 if not remaining else None),
        throughput_count_scope="SUCCESSOR_PHASE_NEW_UNIQUE_LINKS_EXCLUDING_HISTORICAL_SEED",
        eta_is_estimate=True, include_report_export_in_task_wall=False)
    if result.get("finished_at"):
        result["total_wall_seconds"] = wall
    result["phase_ten_hour_target_passed"] = bool(result.get("finished_at") and total >= result["target_unique_links"] and wall <= 36000)
    result["ten_hour_target_passed"] = bool(result.get("finished_at") and total >= result["target_unique_links"]
        and cumulative_wall is not None and cumulative_wall <= 36000)
    sample = {"raw_observations": len(fresh), "unique_link_candidates": added if attributable else 0,
              "product_pages": len(live_pages), "request_count": len(live_requests),
              "total_wall_seconds": wall if attributable else None, "origin": "LIVE_CAPTURE",
              "source_count": len({p["source_id"] for p in live_pages if p.get("source_id")}),
              "verified_source_capacity": None}
    model = estimate_capacity(sample, target=remaining)
    model.update(is_continuation=True, sample_scope="FRESH_SUCCESSOR_LIVE_CAPTURE_ONLY",
                 inherited_unique_links=seed, cumulative_unique_links=total, phase_new_unique_links=added,
                 fresh_live_product_pages=len(live_pages), fresh_live_request_count=len(live_requests),
                 remaining_cumulative_target=remaining)
    if not live_requests:
        model["estimated_total_seconds"] = None
        model["ten_hour_throughput_projection"] = None
    result["continuation_capacity_model"] = model
    return result


def _reviewed_title_rows(rows, projection, manifest_path):
    """Render centrally validated title evidence without replacing captured fields."""
    result = []
    for row in rows:
        review = projection.get(row.get("observation_id"))
        status = review["review_status"] if review else "PENDING_REVIEW"
        result.append({**row,
            "original_product_title_raw": row.get("product_title_raw"),
            "product_title_for_research": review.get("corrected_source_title")
                if review and status in {"CORRECTED", "UNCHANGED"} else None,
            "title_review_status": status,
            "title_review_reason": review.get("reason") if review else "OBSERVATION_NOT_IN_REVIEW_SNAPSHOT",
            "title_review_manifest": str(manifest_path),
            "title_review_evidence": review,
        })
    return result


def export_rows_report(*, observations, candidates, output_root, run_metadata=None,
                  metrics=None, pages=(), sources=(), qa=None, pending_journals=(),
                  pending_observations=(), pending_issues=(), title_review_metadata=None):
    """Create a new directory every time; never rewrite snapshots or formal Excel."""
    export_started = time.perf_counter()
    observations, candidates, pages, sources = map(list, (observations, candidates, pages, sources))
    eligible_ids = eligible_candidate_ids(candidates)
    candidates = [{**row, "target_eligible": row["candidate_id"] in eligible_ids} for row in candidates]
    pending_journals, pending_observations = list(pending_journals), list(pending_observations)
    pending_count = sum(p.get("raw_observations", 0) for p in pending_journals)
    candidate_groups = {row["candidate_id"]: row["candidate_id"] if _strict_identity_verified(row) else (
                        row.get("target_group_key") or row.get("possible_duplicate_key")
                        or row.get("normalized_product_url") or row["candidate_id"]) for row in candidates}
    directory = Path(output_root) / ("report_" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    duplicates = [r for r in observations if r.get("dedup_status") in {
        "EXACT_DUPLICATE", "NORMALIZED_DUPLICATE", "VERIFIED_SAME_DESTINATION"}]
    issues = [r for r in observations if r.get("url_identity_status") != "VERIFIED_INDEPENDENT_PURCHASABLE"
              or r.get("dedup_status") in {"POSSIBLE_DUPLICATE", "IDENTITY_UNCONFIRMED", "INVALID_PRODUCT_LINK"}]
    memberships = defaultdict(lambda: {"source_types": set(), "research_categories": set()})
    group_memberships = defaultdict(lambda: {"source_types": set(), "research_categories": set(), "candidate_ids": set()})
    for row in observations:
        if row.get("candidate_id"):
            memberships[row["candidate_id"]]["source_types"].add(row.get("source_type"))
            memberships[row["candidate_id"]]["research_categories"].add(row.get("research_category"))
            group = candidate_groups.get(row["candidate_id"], row["candidate_id"])
            group_memberships[group]["source_types"].add(row.get("source_type"))
            group_memberships[group]["research_categories"].add(row.get("research_category"))
            group_memberships[group]["candidate_ids"].add(row["candidate_id"])
    reuse = [{"candidate_id": key, **{name: sorted(v, key=lambda x: str(x)) for name, v in value.items()}}
             for key, value in memberships.items()]
    group_reuse = [{"target_group_key": key, **{name: sorted(v, key=lambda x: str(x)) for name, v in value.items()}}
                  for key, value in group_memberships.items()]
    list_stats = group_statistics(observations, "source_type", SOURCE_ROOTS, eligible_ids, candidate_groups)
    category_stats = group_statistics(observations, "research_category", RESEARCH_CATEGORIES, eligible_ids, candidate_groups)
    for field, rows in (("source_type", list_stats), ("research_category", category_stats)):
        scope_times = (metrics or {}).get("measured_stage_seconds_by_scope", {}).get(field, {})
        for row in rows:
            row["measured_stage_seconds"] = scope_times.get(row[field])
            row["timing_missing"] = row[field] not in scope_times
    for name, rows in (("observations", [*observations, *pending_observations]), ("unique_links", candidates),
                       ("unindexed_preserved_observations", pending_observations),
                       ("eligible_target_links", [row for row in candidates if row["target_eligible"]]),
                       ("duplicates", duplicates), ("identity_issues", issues),
                       ("five_list_statistics", list_stats),
                       ("fifteen_category_statistics", category_stats),
                       ("candidate_memberships", reuse), ("target_group_memberships", group_reuse),
                       ("source_coverage", source_coverage(sources))):
        _rows(directory, name, rows)
    status_counts = Counter(r.get("dedup_status", "UNKNOWN") for r in observations)
    summary = {"run": run_metadata or {}, "raw_observations": len(observations) + pending_count,
               "indexed_raw_observations": len(observations),
               "unindexed_preserved_raw_observations": pending_count,
               "pending_journal_errors": list(pending_issues),
               "raw_count_scope": "INDEXED_PLUS_PRESERVED_UNINDEXED_CARD_REPRESENTATIONS",
               "unique_link_candidates": len(eligible_ids), "normalized_unique_url_count": len(candidates),
               "possible_variant_links_not_counted_as_extra_target_slots": len(candidates) - len(eligible_ids),
               "dedup_status_counts": dict(status_counts),
               "independent_purchasable_skus_confirmed": sum(_strict_identity_verified(c) for c in candidates),
               "identity_warning": "Unique URLs are not proof of independent purchasable SKUs.",
               "qa": qa or {}, "performance": metrics or {},
               "url_unique_identity_unconfirmed_candidates": sum(not _strict_identity_verified(c) for c in candidates),
               "possible_duplicate_or_identity_conflict_candidates": sum(c.get("dedup_status") == "POSSIBLE_DUPLICATE"
                   or c.get("url_identity_status") == "IDENTITY_CONFLICT" for c in candidates),
               "cross_list_reused_candidates": sum(len(r["source_types"]) > 1 for r in group_reuse),
               "cross_category_reused_candidates": sum(len(r["research_categories"]) > 1 for r in group_reuse)}
    if title_review_metadata is not None:
        summary["title_review"] = title_review_metadata
    if (metrics or {}).get("is_continuation"):
        summary.update({name: metrics[name] for name in ("inherited_unique_links", "cumulative_unique_links",
            "phase_new_unique_links", "raw_observations_by_origin", "raw_observations_by_origin_and_derivation",
            "derived_raw_observations", "phase_live_raw_observations")})
    complete_pages = sum(p.get("status") == "COMPLETE" or p.get("page_complete") is True for p in pages)
    summary["page_count"] = len(pages)
    summary["page_completeness_rate"] = complete_pages / len(pages) if pages else None
    summary["unknown_or_incomplete_pages"] = len(pages) - complete_pages
    _json(directory / "summary.json", summary)
    _json(directory / "capacity_model.json", (metrics or {}).get("continuation_capacity_model") or estimate_capacity({**(metrics or {}), "raw_observations": len(observations),
          "unique_link_candidates": len(eligible_ids), "product_pages": sum(p.get("observation_count", 0) > 0 for p in pages)}))
    evidence = [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                for p in dict.fromkeys(Path(row["evidence_path"]) for row in [*observations, *pending_observations]
                                      if row.get("evidence_path")) if p.is_file()]
    _json(directory / "snapshot_manifest.json", {"pages": pages, "raw_evidence": evidence,
          "pending_unindexed_page_journals": list(pending_journals),
          "unindexed_raw_card_count": pending_count,
          "pending_journal_errors": list(pending_issues),
          **({"title_review": title_review_metadata} if title_review_metadata is not None else {}),
          "origin": (run_metadata or {}).get("origin", "UNKNOWN"), "original_evidence_modified": False})
    (directory / "research_report.txt").write_text(
        "商品链接快照研究报告\n" + f"已索引原始观察：{len(observations)}\n未索引但保存的原始卡片：{pending_count}\n计入目标的有效去重链接候选：{len(eligible_ids)}\n"
        + "链接唯一不等于独立可购买 SKU；未知来源容量仍未验收。\n"
        + json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # Finalize timings only after all bulky CSV/JSONL and evidence QA is saved.
    # This directory has not yet been returned/published; no prior report changes.
    finalized_metrics = dict(metrics or {})
    finalized_metrics["count_scope"] = "INDEXED_OBSERVATIONS_ONLY_UNINDEXED_RAW_REPORTED_SEPARATELY"
    finalized_metrics["report_serialization_seconds"] = time.perf_counter() - export_started
    if (finalized_metrics.get("started_at") and finalized_metrics.get("finished_at")
            and finalized_metrics.get("include_report_export_in_task_wall") is True):
        ended = datetime.now(timezone.utc)
        start = datetime.fromisoformat(finalized_metrics["started_at"].replace("Z", "+00:00"))
        finalized_metrics["pipeline_before_export_wall_seconds"] = finalized_metrics.get("total_wall_seconds")
        finalized_metrics["total_wall_seconds"] = (ended - start).total_seconds()
        finalized_metrics["finished_at"] = ended.isoformat().replace("+00:00", "Z")
        finalized_metrics["timing_scope"] = "FIRST_TASK_START_THROUGH_DATA_EXPORT_AND_BASIC_QA"
        wall = finalized_metrics["total_wall_seconds"]
        finalized_metrics["unique_per_hour"] = len(eligible_ids) * 3600 / wall if wall > 0 else None
        finalized_metrics["unique_per_minute"] = len(eligible_ids) * 60 / wall if wall > 0 else None
        finalized_metrics["ten_hour_target_passed"] = bool(finalized_metrics.get("ten_hour_target_passed")
            and len(eligible_ids) >= finalized_metrics.get("target_unique_links", 30000) and wall <= 36000)
        finalized_metrics["include_report_export_in_task_wall"] = False
    summary["performance"] = finalized_metrics
    _json(directory / "summary.json", summary)
    _json(directory / "performance.json", finalized_metrics)
    _json(directory / "capacity_model.json", finalized_metrics.get("continuation_capacity_model") or estimate_capacity({**finalized_metrics,
        "raw_observations": len(observations), "unique_link_candidates": len(eligible_ids),
        "product_pages": sum(p.get("raw_card_count", p.get("observation_count", 0)) > 0 for p in pages)}))
    (directory / "research_report.txt").write_text(
        "商品链接快照研究报告\n" + f"已索引原始观察：{len(observations)}\n未索引但保存的原始卡片：{pending_count}\n计入目标的有效去重链接候选：{len(eligible_ids)}\n"
        + "链接唯一不等于独立可购买 SKU；未知来源容量仍未验收。\n"
        + json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _json(directory / "report_manifest.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in directory.iterdir() if p.is_file()})
    return directory


def _user_pause_sample(run):
    if run.get("status") != "USER_PAUSED":
        return None
    value = run.get("user_paused_at")
    start = run.get("task_started_at") or run.get("plan", {}).get("task_started_at") or run.get("started_at") or run.get("created_at")
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        beginning = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        if instant.tzinfo is not None and beginning.tzinfo is not None and instant >= beginning:
            return str(value)
    except (ValueError, TypeError):
        pass
    return None


def export_report(store, run_id, output_root, source_registry=None, metrics=None, *, title_review_manifest=None):
    loaded = store.load_run(run_id)
    run = loaded.get("run", {})
    if run.get("plan", {}).get("snapshot_contract_version") == 3:
        from .v3_workflows import report_replay_v3
        return report_replay_v3(store.root, run_id, output_root)
    sources = source_registry or run.get("sources", [])
    if isinstance(sources, dict):
        sources = sources.get("sources", [])
    if metrics is None:
        from .performance import summarize_metrics
        observations = list(store.iter_observations(run_id))
        candidates = list(store.iter_candidates(run_id))
        events = []
        for event in store.events(run_id):
            event = dict(event)
            event.setdefault("stage", event.get("operation") or event.get("request_kind") or event.get("event_type") or event.get("kind", "unknown"))
            events.append(event)
        finished_at = None
        if run.get("status") == "USER_PAUSED":
            finished_at = _user_pause_sample(run)
        elif run.get("status") not in {"RUNNING", "CREATED", "NEW"}:
            start = datetime.fromisoformat((run.get("started_at") or run["created_at"]).replace("Z", "+00:00"))
            endings = []
            for name in ("finished_at", "completed_at", "ended_at"):
                value = run.get(name)
                if value:
                    try:
                        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                        if instant.tzinfo is not None and instant >= start:
                            endings.append((instant, str(value)))
                    except (ValueError, TypeError):
                        pass
            if endings:
                finished_at = max(endings, key=lambda item: item[0])[1]
        metrics = summarize_metrics(events, raw_count=len(observations),
            valid_link_count=sum(bool(r.get("candidate_id")) for r in observations),
            unique_count=len(eligible_candidate_ids(candidates)), started_at=run.get("started_at") or run["created_at"],
            finished_at=finished_at,
            target=run.get("target_unique_links", run.get("plan", {}).get("target_unique_links", 30000)))
    else:
        observations, candidates = list(store.iter_observations(run_id)), list(store.iter_candidates(run_id))
        if run.get("status") == "USER_PAUSED":
            metrics = dict(metrics)
            metrics["finished_at"] = _user_pause_sample(run)
            metrics["total_wall_seconds"] = None
            if metrics["finished_at"]:
                metrics["total_wall_seconds"] = (datetime.fromisoformat(metrics["finished_at"].replace("Z", "+00:00"))
                    - datetime.fromisoformat(metrics["started_at"].replace("Z", "+00:00"))).total_seconds()
    continuation_events = store.events(run_id)
    parent_id = run.get("parent_run_id") or next((e.get("parent_run_id") for e in continuation_events
        if (e.get("kind") or e.get("event_type")) == "HISTORICAL_CONTINUATION_SEED"), None)
    parent_start = None
    if parent_id and (run.get("is_continuation") or any((e.get("kind") or e.get("event_type")) == "HISTORICAL_CONTINUATION_SEED" for e in continuation_events)):
        try:
            parent_start = store.get_run(parent_id).get("started_at")
        except (ValueError, KeyError):
            pass
    metrics = _continuation_metrics(metrics, run, observations, store.pages(run_id),
        loaded.get("requests", []), continuation_events, parent_start)
    if run.get("status") == "USER_PAUSED":
        metrics = dict(metrics)
        pause = _user_pause_sample(run)
        metrics.update(finished_at=None, run_completed=False, user_paused_at=pause,
            sample_observed_at=pause, timing_endpoint_kind="USER_PAUSE" if pause else "UNKNOWN_USER_PAUSE",
            timing_scope="FIRST_TASK_START_THROUGH_USER_PAUSE_NOT_COMPLETION",
            eta_remaining_seconds=None, ten_hour_target_passed=False,
            include_report_export_in_task_wall=False,
            throughput_interpretation="HISTORICAL_OBSERVATIONS_UNTIL_USER_PAUSE_NOT_ACTIVE_GOAL_PROJECTION")
        if metrics.get("is_continuation"):
            metrics.update(phase_sample_observed_at=pause, phase_ten_hour_target_passed=False)
            metrics["continuation_capacity_model"] = {**metrics["continuation_capacity_model"],
                "run_completed": False, "user_paused": True, "estimated_total_seconds": None,
                "ten_hour_throughput_projection": None}
        if not pause:
            metrics.update(total_wall_seconds=None, unique_per_hour=None, unique_per_minute=None)
            if metrics.get("is_continuation"):
                metrics.update(phase_wall_seconds=None, cumulative_wall_seconds_including_idle_gap=None)
    from ..multisource.storage import read_sealed
    pending = loaded.get("manifest", {}).get("pending_page_journals", [])
    pending_rows, pending_errors = [], []
    for item in pending:
        try:
            value = read_sealed(item["journal_path"])
            item["journal_sha256"] = hashlib.sha256(Path(item["journal_path"]).read_bytes()).hexdigest()
            if value.get("run_id") != run_id:
                raise ValueError("PENDING_JOURNAL_RUN_MISMATCH")
            for row in value["observations"]:
                pending_rows.append({**row, "preserved_journal_path": item["journal_path"],
                    "index_status": "UNINDEXED_PRESERVED_RAW", "target_eligible": False})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            pending_errors.append({"journal_path": item.get("journal_path"), "error": str(exc)})
    title_metadata = None
    if title_review_manifest is not None:
        from .title_review import load_title_projection
        # All semantic and binding checks live in the central review module.
        # A corrupt manifest raises before creating any report directory.
        projection = load_title_projection(title_review_manifest, store, run_id)
        observations = _reviewed_title_rows(observations, projection, title_review_manifest)
        candidates = _reviewed_title_rows(candidates, projection, title_review_manifest)
        pending_rows = _reviewed_title_rows(pending_rows, projection, title_review_manifest)
        title_metadata = {"manifest_path": str(title_review_manifest),
            "manifest_sha256": hashlib.sha256(Path(title_review_manifest).read_bytes()).hexdigest(),
            "observation_status_counts": dict(Counter(r["title_review_status"] for r in observations)),
            "candidate_status_counts": dict(Counter(r["title_review_status"] for r in candidates)),
            "unindexed_status_counts": dict(Counter(r["title_review_status"] for r in pending_rows)),
            "original_titles_modified": False, "identity_counts_modified": False,
            "usable_title_field": "product_title_for_research",
            "unverified_title_policy": "NULL_RESEARCH_TITLE_KEEP_ORIGINAL_CAPTURED_TITLE"}
    directory = export_rows_report(observations=observations, candidates=candidates,
        output_root=output_root, run_metadata=run, metrics=metrics, pages=store.pages(run_id),
        sources=sources, qa=loaded.get("manifest", {}).get("qa", {}),
        pending_journals=pending, pending_observations=pending_rows, pending_issues=pending_errors,
        title_review_metadata=title_metadata)
    return {"report_directory": str(directory), "summary_path": str(directory / "summary.json"),
            "manifest_path": str(directory / "report_manifest.json")}


def daily_history(runs, *, window_days=28):
    """Research interface only; does not schedule or request network pages."""
    if window_days < 1:
        raise ValueError("Positive history window required")
    runs = sorted((r for r in runs if not r.get("is_derivation")), key=lambda r: (r["local_date"], r["run_id"]))
    origins = {r.get("origin", "UNKNOWN") for r in runs}
    dates = {origin: sorted({r["local_date"] for r in runs if r.get("origin", "UNKNOWN") == origin})[-window_days:]
             for origin in origins}
    seen = {origin: set() for origin in origins}
    day_seen = {origin: set() for origin in origins}
    previous_date = {origin: None for origin in origins}
    result = []
    for run in runs:
        origin = run.get("origin", "UNKNOWN")
        current = set(run.get("normalized_product_urls", []))
        if previous_date[origin] != run["local_date"]:
            seen[origin].update(day_seen[origin])
            day_seen[origin] = set()
            previous_date[origin] = run["local_date"]
        if run["local_date"] in dates[origin]:
            result.append({"run_id": run["run_id"], "local_date": run["local_date"],
                           "origin": origin, "unique_links": len(current),
                           "historical_first_seen": len(current - seen[origin] - day_seen[origin]),
                           "cross_day_reused": len(current & seen[origin]), "same_day_reused": len(current & day_seen[origin]),
                           "identity_scope": "LINK_LEVEL_ONLY"})
        day_seen[origin].update(current)
    return result
