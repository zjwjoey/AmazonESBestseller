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


def export_rows_report(*, observations, candidates, output_root, run_metadata=None,
                  metrics=None, pages=(), sources=(), qa=None, pending_journals=(),
                  pending_observations=(), pending_issues=()):
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
    complete_pages = sum(p.get("status") == "COMPLETE" or p.get("page_complete") is True for p in pages)
    summary["page_count"] = len(pages)
    summary["page_completeness_rate"] = complete_pages / len(pages) if pages else None
    summary["unknown_or_incomplete_pages"] = len(pages) - complete_pages
    _json(directory / "summary.json", summary)
    _json(directory / "capacity_model.json", estimate_capacity({**(metrics or {}), "raw_observations": len(observations),
          "unique_link_candidates": len(eligible_ids), "product_pages": sum(p.get("observation_count", 0) > 0 for p in pages)}))
    evidence = [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                for p in dict.fromkeys(Path(row["evidence_path"]) for row in [*observations, *pending_observations]
                                      if row.get("evidence_path")) if p.is_file()]
    _json(directory / "snapshot_manifest.json", {"pages": pages, "raw_evidence": evidence,
          "pending_unindexed_page_journals": list(pending_journals),
          "unindexed_raw_card_count": pending_count,
          "pending_journal_errors": list(pending_issues),
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
    _json(directory / "capacity_model.json", estimate_capacity({**finalized_metrics,
        "raw_observations": len(observations), "unique_link_candidates": len(eligible_ids),
        "product_pages": sum(p.get("raw_card_count", p.get("observation_count", 0)) > 0 for p in pages)}))
    (directory / "research_report.txt").write_text(
        "商品链接快照研究报告\n" + f"已索引原始观察：{len(observations)}\n未索引但保存的原始卡片：{pending_count}\n计入目标的有效去重链接候选：{len(eligible_ids)}\n"
        + "链接唯一不等于独立可购买 SKU；未知来源容量仍未验收。\n"
        + json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _json(directory / "report_manifest.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in directory.iterdir() if p.is_file()})
    return directory


def export_report(store, run_id, output_root, source_registry=None, metrics=None):
    loaded = store.load_run(run_id)
    run = loaded.get("run", {})
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
        metrics = summarize_metrics(events, raw_count=len(observations),
            valid_link_count=sum(bool(r.get("candidate_id")) for r in observations),
            unique_count=len(eligible_candidate_ids(candidates)), started_at=run.get("started_at") or run["created_at"],
            finished_at=run.get("ended_at") or run.get("completed_at"),
            target=run.get("target_unique_links", run.get("plan", {}).get("target_unique_links", 30000)))
    else:
        observations, candidates = list(store.iter_observations(run_id)), list(store.iter_candidates(run_id))
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
    directory = export_rows_report(observations=observations, candidates=candidates,
        output_root=output_root, run_metadata=run, metrics=metrics, pages=store.pages(run_id),
        sources=sources, qa=loaded.get("manifest", {}).get("qa", {}),
        pending_journals=pending, pending_observations=pending_rows, pending_issues=pending_errors)
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
