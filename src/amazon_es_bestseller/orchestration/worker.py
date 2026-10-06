"""One-category worker for reviewed ranking and frozen-detail phases."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
import time
from typing import Callable, Mapping

from .checkpoint import TaskCheckpointRepository
from .plan import category_rank_filter, needs_reserve_sources, normalize_url, source_urls
from .state import CategoryRuntimeState
from ..collection.detail_request_plan import build_detail_request_plan


def _candidate_asins(plan: Mapping, group: str) -> list[str]:
    value = (plan.get("frozen_candidates_by_category") or {}).get(group, [])
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        asin = str(item.get("asin") if isinstance(item, Mapping) else item or "").strip().upper()
        if asin and asin not in seen:
            seen.add(asin)
            result.append(asin)
    return result


def _candidate_rows(plan: Mapping, group: str) -> dict[str, dict]:
    """Keep the immutable candidate row available to the detail collector.

    Older callers may supply ASIN-only frozen candidates.  Those deliberately
    remain supported and receive the safe ASIN fallback request plan.
    """
    values = (plan.get("frozen_candidate_rows_by_category") or {}).get(group, [])
    rows: dict[str, dict] = {}
    for item in values:
        if not isinstance(item, Mapping):
            continue
        asin = str(item.get("asin") or item.get("ranking_asin") or "").strip().upper()
        if asin and asin not in rows:
            rows[asin] = dict(item)
    for asin in _candidate_asins(plan, group):
        rows.setdefault(asin, {"asin": asin, "research_category": group})
    return rows


def _completed_detail_cache_records(category_dir: Path, candidate_asins: set[str]) -> list[dict]:
    """Recover collector checkpoints after an interrupted batch.

    ``collect_details`` persists every successful record before the batch-wide
    ``details.json`` write.  Treat those checkpoints as durable cache evidence
    so restarting a worker never revisits already successful product pages.
    Older records did not know ranking URL provenance and are labelled
    explicitly as legacy ASIN-fallback evidence rather than misrepresented.
    """
    result: list[dict] = []
    root = category_dir / "detail_cache" / "checkpoints"
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record = payload.get("record") if isinstance(payload, Mapping) else None
        asin = str((record or {}).get("asin") or payload.get("asin") or "").strip().upper()
        if (str(payload.get("status") or "").lower() != "success" or
                not isinstance(record, Mapping) or asin not in candidate_asins):
            continue
        recovered = dict(record)
        recovered.setdefault("request_source", "ASIN_FALLBACK_LEGACY")
        recovered.setdefault("detail_status", "SUCCESS")
        result.append(recovered)
    return result


def run_category_live(category: Mapping, plan: Mapping, output: Path,
                      worker_id: int, headful: bool, profile_dir: str,
                      claim_asins: Callable[[list[str]], list[str]],
                      release_asins: Callable[[list[str]], None],
                      completed_urls: set[str],
                      stop_event: threading.Event | None = None,
                      challenge_pause: threading.Event | None = None) -> dict:
    """Run exactly one serial category operation.

    ``ranking`` performs no detail navigation.  ``detail`` has no ranking URL
    loop and reads only the scheduler-supplied frozen candidate mapping.  The
    legacy ``all`` path remains available for pre-5500 callers.
    """
    from ..access.browser import BrowserSession
    from ..access.location import ensure_spain_delivery
    from ..collection.detail import collect_details
    from ..collection.ranking import collect_rankings

    phase = str(plan.get("collection_phase") or "all").lower()
    if phase not in {"ranking", "detail", "all"}:
        raise ValueError("TASK_COLLECTION_PHASE_INVALID")
    group = str(category["research_category"])
    category_dir = output / "categories" / group
    category_dir.mkdir(parents=True, exist_ok=True)
    runtime = CategoryRuntimeState(TaskCheckpointRepository(output), category_dir, group, worker_id)
    rankings = runtime.rankings
    detail_map = runtime.detail_map
    ranking_keys = {(row.get("ranking_source_url"), row.get("ranking_page_number"),
                    row.get("bestseller_rank"), str(row.get("asin") or "").upper())
                    for row in rankings if isinstance(row, Mapping)}
    batch = str(plan.get("batch_id") or plan.get("task_id"))
    collected_at = datetime.now().isoformat(timespec="seconds")
    completed_for_category = set(completed_urls) | {
        url for url, status in runtime.source_status.items() if status in {"ranked", "completed"}
    }
    pending_sources = source_urls(category, completed_for_category)
    pending_sources.sort(key=lambda source: 1 if str(source.get("role") or "primary").lower() == "reserve" else 0)

    with BrowserSession(headless=not headful, profile_dir=profile_dir or None) as session:
        session.challenge_wait_seconds = float(plan.get("challenge_wait_seconds", 180))
        session.manual_assist = bool(plan.get("manual_assist", False))
        if challenge_pause is not None:
            session.on_challenge = challenge_pause.set
            session.on_challenge_resolved = challenge_pause.clear

        def should_stop() -> bool:
            while challenge_pause is not None and challenge_pause.is_set():
                if stop_event is not None and stop_event.is_set():
                    return True
                time.sleep(0.25)
            return stop_event.is_set() if stop_event is not None else False

        location = ensure_spain_delivery(session, str(plan.get("postal_code", "28001")))
        if location is not None:
            print("[%s][槽位%d] 配送地点已确认" % (group, worker_id))

        requested_asins: list[str] = []

        candidate_rows = _candidate_rows(plan, group) if phase == "detail" else {}

        def collect_detail_batch(asins: list[str], active_url: str = "") -> None:
            if not asins:
                return
            allowed = set(_candidate_asins(plan, group)) if phase == "detail" else None
            if allowed is not None and not set(asins).issubset(allowed):
                raise ValueError("DETAIL_ASIN_OUTSIDE_FROZEN_CANDIDATES")
            claimed = claim_asins(asins)
            if not claimed:
                return
            requested_asins.extend(claimed)
            runtime.pending_detail_asins.update(claimed)
            runtime.save("RUNNING", active_url, phase=phase)
            try:
                detail_dir = str(category_dir / "detail_cache")
                request_plans = {asin: build_detail_request_plan(candidate_rows.get(asin, {"asin": asin}))
                                 for asin in claimed}
                for asin, context in request_plans.items():
                    context.setdefault("action", "FETCH_NEW")
                    context.setdefault("attempt", 1)
                if phase != "detail":
                    # Legacy all-mode has no immutable candidate manifest.
                    # Keep its historical collector call contract intact.
                    fresh = (collect_details(claimed, session, detail_dir)
                             if stop_event is None else
                             collect_details(claimed, session, detail_dir, should_stop=should_stop))
                elif stop_event is None:
                    fresh = collect_details(
                        claimed, session, detail_dir,
                        request_urls={asin: context["preferred_request_url"] for asin, context in request_plans.items()},
                        execution_context=request_plans,
                    )
                else:
                    fresh = collect_details(
                        claimed, session, detail_dir, should_stop=should_stop,
                        request_urls={asin: context["preferred_request_url"] for asin, context in request_plans.items()},
                        execution_context=request_plans,
                    )
                successes = {str(row.get("asin") or "").upper() for row in fresh if row.get("asin")}
                for row in fresh:
                    asin = str(row.get("asin") or "").upper()
                    if asin:
                        detail_map[asin] = row
                        runtime.pending_detail_asins.discard(asin)
                for asin in claimed:
                    if asin not in successes:
                        runtime.pending_detail_asins.add(asin)
                        release_asins([asin])
            except Exception:
                release_asins(claimed)
                raise

        if phase == "detail":
            candidates = _candidate_asins(plan, group)
            candidate_set = set(candidates)
            for cached in _completed_detail_cache_records(category_dir, candidate_set):
                asin = str(cached.get("asin") or "").upper()
                if asin and asin not in detail_map:
                    detail_map[asin] = cached
            # Ranking-phase reconciliation may preload valid historical detail
            # evidence and limit network work to its explicit reextract queue.
            for cached in (plan.get("detail_reuse_records_by_category") or {}).get(group, []):
                if not isinstance(cached, Mapping):
                    continue
                asin = str(cached.get("asin") or cached.get("ranking_asin") or "").strip().upper()
                if asin in candidate_set and asin not in detail_map:
                    detail_map[asin] = dict(cached)
            queued = (plan.get("detail_fetch_asins_by_category") or {}).get(group)
            blocked = {
                str(asin).strip().upper()
                for asin in (plan.get("detail_blocked_asins_by_category") or {}).get(group, [])
                if str(asin).strip()
            }
            fetch_set = ({str(asin).strip().upper() for asin in queued}
                         if queued is not None else candidate_set)
            fetch_set &= candidate_set
            runtime.pending_detail_asins.intersection_update(candidate_set)
            runtime.pending_detail_asins.update(
                asin for asin in candidates
                if asin in fetch_set and asin not in detail_map)
            runtime.pending_detail_asins.update(blocked & candidate_set)
            if runtime.pending_detail_asins:
                collect_detail_batch([asin for asin in candidates
                                      if asin in runtime.pending_detail_asins and asin in fetch_set])
            final_status = "DETAIL_COMPLETE" if not runtime.pending_detail_asins else "DETAIL_INCOMPLETE"
            runtime.save(final_status, "", phase="detail")
            return {
                "research_category": group, "status": final_status,
                "rankings": [], "details": list(detail_map.values()),
                "completed_source_urls": [], "source_status": runtime.source_status,
                "ranking_page_statuses": runtime.ranking_page_statuses,
                "raw_ranking_records": len(rankings),
                "unique_asins": len({str(row.get("asin") or "").upper() for row in rankings if row.get("asin")}),
                "detail_records": len(detail_map),
                "pending_detail_asins": sorted(runtime.pending_detail_asins),
                "detail_requested_asins": requested_asins,
            }

        if phase == "all" and runtime.pending_detail_asins:
            collect_detail_batch(sorted(runtime.pending_detail_asins))
            runtime.save("RUNNING", "", phase="all")

        for source in pending_sources:
            role = str(source.get("role") or "primary").lower()
            if role == "reserve" and not plan.get("force_reserve_sources") and not needs_reserve_sources(category, rankings):
                break
            if should_stop():
                from ..access.detector import AccessStopError
                raise AccessStopError("其他工作槽触发访问限制，停止新请求")
            url = normalize_url(source["source_url"])
            runtime.save("RUNNING", url, phase=phase)
            pages = int(source.get("pages_per_url", category.get("pages_per_url", plan.get("pages_per_url", 2))) or 2)
            if stop_event is None:
                current = collect_rankings([url], session, str(category_dir), pages_per_url=pages)
            else:
                current = collect_rankings([url], session, str(category_dir), pages_per_url=pages,
                                           should_stop=should_stop)
            runtime.ranking_page_statuses.extend(list(getattr(current, "page_statuses", []) or []))
            filtered = []
            for record in current:
                if not category_rank_filter(record, category, plan):
                    continue
                row = dict(record)
                row.update({"research_category": group, "collection_batch": batch,
                            "collection_time": collected_at, "source_role": role})
                filtered.append(row)
                key = (row.get("ranking_source_url"), row.get("ranking_page_number"),
                       row.get("bestseller_rank"), str(row.get("asin") or "").upper())
                if key not in ranking_keys:
                    ranking_keys.add(key)
                    rankings.append(row)
            runtime.source_status[url] = "ranked"
            runtime.save("RUNNING", url, phase=phase)
            if phase == "all":
                if should_stop():
                    from ..access.detector import AccessStopError
                    raise AccessStopError("其他工作槽触发访问限制，停止新请求")
                seen = set(detail_map)
                candidates = []
                for row in filtered:
                    asin = str(row.get("asin") or "").upper()
                    if asin and asin not in seen:
                        seen.add(asin)
                        candidates.append(asin)
                collect_detail_batch(candidates, url)
            runtime.source_status[url] = "completed"
            runtime.save("RUNNING", "", phase=phase)

        final_status = ("RANKING_COMPLETE" if phase == "ranking" else
                        "COMPLETE" if not runtime.pending_detail_asins else "DETAIL_INCOMPLETE")
        runtime.save(final_status, "", phase=phase)
    return {
        "research_category": group, "status": final_status,
        "rankings": rankings, "details": list(detail_map.values()),
        "completed_source_urls": [url for url, status in runtime.source_status.items() if status == "completed"],
        "source_status": runtime.source_status,
        "ranking_page_statuses": runtime.ranking_page_statuses,
        "raw_ranking_records": len(rankings),
        "unique_asins": len({str(row.get("asin") or "").upper() for row in rankings if row.get("asin")}),
        "detail_records": len(detail_map),
        "pending_detail_asins": sorted(runtime.pending_detail_asins),
        "detail_requested_asins": requested_asins,
    }


__all__ = ["run_category_live"]
