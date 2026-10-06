"""Reviewed collection scheduler with explicit ranking and frozen-detail phases."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
import json
import re
import threading
import time
from typing import Any, Callable, Mapping

from ..collection.quota import QuotaError, select_research_quota
from ..monitoring.snapshot import build_ranking_snapshot
from .checkpoint import TaskCheckpointRepository, read_json, write_json_atomic
from .detail_reconciliation import (NETWORK_ACTIONS, build_reconciliation,
                                     load_detail_records, write_reconciliation)
from .manifest import merge_records, write_summary
from .phases import (PHASE_SCHEMA_VERSION, asins, canonical_json_sha256, git_head,
                     require_phase)
from .plan import cooldown_seconds, validate_task_plan
from .state import TaskRuntimeState
from .worker import run_category_live


def _read_json(path: Path, default: Any) -> Any:
    try:
        return read_json(path, default)
    except (OSError, ValueError):
        return default


def _source_counts(rankings: list[Mapping]) -> dict[str, int]:
    values = {"primary": 0, "reserve": 0}
    used = {(str(row.get("ranking_source_url") or ""), str(row.get("source_role") or ""))
            for row in rankings if isinstance(row, Mapping)}
    for _url, role in used:
        if role in values:
            values[role] += 1
    return values


def _synthesized_page_statuses(rankings: list[Mapping]) -> list[dict]:
    """Compatibility status evidence for injected/offline workers only."""
    values: dict[tuple[str, int], dict] = {}
    for row in rankings:
        if not isinstance(row, Mapping):
            continue
        url = str(row.get("ranking_source_url") or "")
        try:
            page = int(row.get("ranking_page_number") or 1)
        except (TypeError, ValueError):
            page = 1
        if not url:
            continue
        status = values.setdefault((url, page), {
            "source_url": url, "page_number": page, "page_url": url,
            "access_state": "NORMAL", "http_status": 200,
            "parse_status": "PARSE_OK", "parsed_record_count": 0, "error": "",
        })
        status["parsed_record_count"] += 1
    return list(values.values())


def _candidate_rows(selected: Mapping[str, list[dict]], rankings: list[Mapping]) -> list[dict]:
    contexts: dict[str, list[dict]] = defaultdict(list)
    seen_contexts: dict[str, set[str]] = defaultdict(set)
    for raw in rankings:
        if not isinstance(raw, Mapping):
            continue
        asin = str(raw.get("asin") or "").strip().upper()
        if not asin:
            continue
        item = dict(raw)
        token = canonical_json_sha256(item)
        if token not in seen_contexts[asin]:
            seen_contexts[asin].add(token)
            contexts[asin].append(item)
    rows: list[dict] = []
    for group in sorted(selected):
        for raw in selected[group]:
            row = dict(raw)
            asin = str(row.get("asin") or "").strip().upper()
            row["asin"] = asin
            row["ranking_contexts"] = contexts.get(asin, [dict(raw)])
            rows.append(row)
    return rows


def _candidate_groups(rows: list[Mapping]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        group = str(row.get("research_category") or "").strip()
        asin = str(row.get("asin") or "").strip().upper()
        if group and asin:
            values[group].append(asin)
    return {group: list(dict.fromkeys(items)) for group, items in values.items()}


def _candidate_row_groups(rows: list[Mapping]) -> dict[str, list[dict]]:
    """Preserve frozen ranking URL evidence for the detail request contract."""
    values: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        group = str(raw.get("research_category") or "").strip()
        asin = str(raw.get("asin") or "").strip().upper()
        key = (group, asin)
        if group and asin and key not in seen:
            seen.add(key)
            values[group].append(dict(raw))
    return dict(values)


def _detail_reuse_groups(plan: Mapping, candidates: list[Mapping]) -> tuple[dict[str, list[dict]],
                                                                            dict[str, list[str]],
                                                                            dict[str, list[str]]]:
    """Split a persisted detail reconciliation by research category."""
    records = {str(row.get("ranking_asin") or row.get("asin") or "").strip().upper(): dict(row)
               for row in (plan.get("records") or []) if isinstance(row, Mapping)}
    reuse: dict[str, list[dict]] = defaultdict(list)
    fetch: dict[str, list[str]] = defaultdict(list)
    blocked: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        asin = str(candidate.get("asin") or "").strip().upper()
        group = str(candidate.get("research_category") or "").strip()
        item = records.get(asin)
        action = str((item or {}).get("detail_action") or "FETCH_NEW")
        if action == "REUSE_VALID_CACHE":
            cached = (item or {}).get("existing_detail_record")
            if isinstance(cached, Mapping):
                reuse[group].append(dict(cached))
            else:
                # A reuse action without embedded evidence is unsafe; treat it
                # as a network action instead of silently declaring success.
                fetch[group].append(asin)
        elif action in NETWORK_ACTIONS:
            fetch[group].append(asin)
        else:
            blocked[group].append(asin)
    return dict(reuse), dict(fetch), dict(blocked)


def _write_frozen_candidates(output: Path, rows: list[dict]) -> str:
    """Write once, or verify exactly the existing immutable candidate set."""
    path = output / "candidate_manifest.json"
    digest = canonical_json_sha256(rows)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if canonical_json_sha256(existing) != digest:
            raise ValueError("CANDIDATE_MANIFEST_IMMUTABLE")
    else:
        write_json_atomic(path, rows)
    sidecar = output / "candidate_manifest.sha256"
    if sidecar.exists():
        if sidecar.read_text(encoding="utf-8").strip() != digest:
            raise ValueError("CANDIDATE_FINGERPRINT_MISMATCH")
    else:
        sidecar.write_text(digest + "\n", encoding="utf-8")
    return digest


def _load_frozen_candidates(output: Path, target_unique: int) -> tuple[list[dict], str]:
    path = output / "candidate_manifest.json"
    sidecar = output / "candidate_manifest.sha256"
    if not path.is_file() or not sidecar.is_file():
        raise ValueError("DETAIL_REQUIRES_FROZEN_CANDIDATES")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("CANDIDATE_MANIFEST_INVALID")
    rows = [dict(row) for row in value if isinstance(row, Mapping)]
    digest = canonical_json_sha256(rows)
    if sidecar.read_text(encoding="utf-8").strip() != digest:
        raise ValueError("CANDIDATE_FINGERPRINT_MISMATCH")
    if len(rows) != int(target_unique) or len(asins(rows)) != int(target_unique):
        raise ValueError("CANDIDATE_QUOTA_NOT_MET")
    return rows, digest


def _snapshot_html(output: Path) -> tuple[dict[str, str], list[dict]]:
    """Copy ranking evidence with page-status order preserved for replay."""
    contents: dict[str, str] = {}
    statuses: list[dict] = []
    root = output / "categories"
    if not root.is_dir():
        return contents, statuses
    # Offline replay deliberately discovers ``ranking_*.html``.  Keep that
    # established evidence contract even though source runs live deeper in
    # category-specific directories.
    for index, path in enumerate(sorted(root.glob("**/runs/**/html/ranking_*.html")), start=1):
        name = "ranking_%05d.html" % index
        contents[name] = path.read_text(encoding="utf-8")
        match = re.fullmatch(r"ranking_(\d+)\.html", path.name)
        page_index = int(match.group(1)) if match else -1
        status_path = path.parent.parent / "page_statuses.json"
        try:
            raw_statuses = json.loads(status_path.read_text(encoding="utf-8"))
            status = dict(raw_statuses[page_index]) if isinstance(raw_statuses, list) else {}
        except (IndexError, OSError, ValueError, TypeError):
            status = {}
        status["html_file"] = name
        statuses.append(status)
    return contents, statuses


def _snapshot_path(output: Path, report: Mapping) -> Path | None:
    raw = str(report.get("ranking_snapshot_path") or "")
    path = output / raw if raw else None
    return path if path and path.is_dir() else None


def _freeze_ranking_snapshot(output: Path, plan: Mapping, *, plan_path: str | Path | None,
                             project_root: str | Path | None, rankings: list[dict],
                             statuses: list[dict], report: Mapping | None = None) -> dict:
    existing = _snapshot_path(output, report or {})
    if existing is not None and (existing / "manifest.json").is_file():
        return {"path": existing, "manifest": json.loads((existing / "manifest.json").read_text(encoding="utf-8"))}
    html_files, evidence_statuses = _snapshot_html(output)
    # A saved run's page-status order is the only order that offline replay
    # can safely pair with copied HTML.  Injected/offline test workers do not
    # have run directories, so preserve their explicit/synthesized evidence.
    used_statuses = (evidence_statuses if evidence_statuses and all(
        item.get("source_url") for item in evidence_statuses)
        else statuses or _synthesized_page_statuses(rankings))
    started = datetime.now().isoformat(timespec="seconds")
    result = build_ranking_snapshot(
        rankings, output / "ranking_snapshot", planned_sources=used_statuses,
        source_statuses=used_statuses,
        snapshot_id="ranking_%s" % str(plan["canonical_plan_sha256"])[:16],
        started_at=started, completed_at=datetime.now().isoformat(timespec="seconds"),
        parser_version="collection.ranking", html_files=html_files,
        publish_authoritative_pointer=True,
    )
    manifest = dict(result["manifest"])
    sources = _source_counts(rankings)
    manifest.update({
        "task_id": plan["task_id"], "batch_id": plan.get("batch_id", plan["task_id"]),
        "phase": "ranking", "code_head": git_head(project_root),
        "plan_path": str(plan_path or ""), "plan_sha256": plan["canonical_plan_sha256"],
        "target_unique": plan["target_unique"], "category_count": len(plan["categories"]),
        "primary_sources_used": sources["primary"], "reserve_sources_used": sources["reserve"],
        "ranking_pages_planned": len(used_statuses),
        "ranking_pages_completed": int(manifest.get("completed_page_count") or 0),
        "ranking_records": len(rankings),
        "unique_ranking_asins": len(asins(rankings)),
        "access_status": "NORMAL" if manifest.get("snapshot_status") == "AUTHORITATIVE" else "INCOMPLETE",
    })
    write_json_atomic(Path(result["path"]) / "manifest.json", manifest)
    return {"path": Path(result["path"]), "manifest": manifest}


def _run_workers(plan: Mapping, output: Path, runtime: TaskRuntimeState, *, phase: str,
                 mode: str, headful: bool, profile_dir: str, worker: Callable,
                 all_rankings: list[dict], all_details: list[dict]) -> tuple[bool, list[str], list[str]]:
    """Run only phase-eligible category work while retaining the access-stop gate."""
    scheduler = plan["scheduler"]
    categories = plan["categories"]
    category_map = {row["research_category"]: row for row in categories}
    complete = {"ranking": {"RANKING_COMPLETE", "COMPLETE"},
                "detail": {"DETAIL_COMPLETE", "COMPLETE"},
                "all": {"COMPLETE"}}[phase]
    force_reserve = phase in {"ranking", "all"} and runtime.raw.get("run_status") == "QUOTA_UNIQUE_SHORTFALL"
    execution_plan = {**plan, "collection_phase": phase, "force_reserve_sources": force_reserve}
    pending = [group for group in category_map
               if runtime.category_states.get(group, {}).get("status") not in complete
               or (force_reserve and runtime.has_unfinished_reserve(group, category_map[group]))]
    slots = 3 if mode == "parallel3" else 1
    runtime.save("RUNNING", [])
    stop_all = False
    stop_event = threading.Event()
    challenge_pause = threading.Event()
    last_cooldown_log = [0.0] * slots
    requested_asins: list[str] = []

    def run_one(group: str, slot: int):
        try:
            args = (category_map[group], execution_plan, output, slot + 1, headful, profile_dir,
                    runtime.claim_asins, runtime.release_asins,
                    set(runtime.completed_source_urls), stop_event)
            if execution_plan.get("manual_assist"):
                return worker(*args, challenge_pause=challenge_pause)
            return worker(*args)
        except Exception as exc:
            from ..access.detector import AccessStopError
            if isinstance(exc, AccessStopError):
                stop_event.set()
            raise

    with ThreadPoolExecutor(max_workers=slots, thread_name_prefix="amazon-es-category") as executor:
        futures: dict[Future, tuple[int, str]] = {}
        while pending or futures:
            now = time.time()
            for slot in range(slots):
                remaining = runtime.worker_ready_at[slot] - now
                if remaining > 0 and now - last_cooldown_log[slot] >= 10:
                    print("[任务][槽位%d] 冷却中，剩余 %.0f 秒" % (slot + 1, remaining))
                    last_cooldown_log[slot] = now
            for slot in range(slots):
                if stop_all or any(existing_slot == slot for existing_slot, _ in futures.values()):
                    continue
                if not pending or runtime.worker_ready_at[slot] > now:
                    continue
                group = pending.pop(0)
                previous = dict(runtime.category_states.get(group, {}))
                runtime.category_states[group] = {**previous, "status": "RUNNING", "worker": slot + 1,
                                                  "attempts": int(previous.get("attempts", 0)) + 1}
                futures[executor.submit(run_one, group, slot)] = (slot, group)
            runtime.save("ACCESS_STOP" if stop_all else "RUNNING", [group for _, group in futures.values()])
            if not futures:
                if pending:
                    delay = max(0.0, min(runtime.worker_ready_at) - time.time())
                    if delay:
                        time.sleep(min(delay, 1.0))
                    continue
                break
            done, _ = wait(list(futures), timeout=0.5, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                slot, group = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    from ..access.detector import AccessStopError
                    state = "ACCESS_STOP" if isinstance(exc, AccessStopError) else "FAILED"
                    runtime.category_states[group] = {**runtime.category_states.get(group, {}), "status": state,
                                                      "error": str(exc)}
                    if state == "ACCESS_STOP":
                        stop_all = True
                        stop_event.set()
                    elif runtime.category_states[group].get("attempts", 1) < 2:
                        runtime.category_states[group]["status"] = "PENDING"
                        pending.insert(0, group)
                    continue
                all_rankings.extend(result.get("rankings", []))
                all_details.extend(result.get("details", []))
                requested_asins.extend(result.get("detail_requested_asins", []))
                result_status = str(result.get("status") or "")
                state = {
                    **runtime.category_states.get(group, {}), "status": result_status,
                    "raw_ranking_records": result.get("raw_ranking_records", 0),
                    "unique_asins": result.get("unique_asins", 0),
                    "detail_records": result.get("detail_records", 0),
                    "pending_detail_asins": result.get("pending_detail_asins", []),
                    "source_status": result.get("source_status", {}),
                    "ranking_page_statuses": result.get("ranking_page_statuses", []),
                }
                if phase == "ranking":
                    state["ranking_source_status"] = result.get("source_status", {})
                if phase == "detail":
                    state["detail_phase_status"] = result_status
                runtime.category_states[group] = state
                runtime.completed_source_urls.extend(result.get("completed_source_urls", []))
                merge_records(output, all_rankings, all_details)
                runtime.worker_ready_at[slot] = time.time() + cooldown_seconds(mode, scheduler)
                if result_status not in complete:
                    if runtime.category_states[group].get("attempts", 1) < 2:
                        runtime.category_states[group]["status"] = "PENDING"
                        pending.append(group)
                print("[%s][槽位%d] %s完成，榜单%d条，详情%d条" %
                      (group, slot + 1, phase, result.get("raw_ranking_records", 0),
                       result.get("detail_records", 0)))
    merge_records(output, all_rankings, all_details)
    incomplete = [group for group in category_map
                  if runtime.category_states.get(group, {}).get("status") not in complete]
    return stop_all, incomplete, requested_asins


def _run_ranking(plan: Mapping, output: Path, runtime: TaskRuntimeState, *, mode: str,
                 headful: bool, profile_dir: str, plan_path: str | Path | None,
                 project_root: str | Path | None, worker: Callable,
                 previous_details: str | Path | None = None) -> dict:
    plan_hash = plan["canonical_plan_sha256"]
    previous_report = _read_json(output / "ranking_run_report.json", {})
    if (isinstance(previous_report, Mapping) and previous_report and
            str(previous_report.get("plan_sha256") or "") != plan_hash):
        raise ValueError("PLAN_FINGERPRINT_MISMATCH")
    runtime.bind_phase("ranking", plan_sha256=plan_hash, code_head=git_head(project_root))
    all_rankings = _read_json(output / "rankings.json", [])
    all_details = _read_json(output / "details.json", [])
    stop_all, incomplete, _requested = _run_workers(
        plan, output, runtime, phase="ranking", mode=mode, headful=headful,
        profile_dir=profile_dir, worker=worker, all_rankings=all_rankings, all_details=all_details)
    all_rankings = _read_json(output / "rankings.json", [])
    statuses = [status for value in runtime.category_states.values() if isinstance(value, Mapping)
                for status in (value.get("ranking_page_statuses") or []) if isinstance(status, Mapping)]
    report: dict[str, Any] = {
        "schema_version": PHASE_SCHEMA_VERSION, "task_id": plan["task_id"], "batch_id": plan.get("batch_id", plan["task_id"]),
        "phase": "ranking", "mode": mode, "code_head": git_head(project_root),
        "plan_path": str(plan_path or ""), "plan_sha256": plan_hash,
        "target_unique": plan["target_unique"], "ranking_records": len(all_rankings),
        "unique_ranking_asins": len(asins(all_rankings)), "categories_complete": len(plan["categories"]) - len(incomplete),
        "categories_shortfall": incomplete, "primary_sources_used": _source_counts(all_rankings)["primary"],
        "reserve_sources_used": _source_counts(all_rankings)["reserve"],
        "ranking_pages_completed": len(statuses), "access_stop": bool(stop_all),
        "status": "ACCESS_STOP" if stop_all else "INCOMPLETE" if incomplete else "PENDING_QUOTA",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    selected: dict[str, list[dict]] | None = None
    if not stop_all and not incomplete:
        try:
            selected = select_research_quota(all_rankings, plan["categories"], plan["target_unique"])
            candidates = _candidate_rows(selected, all_rankings)
            if (len(candidates) != int(plan["target_unique"]) or
                    len(asins(candidates)) != int(plan["target_unique"])):
                raise ValueError("CANDIDATE_QUOTA_NOT_MET")
            snapshot = _freeze_ranking_snapshot(output, plan, plan_path=plan_path,
                                                 project_root=project_root, rankings=all_rankings,
                                                 statuses=statuses, report=previous_report)
            report["ranking_snapshot_path"] = str(Path(snapshot["path"]).relative_to(output))
            report["snapshot_status"] = snapshot["manifest"].get("snapshot_status")
            if snapshot["manifest"].get("snapshot_status") != "AUTHORITATIVE":
                report["status"] = "RANKING_SNAPSHOT_INCOMPLETE"
            else:
                candidate_hash = _write_frozen_candidates(output, candidates)
                report.update({"candidate_count": len(asins(candidates)),
                               "candidate_manifest_sha256": candidate_hash,
                               "status": "COMPLETE"})
                if previous_details:
                    prior = load_detail_records(previous_details)
                    detail_plan = build_reconciliation(
                        candidates, prior,
                        snapshot_id=str(snapshot["manifest"].get("snapshot_id") or ""),
                    )
                    paths = write_reconciliation(
                        detail_plan, output,
                        previous_details_path=previous_details,
                    )
                    actions = dict((detail_plan.get("summary") or {}).get("actions") or {})
                    report.update({
                        "previous_details_path": str(previous_details),
                        "previous_detail_records": len(prior),
                        "detail_reconciliation_path": str(paths["reconciliation"].relative_to(output)),
                        "detail_plan_path": str(paths["json"].relative_to(output)),
                        "detail_reextract_queue_path": str(paths["reextract_queue"].relative_to(output)),
                        "detail_reuse_count": actions.get("REUSE_VALID_CACHE", 0),
                        "detail_reextract_count": sum(actions.get(action, 0) for action in NETWORK_ACTIONS),
                        "detail_manual_review_count": sum(
                            value for action, value in actions.items()
                            if action not in NETWORK_ACTIONS and action != "REUSE_VALID_CACHE"),
                    })
        except QuotaError:
            report["status"] = "QUOTA_UNIQUE_SHORTFALL"
        except ValueError:
            raise
    write_json_atomic(output / "ranking_run_report.json", report)
    write_json_atomic(output / "run_report.json", report)
    runtime.phase_status["ranking"] = report["status"]
    runtime.save(report["status"], [])
    write_summary(output, plan["categories"], all_rankings, _read_json(output / "details.json", []),
                  selected, runtime.category_states)
    return report


def _require_detail_gate(output: Path, plan: Mapping) -> tuple[list[dict], str, Mapping]:
    report_path = output / "ranking_run_report.json"
    if not report_path.is_file():
        raise ValueError("DETAIL_REQUIRES_RANKING_PHASE")
    ranking_report = json.loads(report_path.read_text(encoding="utf-8"))
    if str(ranking_report.get("plan_sha256") or "") != plan["canonical_plan_sha256"]:
        raise ValueError("PLAN_FINGERPRINT_MISMATCH")
    if ranking_report.get("status") != "COMPLETE":
        raise ValueError("DETAIL_REQUIRES_COMPLETE_RANKING")
    snapshot_path = _snapshot_path(output, ranking_report)
    if snapshot_path is None:
        raise ValueError("DETAIL_REQUIRES_AUTHORITATIVE_SNAPSHOT")
    manifest = json.loads((snapshot_path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("snapshot_status") != "AUTHORITATIVE":
        raise ValueError("DETAIL_REQUIRES_AUTHORITATIVE_SNAPSHOT")
    rows, digest = _load_frozen_candidates(output, int(plan["target_unique"]))
    if str(ranking_report.get("candidate_manifest_sha256") or "") != digest:
        raise ValueError("CANDIDATE_FINGERPRINT_MISMATCH")
    return rows, digest, ranking_report


def _run_detail(plan: Mapping, output: Path, runtime: TaskRuntimeState, *, mode: str,
                headful: bool, profile_dir: str, plan_path: str | Path | None,
                project_root: str | Path | None, worker: Callable) -> dict:
    candidates, candidate_hash, ranking_report = _require_detail_gate(output, plan)
    runtime.bind_phase("detail", plan_sha256=plan["canonical_plan_sha256"],
                       candidate_manifest_sha256=candidate_hash,
                       code_head=git_head(project_root))
    by_group = _candidate_groups(candidates)
    snapshot_id = ""
    snapshot_path = _snapshot_path(output, ranking_report)
    if snapshot_path is not None:
        snapshot_id = str(_read_json(snapshot_path / "manifest.json", {}).get("snapshot_id") or "")
    execution = {
        **plan,
        "frozen_candidates_by_category": by_group,
        "frozen_candidate_rows_by_category": _candidate_row_groups(candidates),
        "ranking_snapshot_id": snapshot_id,
    }
    detail_plan: dict[str, Any] = {}
    detail_plan_path = str(ranking_report.get("detail_plan_path") or "")
    if detail_plan_path:
        candidate_plan_path = output / detail_plan_path
        if candidate_plan_path.is_file():
            detail_plan = _read_json(candidate_plan_path, {})
    if detail_plan:
        reuse, fetch, blocked = _detail_reuse_groups(detail_plan, candidates)
        execution.update({
            "detail_reuse_records_by_category": reuse,
            "detail_fetch_asins_by_category": fetch,
            "detail_blocked_asins_by_category": blocked,
        })
    all_rankings = _read_json(output / "rankings.json", [])
    all_details = _read_json(output / "details.json", [])
    stop_all, incomplete, requested = _run_workers(
        execution, output, runtime, phase="detail", mode=mode, headful=headful,
        profile_dir=profile_dir, worker=worker, all_rankings=all_rankings, all_details=all_details)
    all_details = _read_json(output / "details.json", [])
    candidate_asins = asins(candidates)
    if not set(requested).issubset(candidate_asins):
        raise ValueError("DETAIL_ASIN_OUTSIDE_FROZEN_CANDIDATES")
    successful = asins(all_details) & candidate_asins
    pending = sorted(candidate_asins - successful)
    candidate_details = [row for row in all_details
                         if isinstance(row, Mapping) and str(row.get("asin") or "").upper() in candidate_asins]
    request_source_counts: dict[str, int] = defaultdict(int)
    identity_counts: dict[str, int] = defaultdict(int)
    for row in candidate_details:
        request_source_counts[str(row.get("request_source") or "ASIN_FALLBACK_LEGACY")] += 1
        if str(row.get("identity_event") or "").upper() == "NAVIGATION_IDENTITY_CHANGED":
            identity_counts["navigation_identity_changed"] += 1
        elif str(row.get("identity_status") or "").upper() in {"PARENT_ASIN_MATCH", "VARIATION_RELATED"}:
            identity_counts[str(row.get("identity_status") or "").lower()] += 1
        elif str(row.get("identity_status") or "").upper() in {"IDENTITY_UNCONFIRMED", "UNCONFIRMED"}:
            identity_counts["identity_unconfirmed"] += 1
        else:
            identity_counts["exact_identity_matches"] += 1
    status = "ACCESS_STOP" if stop_all else "DETAIL_INCOMPLETE" if (incomplete or pending) else "COMPLETE"
    selected = defaultdict(list)
    for row in candidates:
        selected[str(row.get("research_category") or "")].append(row)
    report = {
        "schema_version": PHASE_SCHEMA_VERSION, "task_id": plan["task_id"], "batch_id": plan.get("batch_id", plan["task_id"]),
        "phase": "detail", "mode": mode, "code_head": git_head(project_root),
        "plan_path": str(plan_path or ""), "plan_sha256": plan["canonical_plan_sha256"],
        "candidate_count": len(candidate_asins), "candidate_manifest_sha256": candidate_hash,
        "detail_success": len(successful), "detail_failed": len(pending), "detail_pending": pending,
        "outside_candidate_requests": 0, "access_stop": bool(stop_all),
        "request_source_counts": dict(sorted(request_source_counts.items())),
        "exact_identity_matches": identity_counts["exact_identity_matches"],
        "navigation_identity_changed": identity_counts["navigation_identity_changed"],
        "variation_related": identity_counts["variation_related"],
        "identity_unconfirmed": identity_counts["identity_unconfirmed"],
        "status": status, "categories_incomplete": incomplete,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if detail_plan:
        summary = dict(detail_plan.get("summary") or {})
        report.update({
            "detail_plan_path": detail_plan_path,
            "detail_reuse_count": summary.get("reuse_count", 0),
            "detail_reextract_count": summary.get("reextract_count", 0),
            "detail_manual_review_count": summary.get("manual_review_count", 0),
        })
    write_json_atomic(output / "detail_run_report.json", report)
    write_json_atomic(output / "run_report.json", report)
    write_json_atomic(output / "final_manifest.json", candidates if status == "COMPLETE" else [])
    runtime.phase_status["detail"] = status
    runtime.save(status, [])
    write_summary(output, plan["categories"], all_rankings, all_details, selected, runtime.category_states)
    return report


def _run_all(plan: Mapping, output: Path, runtime: TaskRuntimeState, *, mode: str,
             headful: bool, profile_dir: str, worker: Callable) -> dict:
    """Compatibility mode retained for old non-formal tasks and tests."""
    all_rankings = _read_json(output / "rankings.json", [])
    all_details = _read_json(output / "details.json", [])
    stop_all, incomplete, _requested = _run_workers(
        plan, output, runtime, phase="all", mode=mode, headful=headful,
        profile_dir=profile_dir, worker=worker, all_rankings=all_rankings, all_details=all_details)
    all_rankings = _read_json(output / "rankings.json", [])
    all_details = _read_json(output / "details.json", [])
    selected = None
    rows: list[dict] = []
    missing: list[str] = []
    status = "ACCESS_STOP" if stop_all else "INCOMPLETE"
    try:
        selected = select_research_quota(all_rankings, plan["categories"], plan["target_unique"])
        rows = [row for group in selected.values() for row in group]
        selected_asins = asins(rows)
        missing = sorted(selected_asins - asins(all_details))
        if stop_all:
            status = "ACCESS_STOP"
        elif missing:
            status = "DETAIL_INCOMPLETE"
        elif incomplete:
            status = "INCOMPLETE"
        else:
            status = "COMPLETE"
        write_json_atomic(output / "candidate_manifest.json", rows)
        write_json_atomic(output / "final_manifest.json", rows if status == "COMPLETE" else [])
    except QuotaError:
        write_json_atomic(output / "final_manifest.json", [])
        status = "QUOTA_UNIQUE_SHORTFALL" if not stop_all else status
    write_summary(output, plan["categories"], all_rankings, all_details, selected, runtime.category_states)
    report = {
        "schema_version": 1, "task_id": plan["task_id"], "mode": mode,
        "run_status": status, "target_unique": plan["target_unique"],
        "ranking_records": len(all_rankings), "unique_ranking_asins": len(asins(all_rankings)),
        "detail_records": len(asins(all_details)), "missing_detail_asins": missing,
        "final_unique_asins": len(asins(rows if status == "COMPLETE" else [])),
        "categories": runtime.category_states, "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json_atomic(output / "run_report.json", report)
    runtime.save(status, [])
    return report


def run_reviewed_task(plan: Mapping, out_dir: str, mode: str | None = None,
                      headful: bool = False, profile_dir: str = "",
                      plan_path: str | Path | None = None,
                      project_root: str | Path | None = None,
                      worker: Callable | None = None, phase: str = "all",
                      resume: bool = False,
                      runtime_overrides: Mapping | None = None,
                      previous_details: str | Path | None = None) -> dict:
    """Run one reviewed plan without widening its scheduling or access policy."""
    phase = require_phase(phase)
    plan = validate_task_plan(plan, plan_path=plan_path, project_root=project_root)
    runtime_overrides = dict(runtime_overrides or {})
    unsupported_overrides = set(runtime_overrides) - {
        "postal_code", "challenge_wait_seconds", "manual_assist",
    }
    if unsupported_overrides:
        raise ValueError("TASK_RUNTIME_OVERRIDE_INVALID: %s" % ", ".join(sorted(unsupported_overrides)))
    if previous_details and not Path(previous_details).expanduser().is_file():
        raise ValueError("PREVIOUS_DETAILS_NOT_FOUND: %s" % previous_details)
    plan = {**plan, **runtime_overrides}
    if plan["task_id"] == "amazon_es_bestseller_5500_202610" and phase == "all":
        raise ValueError("FORMAL_5500_REQUIRES_EXPLICIT_PHASE")
    if (plan["task_id"] == "amazon_es_bestseller_5500_202610" and phase == "detail"
            and not resume):
        raise ValueError("FORMAL_5500_DETAIL_REQUIRES_RESUME")
    scheduler = plan["scheduler"]
    mode = mode or scheduler["mode"]
    if mode not in {"parallel3", "serial"}:
        raise ValueError("mode 必须是 parallel3 或 serial")
    if mode == "parallel3" and profile_dir:
        raise ValueError("parallel3 不接受共享浏览器 profile_dir；请使用独立会话或串行模式")
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    repository = TaskCheckpointRepository(output)
    runtime = TaskRuntimeState.load(repository, plan, mode=mode, slots=3 if mode == "parallel3" else 1)
    worker = worker or run_category_live
    if phase == "ranking":
        return _run_ranking(plan, output, runtime, mode=mode, headful=headful,
                            profile_dir=profile_dir, plan_path=plan_path,
                            project_root=project_root, worker=worker,
                            previous_details=previous_details)
    if phase == "detail":
        return _run_detail(plan, output, runtime, mode=mode, headful=headful,
                           profile_dir=profile_dir, plan_path=plan_path,
                           project_root=project_root, worker=worker)
    return _run_all(plan, output, runtime, mode=mode, headful=headful,
                    profile_dir=profile_dir, worker=worker)


__all__ = ["run_reviewed_task"]
