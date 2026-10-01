# -*- coding: utf-8 -*-
"""Reviewed category-scale task runner.

The historical ``batch-collect`` command remains available for the old
31--50 repair task.  This module is the task-specific runner for a reviewed
multi-category plan: it supports a bounded three-slot scheduler as the main
mode and a one-slot serial fallback, while reusing the existing ranking,
detail and access-gate collectors.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections import defaultdict
from datetime import datetime
import csv
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

from .quota import (QuotaError, normalize_source_url, select_research_quota,
                     validate_research_categories)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    # A fixed ``.tmp`` name allowed stale files and antivirus/indexer locks to
    # make Windows ``os.replace`` fail with WinError 5.  Keep each write's
    # staging file unique so a previous interrupted write cannot collide with
    # the current checkpoint.
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp.write_text(payload, encoding="utf-8")
    last_error: PermissionError | None = None
    for delay in (0.0, 0.05, 0.15, 0.35, 0.75, 1.5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_error = exc
            if delay:
                time.sleep(delay)

    # Some Windows readers deny delete/rename sharing but still allow a normal
    # write.  Preserve the same JSON payload through that fallback instead of
    # terminating the scheduler; the next successful checkpoint will restore
    # atomic replacement.  If the target also denies writing, surface the
    # original permission error and retain the staged file for recovery.
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.unlink(missing_ok=True)
        print(f"[任务] 检查点替换被占用，已使用直接写入回退：{path}")
        return
    except OSError:
        if last_error is not None:
            raise last_error
        raise


def _normalize_url(url: object) -> str:
    return str(url or "").strip().rstrip("/")


def _default_project_root() -> Path:
    # task.py lives at <project>/src/amazon_es_bestseller/collection/task.py.
    return Path(__file__).resolve().parents[3]


def resolve_task_path(value: str | Path, *, plan_path: str | Path | None = None,
                     project_root: str | Path | None = None) -> Path:
    """Resolve a task-plan path against the project root.

    ``source_snapshot`` is intentionally stored as a project-root-relative
    path.  ``plan_path`` is accepted for callers that need to report the
    originating plan, while the explicit project-root contract keeps a copied
    plan deterministic across worktrees and drive letters.  Absolute paths are
    still accepted for backwards-compatible offline validation, but are not
    emitted by the plan builder.
    """
    raw = Path(str(value)).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    root = Path(project_root).expanduser().resolve() if project_root else _default_project_root()
    return (root / raw).resolve()


def validate_task_plan(plan: Mapping, *, plan_path: str | Path | None = None,
                       project_root: str | Path | None = None) -> dict:
    """Validate the new task plan before any network request is made."""
    if not isinstance(plan, Mapping):
        raise ValueError("任务计划必须是 JSON 对象")
    task_id = str(plan.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("任务计划缺少 task_id")
    target = plan.get("target_unique")
    if target is None:
        raise ValueError("任务计划缺少 target_unique")
    if plan.get("discovery_required") is not False:
        raise ValueError("任务计划必须先完成当前类目树发现和人工审核")
    snapshot_value = str(plan.get("source_snapshot") or "").strip()
    if not snapshot_value:
        raise ValueError("任务计划缺少 source_snapshot 审核证据")
    snapshot_path = resolve_task_path(snapshot_value, plan_path=plan_path,
                                      project_root=project_root)
    if not snapshot_path.is_file():
        raise ValueError("source_snapshot 不存在：%s" % snapshot_path)
    if plan.get("sources_reviewed") is not True:
        raise ValueError("任务计划缺少 sources_reviewed=true 审核标记")
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("source_snapshot 不是有效 JSON：%s" % exc)
    if not isinstance(snapshot, Mapping):
        raise ValueError("source_snapshot 顶层必须是 JSON 对象")
    snapshot_pages = snapshot.get("pages")
    if not isinstance(snapshot_pages, list) or not snapshot_pages:
        raise ValueError("source_snapshot 缺少逐页发现证据")
    snapshot_root = snapshot_path.parent
    for page in snapshot_pages:
        if not isinstance(page, Mapping):
            raise ValueError("source_snapshot 页面记录无效")
        if not page.get("source_url") or not page.get("http_status"):
            raise ValueError("source_snapshot 页面缺少 source_url 或 http_status")
        html_file = str(page.get("html_file") or "").strip()
        if not html_file or not (snapshot_root / html_file).is_file():
            raise ValueError("source_snapshot 缺少原始 HTML 证据：%s" % page.get("source_url"))
    if int(snapshot.get("page_count", len(snapshot_pages))) != len(snapshot_pages):
        raise ValueError("source_snapshot page_count 与 pages 不一致")
    snapshot_links = snapshot.get("links")
    if not isinstance(snapshot_links, list):
        raise ValueError("source_snapshot 缺少解析后的 links")
    if int(snapshot.get("link_count", len(snapshot_links))) != len(snapshot_links):
        raise ValueError("source_snapshot link_count 与 links 不一致")
    observed_sources = {
        normalize_source_url(row.get("source_url"))
        for row in snapshot.get("pages", []) if isinstance(row, Mapping)
    }
    observed_sources.update(
        normalize_source_url(row.get("url"))
        for row in snapshot.get("links", []) if isinstance(row, Mapping)
    )
    if not observed_sources:
        raise ValueError("source_snapshot 没有可审核的来源 URL")
    categories = validate_research_categories(plan.get("categories"), target)
    scheduler = dict(plan.get("scheduler") or {})
    mode = str(scheduler.get("mode") or "parallel3")
    if mode not in {"parallel3", "serial"}:
        raise ValueError("scheduler.mode 必须是 parallel3 或 serial")
    try:
        parallel = int(scheduler.get("max_parallel_categories", 3))
        cooldown = int(scheduler.get("cooldown_after_category_seconds", 600))
        fallback_cooldown = int(scheduler.get("fallback_cooldown_seconds", 1800))
    except (TypeError, ValueError):
        raise ValueError("调度器并发数或冷却时间无效")
    if parallel != 3 or cooldown < 0 or fallback_cooldown < 0:
        raise ValueError("当前任务要求 max_parallel_categories=3，冷却不能为负数")
    try:
        plan_pages = int(plan.get("pages_per_url", 2 if int(plan.get("rank_end", 80) or 80) > 50 else 1))
    except (TypeError, ValueError):
        raise ValueError("pages_per_url 必须是正整数")
    if plan_pages < 1:
        raise ValueError("pages_per_url 必须是正整数")
    if int(plan.get("rank_end", 80) or 80) > 50 and plan_pages < 2:
        raise ValueError("排名超过50时，pages_per_url 至少必须为2")
    seen_urls: set[str] = set()
    normalized_categories = []
    for category in categories:
        sources = category.get("sources") or category.get("source_urls") or []
        if not isinstance(sources, list) or not sources:
            raise ValueError("研究类目 %s 缺少已审核来源榜单" % category["research_category"])
        normalized_sources = []
        for source in sources:
            if isinstance(source, str):
                source = {"source_url": source}
            if not isinstance(source, Mapping):
                raise ValueError("研究类目 %s 的来源必须是对象或 URL" % category["research_category"])
            url = _normalize_url(source.get("source_url") or source.get("url"))
            if (not url or urlsplit(url).scheme != "https"
                    or urlsplit(url).netloc.lower() not in {"amazon.es", "www.amazon.es"}):
                raise ValueError("研究类目 %s 存在非 Amazon.es HTTPS 来源" % category["research_category"])
            if url in seen_urls:
                raise ValueError("榜单来源重复：%s" % url)
            if normalize_source_url(url) not in observed_sources:
                raise ValueError("来源未出现在 source_snapshot：%s" % url)
            seen_urls.add(url)
            role = str(source.get("role") or "primary").strip().lower()
            if role not in {"primary", "reserve"}:
                raise ValueError("研究类目 %s 的来源 role 必须是 primary 或 reserve" % category["research_category"])
            normalized_sources.append({**dict(source), "source_url": url, "role": role})
        try:
            category_pages = int(category.get("pages_per_url", plan_pages) or plan_pages)
            category_end = int(category.get("rank_end", plan.get("rank_end", 80)) or 80)
        except (TypeError, ValueError):
            raise ValueError("研究类目 %s 的分页或排名范围无效" % category["research_category"])
        if category_pages < 1 or (category_end > 50 and category_pages < 2):
            raise ValueError("研究类目 %s 的 pages_per_url 不足以覆盖目标排名" % category["research_category"])
        normalized_categories.append({**category, "sources": normalized_sources,
                                      "pages_per_url": category_pages})
    return {**dict(plan), "categories": normalized_categories,
            "target_unique": int(target),
            "pages_per_url": plan_pages,
            "scheduler": {**scheduler, "mode": mode,
                           "max_parallel_categories": parallel,
                           "cooldown_after_category_seconds": cooldown,
                           "fallback_cooldown_seconds": fallback_cooldown}}


def _source_urls(category: Mapping, completed_urls: set[str]) -> list[dict]:
    return [dict(source) for source in category.get("sources", [])
            if _normalize_url(source.get("source_url")) not in completed_urls]


def _needs_reserve_sources(category: Mapping, rankings: list[Mapping]) -> bool:
    """Decide whether a category still needs its reviewed reserve sources."""
    target = int(category.get("target_unique", 0) or 0)
    share = float(category.get("max_single_source_share", 0.35) or 0.35)
    unique_asins = {str(row.get("asin") or "").upper() for row in rankings if row.get("asin")}
    source_urls = {normalize_source_url(row.get("ranking_source_url"))
                   for row in rankings if row.get("ranking_source_url")}
    source_cap = max(1, math.ceil(target * share))
    minimum_sources = math.ceil(target / source_cap) if target else 0
    return len(unique_asins) < target or len(source_urls) < minimum_sources


def _cooldown_seconds(mode: str, scheduler: Mapping) -> int:
    """Return the reviewed interval for the selected scheduler mode."""
    key = ("cooldown_after_category_seconds"
           if mode == "parallel3" else "fallback_cooldown_seconds")
    return int(scheduler[key])


def _category_rank_filter(record: Mapping, category: Mapping, plan: Mapping) -> bool:
    start = int(category.get("rank_start", plan.get("rank_start", 1)) or 1)
    end = int(category.get("rank_end", plan.get("rank_end", 80)) or 80)
    rank = record.get("bestseller_rank")
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        return False
    return start <= rank <= end


def _run_category_live(category: Mapping, plan: Mapping, output: Path,
                       worker_id: int, headful: bool, profile_dir: str,
                       claim_asins: Callable[[list[str]], list[str]],
                       release_asins: Callable[[list[str]], None],
                       completed_urls: set[str],
                       stop_event: threading.Event | None = None,
                       challenge_pause: threading.Event | None = None) -> dict:
    """Collect one category serially inside one worker/browser session."""
    from ..access.browser import BrowserSession
    from ..access.location import ensure_spain_delivery
    from .detail import collect_details
    from .ranking import collect_rankings

    group = str(category["research_category"])
    category_dir = output / "categories" / group
    category_dir.mkdir(parents=True, exist_ok=True)
    category_state_path = category_dir / "category_state.json"
    category_state = _read_json(category_state_path, {})
    rankings = _read_json(category_dir / "rankings.json", [])
    details = _read_json(category_dir / "details.json", [])
    ranking_keys = {(r.get("ranking_source_url"), r.get("ranking_page_number"),
                    r.get("bestseller_rank"), str(r.get("asin") or "").upper())
                   for r in rankings if isinstance(r, Mapping)}
    detail_map = {str(r.get("asin") or "").upper(): r for r in details
                  if isinstance(r, Mapping) and r.get("asin")}
    source_status = dict(category_state.get("source_status") or {})
    pending_detail_asins = {
        str(asin).strip().upper() for asin in (category_state.get("pending_detail_asins") or [])
        if str(asin).strip()
    }
    batch = str(plan.get("batch_id") or plan.get("task_id"))
    collected_at = datetime.now().isoformat(timespec="seconds")

    def save_category(status: str, active_url: str = "") -> None:
        _write_json_atomic(category_state_path, {
            "schema_version": 1,
            "research_category": group,
            "worker_id": worker_id,
            "status": status,
            "active_source_url": active_url,
            "source_status": source_status,
            "raw_ranking_records": len(rankings),
            "unique_asins": len({str(r.get("asin") or "").upper() for r in rankings if r.get("asin")}),
            "detail_records": len(detail_map),
            "pending_detail_asins": sorted(pending_detail_asins),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        _write_json_atomic(category_dir / "rankings.json", rankings)
        _write_json_atomic(category_dir / "details.json", list(detail_map.values()))

    completed_for_category = set(completed_urls) | {
        url for url, status in source_status.items() if status == "completed"
    }
    pending_sources = _source_urls(category, completed_for_category)
    pending_sources.sort(key=lambda source: 1 if str(source.get("role") or "primary").lower() == "reserve" else 0)
    with BrowserSession(headless=not headful, profile_dir=profile_dir or None) as session:
        session.challenge_wait_seconds = float(plan.get("challenge_wait_seconds", 180))
        session.manual_assist = bool(plan.get("manual_assist", False))
        if challenge_pause is not None:
            session.on_challenge = challenge_pause.set
            session.on_challenge_resolved = challenge_pause.clear

        def should_stop() -> bool:
            # Manual challenge handling pauses every worker before its next
            # request. A peer access stop still terminates the whole task.
            while challenge_pause is not None and challenge_pause.is_set():
                if stop_event is not None and stop_event.is_set():
                    return True
                time.sleep(0.25)
            return stop_event.is_set() if stop_event is not None else False

        location = ensure_spain_delivery(session, str(plan.get("postal_code", "28001")))
        if location is not None:
            print("[%s][槽位%d] 配送地点已确认" % (group, worker_id))

        def collect_detail_batch(asins: list[str]) -> None:
            """Collect and persist a retryable detail batch."""
            if not asins:
                return
            claimed = claim_asins(asins)
            if not claimed:
                return
            try:
                detail_dir = str(category_dir / "detail_cache")
                if stop_event is None:
                    # Keep compatibility with small offline test doubles and
                    # legacy adapters that predate the cooperative stop hook.
                    fresh = collect_details(claimed, session, detail_dir)
                else:
                    fresh = collect_details(claimed, session, detail_dir,
                                            should_stop=should_stop)
                successes = {str(row.get("asin") or "").upper() for row in fresh if row.get("asin")}
                for row in fresh:
                    asin = str(row.get("asin") or "").upper()
                    if asin:
                        detail_map[asin] = row
                        pending_detail_asins.discard(asin)
                for asin in claimed:
                    if asin not in successes:
                        pending_detail_asins.add(asin)
                        release_asins([asin])
            except Exception:
                release_asins(claimed)
                raise

        # A previous run may have finished ranking collection but left
        # transient detail failures. Retry those ASINs before new sources.
        if pending_detail_asins:
            collect_detail_batch(sorted(pending_detail_asins))
            save_category("RUNNING", "")

        for source in pending_sources:
            role = str(source.get("role") or "primary").lower()
            if role == "reserve" and not plan.get("force_reserve_sources") \
                    and not _needs_reserve_sources(category, rankings):
                break
            if should_stop():
                from ..access.detector import AccessStopError
                raise AccessStopError("其他工作槽触发访问限制，停止新请求")
            url = _normalize_url(source["source_url"])
            save_category("RUNNING", url)
            pages = int(source.get("pages_per_url", category.get("pages_per_url", plan.get("pages_per_url", 2))) or 2)
            if stop_event is None:
                current = collect_rankings([url], session, str(category_dir), pages_per_url=pages)
            else:
                current = collect_rankings([url], session, str(category_dir), pages_per_url=pages,
                                           should_stop=should_stop)
            filtered = []
            for row in current:
                if not _category_rank_filter(row, category, plan):
                    continue
                row = dict(row)
                row.update({"research_category": group, "collection_batch": batch,
                            "collection_time": collected_at})
                filtered.append(row)
                key = (row.get("ranking_source_url"), row.get("ranking_page_number"),
                       row.get("bestseller_rank"), str(row.get("asin") or "").upper())
                if key not in ranking_keys:
                    ranking_keys.add(key)
                    rankings.append(row)
            # Claim before details so concurrent categories do not fetch the same
            # ASIN twice. All ranking contexts are still retained above.
            candidates = []
            seen = set(detail_map)
            for row in filtered:
                asin = str(row.get("asin") or "").upper()
                if asin and asin not in seen:
                    seen.add(asin)
                    candidates.append(asin)
            if should_stop():
                from ..access.detector import AccessStopError
                raise AccessStopError("其他工作槽触发访问限制，停止新请求")
            collect_detail_batch(candidates)
            source_status[url] = "completed"
            save_category("RUNNING", "")
        final_status = "COMPLETE" if not pending_detail_asins else "DETAIL_INCOMPLETE"
        save_category(final_status, "")
    return {"research_category": group, "status": final_status,
            "rankings": rankings, "details": list(detail_map.values()),
            "completed_source_urls": list(source_status),
            "source_status": source_status,
            "raw_ranking_records": len(rankings),
            "unique_asins": len({str(r.get("asin") or "").upper() for r in rankings if r.get("asin")}),
            "detail_records": len(detail_map),
            "pending_detail_asins": sorted(pending_detail_asins)}


def _merge_records(output: Path, all_rankings: list, all_details: list) -> None:
    ranking_map = {}
    for row in all_rankings:
        key = (row.get("ranking_source_url"), row.get("ranking_page_number"),
               row.get("bestseller_rank"), str(row.get("asin") or "").upper())
        if key[3]:
            ranking_map.setdefault(key, row)
    detail_map = {}
    for row in all_details:
        asin = str(row.get("asin") or "").upper()
        if asin:
            detail_map.setdefault(asin, row)
    _write_json_atomic(output / "rankings.json", list(ranking_map.values()))
    _write_json_atomic(output / "details.json", list(detail_map.values()))


def _write_summary(output: Path, categories: list[Mapping], rankings: list,
                   details: list, selected: Mapping[str, list] | None,
                   statuses: Mapping[str, Mapping]) -> None:
    by_group = defaultdict(list)
    by_detail = defaultdict(set)
    for row in rankings:
        group = str(row.get("research_category") or "")
        if group:
            by_group[group].append(row)
    for row in details:
        asin = str(row.get("asin") or "").upper()
        if asin:
            by_detail[asin].add(asin)
    selected = selected or {}
    output.mkdir(parents=True, exist_ok=True)
    fields = ["采集研究类目", "目标SKU", "原始榜单记录", "类目内唯一ASIN",
              "详情记录", "最终保留", "使用榜单数量", "状态"]
    with (output / "category_summary.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for category in categories:
            group = category["research_category"]
            rows = by_group[group]
            asins = {str(row.get("asin") or "").upper() for row in rows if row.get("asin")}
            sources = {str(row.get("ranking_source_url") or "") for row in rows if row.get("ranking_source_url")}
            final_count = len(selected.get(group, []))
            target = int(category["target_unique"])
            writer.writerow({"采集研究类目": group, "目标SKU": target,
                             "原始榜单记录": len(rows), "类目内唯一ASIN": len(asins),
                             "详情记录": len(asins & set(by_detail)), "最终保留": final_count,
                             "使用榜单数量": len(sources),
                             "状态": "PASS" if final_count == target else statuses.get(group, {}).get("status", "INCOMPLETE")})


def run_task(plan: Mapping, out_dir: str, mode: str | None = None,
             headful: bool = False, profile_dir: str = "",
             plan_path: str | Path | None = None,
             project_root: str | Path | None = None) -> dict:
    """Run a reviewed plan in ``parallel3`` or ``serial`` mode."""
    plan = validate_task_plan(plan, plan_path=plan_path, project_root=project_root)
    scheduler = plan["scheduler"]
    mode = mode or scheduler["mode"]
    if mode not in {"parallel3", "serial"}:
        raise ValueError("mode 必须是 parallel3 或 serial")
    if mode == "parallel3" and profile_dir:
        raise ValueError("parallel3 不接受共享浏览器 profile_dir；请使用独立会话或串行模式")
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "batch_state_v2.json"
    state = _read_json(state_path, {})
    categories = plan["categories"]
    category_map = {row["research_category"]: row for row in categories}
    force_reserve_sources = state.get("run_status") == "QUOTA_UNIQUE_SHORTFALL"
    plan = {**plan, "force_reserve_sources": force_reserve_sources}
    category_states = dict(state.get("categories") or {})
    for group, value in list(category_states.items()):
        if value.get("status") == "RUNNING":
            value["status"] = "PENDING"
        elif value.get("status") == "DETAIL_INCOMPLETE":
            # A new process gets a fresh retry budget for persisted detail
            # failures; the ASIN queue itself remains authoritative.
            value["status"] = "PENDING"
            value["attempts"] = 0
    all_rankings = _read_json(output / "rankings.json", [])
    all_details = _read_json(output / "details.json", [])
    claimed = {str(row.get("asin") or "").upper() for row in all_details if row.get("asin")}
    claim_lock = threading.Lock()

    def claim_asins(asins: list[str]) -> list[str]:
        with claim_lock:
            fresh = [a for a in asins if a and a not in claimed]
            claimed.update(fresh)
            return fresh

    def release_asins(asins: list[str]) -> None:
        with claim_lock:
            for asin in asins:
                claimed.discard(asin)

    def save_state(run_status: str, ready_at: list[float], active: list[str]) -> None:
        _write_json_atomic(state_path, {
            "schema_version": 2, "task_id": plan["task_id"], "mode": mode,
            "run_status": run_status, "categories": category_states,
            "worker_ready_at": ready_at, "active_workers": active,
            "claimed_asins": sorted(claimed),
            "completed_source_urls": sorted(set(state.get("completed_source_urls", []))),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def has_unfinished_reserve(group: str) -> bool:
        current = category_states.get(group, {})
        source_status = current.get("source_status") or {}
        # Older batch checkpoints kept source_status only in the per-category
        # state file.  Hydrate it here so a quota-shortfall resume does not
        # reopen already exhausted reserve URLs.
        if not source_status:
            category_state_path = output / "categories" / group / "category_state.json"
            saved = _read_json(category_state_path, {})
            source_status = saved.get("source_status") or {}
        completed = {url for url, status in source_status.items()
                     if status == "completed"}
        return any(str(source.get("role") or "primary").lower() == "reserve"
                   and _normalize_url(source.get("source_url")) not in completed
                   for source in category_map[group].get("sources", []))

    pending = [group for group in category_map
               if category_states.get(group, {}).get("status") != "COMPLETE"
               or (force_reserve_sources and has_unfinished_reserve(group))]
    ready_at = [float(value) for value in state.get("worker_ready_at", [])]
    slots = max(1, 3 if mode == "parallel3" else 1)
    ready_at = (ready_at + [0.0] * slots)[:slots]
    statuses = category_states
    save_state("RUNNING", ready_at, [])
    stop_all = False
    stop_event = threading.Event()
    challenge_pause = threading.Event()
    last_cooldown_log = [0.0] * slots

    def run_one(group: str, slot: int):
        try:
            args = (category_map[group], plan, output, slot + 1, headful,
                    profile_dir, claim_asins, release_asins,
                    set(state.get("completed_source_urls", [])), stop_event)
            if plan.get("manual_assist"):
                return _run_category_live(*args, challenge_pause=challenge_pause)
            # Preserve compatibility with legacy/offline worker doubles that
            # implement the original positional signature.
            return _run_category_live(*args)
        except Exception as exc:
            # Trip the shared gate in the worker that observed access
            # restriction, before the scheduler gets a chance to inspect the
            # future.  Other workers therefore stop before their next request.
            from ..access.detector import AccessStopError
            if isinstance(exc, AccessStopError):
                stop_event.set()
            raise

    with ThreadPoolExecutor(max_workers=slots, thread_name_prefix="amazon-es-category") as executor:
        futures: dict[Future, tuple[int, str]] = {}
        while pending or futures:
            now = time.time()
            for slot in range(slots):
                remaining = ready_at[slot] - now
                if remaining > 0 and now - last_cooldown_log[slot] >= 10:
                    print("[任务][槽位%d] 冷却中，剩余 %.0f 秒" % (slot + 1, remaining))
                    last_cooldown_log[slot] = now
            # Fill ready slots. A persisted deadline is honored after restart.
            for slot in range(slots):
                if stop_all or any(existing_slot == slot for existing_slot, _ in futures.values()):
                    continue
                if not pending or ready_at[slot] > now:
                    continue
                group = pending.pop(0)
                previous = dict(statuses.get(group, {}))
                statuses[group] = {**previous, "status": "RUNNING", "worker": slot + 1,
                                   "attempts": int(previous.get("attempts", 0)) + 1}
                future = executor.submit(run_one, group, slot)
                futures[future] = (slot, group)
            save_state("ACCESS_STOP" if stop_all else "RUNNING", ready_at,
                       [group for _, group in futures.values()])
            if not futures:
                if pending:
                    delay = max(0.0, min(ready_at) - time.time())
                    if delay:
                        print("[任务] 等待下一工作槽冷却 %.0f 秒" % delay)
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
                    statuses[group] = {**statuses.get(group, {}), "status":
                                       "ACCESS_STOP" if isinstance(exc, AccessStopError) else "FAILED",
                                       "error": str(exc)}
                    if isinstance(exc, AccessStopError):
                        stop_all = True
                        stop_event.set()
                    elif statuses[group].get("attempts", 1) < 2:
                        statuses[group]["status"] = "PENDING"
                        pending.insert(0, group)
                    continue
                all_rankings.extend(result.get("rankings", []))
                all_details.extend(result.get("details", []))
                statuses[group] = {**statuses.get(group, {}), "status": "COMPLETE",
                                   "raw_ranking_records": result.get("raw_ranking_records", 0),
                                   "unique_asins": result.get("unique_asins", 0),
                                   "detail_records": result.get("detail_records", 0),
                                   "pending_detail_asins": result.get("pending_detail_asins", []),
                                   "source_status": result.get("source_status", {})}
                state.setdefault("completed_source_urls", []).extend(result.get("completed_source_urls", []))
                _merge_records(output, all_rankings, all_details)
                # Cool down after every completed category.  Do not inspect
                # `pending` here: another worker may fail a moment later and
                # enqueue a retry, which must still wait for this slot.
                ready_at[slot] = time.time() + _cooldown_seconds(mode, scheduler)
                if result.get("status") != "COMPLETE":
                    statuses[group]["status"] = result.get("status", "DETAIL_INCOMPLETE")
                    if statuses[group].get("attempts", 1) < 2:
                        statuses[group]["status"] = "PENDING"
                        pending.append(group)
                print("[%s][槽位%d] 完成，榜单%d条，详情%d条" %
                      (group, slot + 1, result.get("raw_ranking_records", 0),
                       result.get("detail_records", 0)))
    _merge_records(output, all_rankings, all_details)
    all_rankings = _read_json(output / "rankings.json", [])
    all_details = _read_json(output / "details.json", [])
    selected = None
    selected_rows = []
    missing_detail_asins = []
    run_status = "ACCESS_STOP" if stop_all else "INCOMPLETE"
    try:
        selected = select_research_quota(all_rankings, categories, plan["target_unique"])
        selected_rows = [row for group in selected.values() for row in group]
        selected_asins = {str(row.get("asin") or "").upper() for row in selected_rows if row.get("asin")}
        detail_asins = {str(row.get("asin") or "").upper() for row in all_details if row.get("asin")}
        missing_detail_asins = sorted(selected_asins - detail_asins)
        incomplete_groups = [group for group in category_map
                             if statuses.get(group, {}).get("status") != "COMPLETE"]
        if stop_all:
            run_status = "ACCESS_STOP"
        elif missing_detail_asins:
            run_status = "DETAIL_INCOMPLETE"
            print("[任务] 详情仍缺失 %d 个 ASIN，未通过完成 Gate" % len(missing_detail_asins))
        elif incomplete_groups:
            run_status = "INCOMPLETE"
            print("[任务] 类目仍未完成：%s" % ", ".join(incomplete_groups))
        else:
            run_status = "COMPLETE"
        _write_json_atomic(output / "candidate_manifest.json", selected_rows)
        _write_json_atomic(output / "final_manifest.json",
                           selected_rows if run_status == "COMPLETE" else [])
    except QuotaError as exc:
        _write_json_atomic(output / "final_manifest.json", [])
        run_status = "QUOTA_UNIQUE_SHORTFALL" if not stop_all else run_status
        print("[任务] 尚未通过最终配额 Gate：%s" % exc)
    _write_summary(output, categories, all_rankings, all_details, selected, statuses)
    report = {
        "schema_version": 1, "task_id": plan["task_id"], "mode": mode,
        "run_status": run_status, "target_unique": plan["target_unique"],
        "ranking_records": len(all_rankings),
        "unique_ranking_asins": len({str(row.get("asin") or "").upper() for row in all_rankings if row.get("asin")}),
        "detail_records": len({str(row.get("asin") or "").upper() for row in all_details if row.get("asin")}),
        "missing_detail_asins": missing_detail_asins,
        "final_unique_asins": len({str(row.get("asin") or "").upper()
                                    for row in (selected_rows if run_status == "COMPLETE" else [])
                                    if row.get("asin")}),
        "categories": statuses,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_json_atomic(output / "run_report.json", report)
    save_state(run_status, ready_at, [])
    return report
