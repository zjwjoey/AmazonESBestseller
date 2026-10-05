"""One-category serial collection worker for reviewed task plans."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
import time
from typing import Callable, Mapping

from .checkpoint import TaskCheckpointRepository
from .plan import category_rank_filter, needs_reserve_sources, normalize_url, source_urls
from .state import CategoryRuntimeState


def run_category_live(category: Mapping, plan: Mapping, output: Path,
                      worker_id: int, headful: bool, profile_dir: str,
                      claim_asins: Callable[[list[str]], list[str]],
                      release_asins: Callable[[list[str]], None],
                      completed_urls: set[str],
                      stop_event: threading.Event | None = None,
                      challenge_pause: threading.Event | None = None) -> dict:
    """Collect exactly one category serially, preserving its retry checkpoint."""
    from ..access.browser import BrowserSession
    from ..access.location import ensure_spain_delivery
    from ..collection.detail import collect_details
    from ..collection.ranking import collect_rankings

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

        def collect_detail_batch(asins: list[str], active_url: str = "") -> None:
            if not asins:
                return
            claimed = claim_asins(asins)
            if not claimed:
                return
            runtime.pending_detail_asins.update(claimed)
            runtime.save("RUNNING", active_url)
            try:
                detail_dir = str(category_dir / "detail_cache")
                if stop_event is None:
                    fresh = collect_details(claimed, session, detail_dir)
                else:
                    fresh = collect_details(claimed, session, detail_dir, should_stop=should_stop)
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

        # Resume durable incomplete details before taking a new ranking source.
        if runtime.pending_detail_asins:
            collect_detail_batch(sorted(runtime.pending_detail_asins))
            runtime.save("RUNNING", "")

        for source in pending_sources:
            role = str(source.get("role") or "primary").lower()
            if role == "reserve" and not plan.get("force_reserve_sources") and not needs_reserve_sources(category, rankings):
                break
            if should_stop():
                from ..access.detector import AccessStopError
                raise AccessStopError("其他工作槽触发访问限制，停止新请求")
            url = normalize_url(source["source_url"])
            runtime.save("RUNNING", url)
            pages = int(source.get("pages_per_url", category.get("pages_per_url", plan.get("pages_per_url", 2))) or 2)
            if stop_event is None:
                current = collect_rankings([url], session, str(category_dir), pages_per_url=pages)
            else:
                current = collect_rankings([url], session, str(category_dir), pages_per_url=pages,
                                           should_stop=should_stop)
            filtered = []
            for record in current:
                if not category_rank_filter(record, category, plan):
                    continue
                row = dict(record)
                row.update({"research_category": group, "collection_batch": batch,
                            "collection_time": collected_at})
                filtered.append(row)
                key = (row.get("ranking_source_url"), row.get("ranking_page_number"),
                       row.get("bestseller_rank"), str(row.get("asin") or "").upper())
                if key not in ranking_keys:
                    ranking_keys.add(key)
                    rankings.append(row)
            candidates = []
            seen = set(detail_map)
            for row in filtered:
                asin = str(row.get("asin") or "").upper()
                if asin and asin not in seen:
                    seen.add(asin)
                    candidates.append(asin)
            # Ranking proof is durable before its first associated detail request.
            runtime.source_status[url] = "ranked"
            runtime.save("RUNNING", url)
            if should_stop():
                from ..access.detector import AccessStopError
                raise AccessStopError("其他工作槽触发访问限制，停止新请求")
            collect_detail_batch(candidates, url)
            runtime.source_status[url] = "completed"
            runtime.save("RUNNING", "")

        final_status = "COMPLETE" if not runtime.pending_detail_asins else "DETAIL_INCOMPLETE"
        runtime.save(final_status, "")
    return {
        "research_category": group, "status": final_status,
        "rankings": rankings, "details": list(detail_map.values()),
        "completed_source_urls": list(runtime.source_status), "source_status": runtime.source_status,
        "raw_ranking_records": len(rankings),
        "unique_asins": len({str(row.get("asin") or "").upper() for row in rankings if row.get("asin")}),
        "detail_records": len(detail_map),
        "pending_detail_asins": sorted(runtime.pending_detail_asins),
    }


__all__ = ["run_category_live"]
