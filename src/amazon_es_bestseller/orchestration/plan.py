"""Reviewed task-plan validation and source-selection policy.

This module contains the static part of category-scale collection: it validates
the reviewed source evidence before a scheduler can create a browser session.
It deliberately contains no runtime state or collection I/O.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from ..collection.quota import normalize_source_url, validate_research_categories
from .phases import canonical_json_sha256


def normalize_url(url: object) -> str:
    return str(url or "").strip().rstrip("/")


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def canonical_plan_sha256(plan: Mapping) -> str:
    """Fingerprint the parsed plan, not its incidental line endings."""
    return canonical_json_sha256(dict(plan))


def _formal_plan_registry(plan_path: str | Path | None, project_root: str | Path | None) -> dict:
    root = Path(project_root).expanduser().resolve() if project_root else default_project_root()
    preferred = (Path(plan_path).resolve().parent / "formal_plan_registry.json"
                 if plan_path else root / "configs" / "tasks" / "formal_plan_registry.json")
    registry_path = preferred if preferred.is_file() else root / "configs" / "tasks" / "formal_plan_registry.json"
    if not registry_path.is_file():
        return {}
    try:
        value = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("FORMAL_PLAN_REGISTRY_INVALID") from exc
    return dict(value) if isinstance(value, Mapping) else {}


def validate_formal_plan_fingerprint(plan: Mapping, *, plan_path: str | Path | None = None,
                                     project_root: str | Path | None = None) -> str:
    """Fail before a browser can exist when the immutable 5500 plan drifts."""
    task_id = str(plan.get("task_id") or "")
    registry = _formal_plan_registry(plan_path, project_root)
    entry = registry.get(task_id)
    if not isinstance(entry, Mapping):
        return canonical_plan_sha256(plan)
    expected = str(entry.get("canonical_sha256") or "").strip().lower()
    actual = canonical_plan_sha256(plan)
    if not expected or actual != expected:
        raise ValueError("FORMAL_5500_PLAN_HASH_MISMATCH")
    return actual


def resolve_task_path(value: str | Path, *, plan_path: str | Path | None = None,
                      project_root: str | Path | None = None) -> Path:
    """Resolve a reviewed-plan artifact path against the project root."""
    del plan_path  # Compatibility argument; paths are deliberately root-relative.
    raw = Path(str(value)).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    root = Path(project_root).expanduser().resolve() if project_root else default_project_root()
    return (root / raw).resolve()


def validate_task_plan(plan: Mapping, *, plan_path: str | Path | None = None,
                       project_root: str | Path | None = None) -> dict:
    """Validate a reviewed task plan before any collection can begin."""
    if not isinstance(plan, Mapping):
        raise ValueError("任务计划必须是 JSON 对象")
    task_id = str(plan.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("任务计划缺少 task_id")
    # Formal-task drift is a hard pre-browser gate.  Check it before parsing
    # any other operational field so a modified 5500 plan never reaches a
    # scheduler or transport through a secondary validation error.
    plan_hash = validate_formal_plan_fingerprint(plan, plan_path=plan_path,
                                                  project_root=project_root)
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
        raise ValueError("source_snapshot 不是有效 JSON：%s" % exc) from exc
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
        for row in snapshot_pages if isinstance(row, Mapping)
    }
    observed_sources.update(
        normalize_source_url(row.get("url"))
        for row in snapshot_links if isinstance(row, Mapping)
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
    except (TypeError, ValueError) as exc:
        raise ValueError("调度器并发数或冷却时间无效") from exc
    if parallel != 3 or cooldown < 0 or fallback_cooldown < 0:
        raise ValueError("当前任务要求 max_parallel_categories=3，冷却不能为负数")
    try:
        plan_pages = int(plan.get("pages_per_url", 2 if int(plan.get("rank_end", 80) or 80) > 50 else 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("pages_per_url 必须是正整数") from exc
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
            url = normalize_url(source.get("source_url") or source.get("url"))
            split = urlsplit(url)
            if not url or split.scheme != "https" or split.netloc.lower() not in {"amazon.es", "www.amazon.es"}:
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
        except (TypeError, ValueError) as exc:
            raise ValueError("研究类目 %s 的分页或排名范围无效" % category["research_category"]) from exc
        if category_pages < 1 or (category_end > 50 and category_pages < 2):
            raise ValueError("研究类目 %s 的 pages_per_url 不足以覆盖目标排名" % category["research_category"])
        normalized_categories.append({**category, "sources": normalized_sources,
                                      "pages_per_url": category_pages})
    return {**dict(plan), "categories": normalized_categories,
            "target_unique": int(target), "pages_per_url": plan_pages,
            "canonical_plan_sha256": plan_hash,
            "scheduler": {**scheduler, "mode": mode,
                          "max_parallel_categories": parallel,
                          "cooldown_after_category_seconds": cooldown,
                          "fallback_cooldown_seconds": fallback_cooldown}}


def source_urls(category: Mapping, completed_urls: set[str]) -> list[dict]:
    return [dict(source) for source in category.get("sources", [])
            if normalize_url(source.get("source_url")) not in completed_urls]


def needs_reserve_sources(category: Mapping, rankings: list[Mapping]) -> bool:
    target = int(category.get("target_unique", 0) or 0)
    share = float(category.get("max_single_source_share", 0.35) or 0.35)
    unique_asins = {str(row.get("asin") or "").upper() for row in rankings if row.get("asin")}
    source_urls_seen = {normalize_source_url(row.get("ranking_source_url"))
                         for row in rankings if row.get("ranking_source_url")}
    source_cap = max(1, math.ceil(target * share))
    minimum_sources = math.ceil(target / source_cap) if target else 0
    return len(unique_asins) < target or len(source_urls_seen) < minimum_sources


def cooldown_seconds(mode: str, scheduler: Mapping) -> int:
    key = "cooldown_after_category_seconds" if mode == "parallel3" else "fallback_cooldown_seconds"
    return int(scheduler[key])


def category_rank_filter(record: Mapping, category: Mapping, plan: Mapping) -> bool:
    start = int(category.get("rank_start", plan.get("rank_start", 1)) or 1)
    end = int(category.get("rank_end", plan.get("rank_end", 80)) or 80)
    try:
        rank = int(record.get("bestseller_rank"))
    except (TypeError, ValueError):
        return False
    return start <= rank <= end


__all__ = ["canonical_plan_sha256", "category_rank_filter", "cooldown_seconds", "default_project_root",
           "needs_reserve_sources", "normalize_url", "resolve_task_path", "source_urls",
           "validate_formal_plan_fingerprint", "validate_task_plan"]
