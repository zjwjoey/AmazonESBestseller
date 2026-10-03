# -*- coding: utf-8 -*-
"""Pure-offline incremental detail planning.

This module never imports a browser or performs a request.  It consumes the
latest authoritative ranking records plus existing detail evidence/state and
emits explicit actions for the executor.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from ..collection.detail import CURRENT_DETAIL_SCHEMA_VERSION
from ..identity import resolve_identity
from ..models import is_valid_asin, normalize_asin


class DetailAction(str, Enum):
    REUSE_VALID_CACHE = "REUSE_VALID_CACHE"
    FETCH_NEW = "FETCH_NEW"
    REFETCH_INVALID_CACHE = "REFETCH_INVALID_CACHE"
    RETRY_TRANSIENT_FAILURE = "RETRY_TRANSIENT_FAILURE"
    VERIFY_IDENTITY = "VERIFY_IDENTITY"
    REPARSE_SAVED_HTML = "REPARSE_SAVED_HTML"
    BLOCK_ACCESS_STATE = "BLOCK_ACCESS_STATE"
    BLOCK_LINK_IDENTITY = "BLOCK_LINK_IDENTITY"
    BLOCK_CODE_FIX = "BLOCK_CODE_FIX"


class DetailRefreshPolicy:
    """V1 policy hook; valid detail evidence is reusable indefinitely."""

    def should_refresh(self, ranking_record: Mapping, detail_record: Mapping | None,
                       now=None) -> bool:
        return False


_NETWORK_ACTIONS = {
    DetailAction.FETCH_NEW.value,
    DetailAction.REFETCH_INVALID_CACHE.value,
    DetailAction.RETRY_TRANSIENT_FAILURE.value,
}
_ACCESS_STATES = {"CHALLENGE", "RATE_LIMITED", "BLOCKED", "ACCESS_BLOCKED"}
_TRANSIENT_STATES = {"FAILED", "TIMEOUT", "NETWORK_ERROR", "TRANSIENT_FAILED",
                     "TRANSIENT_NETWORK_FAILURE"}
_NON_RETRYABLE_STATES = {"PARSER_ERROR", "PROGRAM_ERROR", "SCHEMA_ERROR",
                         "DETERMINISTIC_INVALID_PAGE", "IDENTITY_MISMATCH"}


def _rows(value) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if isinstance(value.get("rankings"), list):
            return [dict(row) for row in value["rankings"] if isinstance(row, Mapping)]
        if isinstance(value.get("records"), list):
            return [dict(row) for row in value["records"] if isinstance(row, Mapping)]
        result = []
        for key, row in value.items():
            if isinstance(row, Mapping):
                item = dict(row)
                item.setdefault("asin", key)
                result.append(item)
        return result
    return [dict(row) for row in value if isinstance(row, Mapping)]


def _state_rows(value) -> dict[str, dict]:
    if value is None:
        return {}
    if hasattr(value, "records") and callable(value.records):
        value = value.records()
    return {normalize_asin(row.get("asin") or row.get("ranking_asin")): dict(row)
            for row in _rows(value)
            if normalize_asin(row.get("asin") or row.get("ranking_asin"))}


def _html_available(saved_html, asin: str) -> bool:
    if saved_html is None:
        return False
    if isinstance(saved_html, Mapping):
        return bool(saved_html.get(asin) or saved_html.get(asin.upper()))
    root = Path(saved_html)
    if root.is_file():
        return True
    return (root / (asin + ".html")).exists()


def _schema(record: Mapping) -> int:
    try:
        return int(record.get("detail_schema_version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _identity_status(ranking_asin: str, record: Mapping | None) -> str:
    if not record:
        return "UNKNOWN"
    explicit = str(record.get("identity_status") or "").upper()
    if explicit in {"IDENTITY_MISMATCH", "IDENTITY_UNCONFIRMED", "IDENTITY_REVIEW",
                    "PARENT_ASIN_MATCH", "VARIATION_RELATED", "MATCH_BY_EXTERNAL_EVIDENCE"}:
        return explicit
    resolved = resolve_identity(
        ranking_asin=ranking_asin,
        requested_asin=record.get("requested_asin") or ranking_asin,
        requested_url=record.get("requested_url") or "",
        final_url_asin=record.get("final_url") or record.get("resolved_asin") or "",
        canonical_asin=record.get("canonical_asin") or "",
        embedded_asins=record.get("embedded_asins") or record.get("observed_page_asins") or [],
        parsed_detail_asin=record.get("parsed_detail_asin") or record.get("resolved_asin") or record.get("asin"),
        parent_asin=record.get("parent_asin") or record.get("parent_asin_raw"),
        variation_family_asins=record.get("variation_family_asins") or record.get("variation_asins") or [],
    )
    detail_asin = normalize_asin(record.get("resolved_asin") or record.get("asin"))
    parent_asin = normalize_asin(record.get("parent_asin") or record.get("parent_asin_raw"))
    family = record.get("variation_family_asins") or record.get("variation_asins") or []
    if isinstance(family, str):
        family = [family]
    family = {normalize_asin(value) for value in family if normalize_asin(value)}
    if resolved["identity_status"] == "IDENTITY_MISMATCH" and detail_asin:
        return "IDENTITY_MISMATCH"
    if detail_asin == ranking_asin:
        return "MATCH"
    # A parent/variation relationship is valid only when the ranking ASIN is
    # explicitly connected to the resolved detail ASIN.  A self-parent value
    # or a family containing only the resolved ASIN is not evidence that an
    # unrelated ranking ASIN may reuse this cache.
    if parent_asin == ranking_asin and detail_asin:
        return "PARENT_ASIN_MATCH"
    if (ranking_asin in family and detail_asin in family
            and detail_asin == parent_asin):
        return "PARENT_ASIN_MATCH"
    if ranking_asin in family and detail_asin in family:
        return "VARIATION_RELATED"
    return "IDENTITY_MISMATCH" if detail_asin else "IDENTITY_REVIEW"


def _cache_classification(record: Mapping | None) -> str:
    if not record:
        return "SOURCE_MISSING"
    explicit = str(record.get("cache_classification") or record.get("classification") or "").upper()
    if explicit in {"VALID_PRODUCT_PAGE", "VALID"}:
        return "VALID_PRODUCT_PAGE"
    if explicit in {"CHALLENGE", "BLOCKED", "RATE_LIMITED"}:
        return "ACCESS_BLOCKED"
    if explicit in {"INVALID_OR_EMPTY", "INVALID"}:
        return "INVALID"
    status = str(record.get("detail_status") or record.get("status") or "").upper()
    access = str(record.get("access_state") or "").upper()
    if access in _ACCESS_STATES or status in _ACCESS_STATES:
        return "ACCESS_BLOCKED"
    if access in _TRANSIENT_STATES or status in _TRANSIENT_STATES:
        return "TRANSIENT_FAILED"
    if status in _NON_RETRYABLE_STATES or str(record.get("error_class") or "").upper() in _NON_RETRYABLE_STATES:
        return "NON_RETRYABLE_FAILURE"
    # Backward-compatible old state records are accepted only when they carry
    # actual parsed product evidence, not merely an ASIN or file path.
    if (record.get("title_es_raw") and not record.get("is_captcha")
            and status not in {"INVALID", "FAILED"}):
        return "VALID_PRODUCT_PAGE"
    return "INVALID"


def _safe_request_url(row: Mapping, asin: str, cache: Mapping | None = None) -> tuple[str, str, str]:
    raw = str(row.get("ranking_product_url_raw") or "")
    normalized = str(row.get("ranking_product_url_normalized") or "")
    link_status = str(row.get("ranking_link_identity_status") or "").upper()
    if link_status in {"MATCH", ""} and normalized:
        return normalized, "latest_ranking_product_url", "https://www.amazon.es/dp/%s" % asin
    cache = cache or {}
    historical = str(cache.get("ranking_product_url_normalized") or "")
    if (historical and re.search(r"/dp/%s(?:[/?#]|$)" % re.escape(asin),
                                historical, re.I)):
        return historical, "historical_valid_ranking_product_url", "https://www.amazon.es/dp/%s" % asin
    return "https://www.amazon.es/dp/%s" % asin, "asin_canonical_fallback", ""


def build_detail_plan(ranking_snapshot, detail_cache=None, detail_state=None, *,
                      saved_html=None, current_schema_version: int = CURRENT_DETAIL_SCHEMA_VERSION,
                      refresh_policy: DetailRefreshPolicy | None = None,
                      checkpoints=None, checkpoint=None,
                      current_access_state: str = "UNKNOWN") -> dict:
    """Build a deterministic offline plan, deduplicated by canonical ranking ASIN."""
    if not isinstance(ranking_snapshot, Mapping):
        raise ValueError("详情 planner 需要带 manifest 的 AUTHORITATIVE ranking snapshot")
    if ranking_snapshot.get("snapshot_status") != "AUTHORITATIVE":
        raise ValueError("详情 planner 只能使用 AUTHORITATIVE ranking snapshot")
    ranking_rows = _rows(ranking_snapshot)
    snapshot_id = str(ranking_snapshot.get("snapshot_id") or "")
    row_snapshot_ids = {str(row.get("snapshot_id") or "") for row in ranking_rows
                        if row.get("snapshot_id")}
    if snapshot_id and row_snapshot_ids - {snapshot_id}:
        raise ValueError("详情 planner 拒绝混合 snapshot_id")
    cache_by_asin = _state_rows(detail_cache)
    cache_rows = _rows(detail_cache)
    state_by_asin = _state_rows(detail_state)
    checkpoint_by_asin = _state_rows(checkpoints if checkpoints is not None else checkpoint)
    chosen: dict[str, dict] = {}
    contexts: dict[str, list[dict]] = {}

    def context_key(row: Mapping) -> tuple[int, int, str]:
        status = str(row.get("ranking_link_identity_status") or "").upper()
        source = str(row.get("ranking_asin_source") or "").upper()
        if status == "MATCH" and row.get("ranking_product_url_normalized"):
            evidence = 0
        elif source == "CARD_DATA_ASIN" and status in {"NO_PRODUCT_URL", ""}:
            evidence = 1
        elif status in {"NO_PRODUCT_URL", "NO_ASIN_IN_URL"}:
            evidence = 2
        else:
            evidence = 3
        rank = row.get("ranking_rank", row.get("bestseller_rank"))
        try:
            rank_value = int(rank)
        except (TypeError, ValueError):
            rank_value = 10**9
        return evidence, rank_value, str(row.get("ranking_product_url_raw") or "")

    for row in ranking_rows:
        asin = normalize_asin(row.get("ranking_asin") or row.get("asin"))
        if not asin or not is_valid_asin(asin):
            continue
        contexts.setdefault(asin, []).append(dict(row))
        existing = chosen.get(asin)
        if existing is None or context_key(row) < tuple(existing.get("_context_key", (10**9, 10**9, ""))):
            chosen[asin] = {**row, "_context_key": context_key(row)}

    policy = refresh_policy or DetailRefreshPolicy()
    items = []
    for asin in sorted(chosen, key=lambda key: (chosen[key].get("_context_key", (10**9, 10**9, "")), key)):
        row = chosen[asin]
        legacy_cache = dict(cache_by_asin.get(asin) or {})
        state_cache = dict(state_by_asin.get(asin) or {})
        prior_checkpoint = checkpoint_by_asin.get(asin, {})
        checkpoint_record = (prior_checkpoint.get("record")
                             if isinstance(prior_checkpoint.get("record"), Mapping)
                             else prior_checkpoint)
        if str(prior_checkpoint.get("status") or "").lower() == "success" and checkpoint_record:
            cache = dict(checkpoint_record)
            cache.update({key: value for key, value in prior_checkpoint.items()
                          if key not in {"record", "asin", "ranking_asin"}})
        elif state_cache:
            cache = state_cache
            cache.update({key: value for key, value in legacy_cache.items() if key not in cache})
        else:
            cache = legacy_cache
        if not cache:
            # A child may legitimately reuse a saved parent/variation-family
            # evidence record when the relation is explicit in that record.
            for candidate in cache_rows:
                parent = normalize_asin(candidate.get("parent_asin"))
                family = candidate.get("variation_family_asins") or candidate.get("variation_asins") or []
                if isinstance(family, str):
                    family = [family]
                family = {normalize_asin(value) for value in family if normalize_asin(value)}
                if asin == parent or asin in family:
                    cache = dict(candidate)
                    break
        cache.update({key: value for key, value in state_cache.items() if key not in cache})
        if str(prior_checkpoint.get("status") or "").lower() not in {"", "success"}:
            cache.setdefault("asin", asin)
            cache.update({key: value for key, value in prior_checkpoint.items()
                          if key not in {"asin", "ranking_asin"}})
        cache_state = _cache_classification(cache or None)
        identity = _identity_status(asin, cache or None)
        link_status = str(row.get("ranking_link_identity_status") or "").upper()
        request_url, request_source, fallback = _safe_request_url(row, asin, cache)
        schema = _schema(cache)
        html_exists = _html_available(saved_html, asin)
        if link_status in {"LINK_ASIN_MISMATCH", "MISMATCH"}:
            action = DetailAction.BLOCK_LINK_IDENTITY.value
            reason = "榜单商品链接身份状态为 %s，不能自动把链接 ASIN 覆盖榜单 ASIN" % link_status
        elif cache_state == "ACCESS_BLOCKED" and str(current_access_state or "").upper() not in {"NORMAL", "RECOVERED"}:
            action = DetailAction.BLOCK_ACCESS_STATE.value
            reason = "历史详情证据处于访问限制状态，等待 Access Gate 恢复"
        elif cache_state == "ACCESS_BLOCKED":
            action = DetailAction.RETRY_TRANSIENT_FAILURE.value
            reason = "当前 Access Gate 已恢复，重试历史受限详情"
        elif identity in {"IDENTITY_MISMATCH", "IDENTITY_REVIEW", "IDENTITY_UNCONFIRMED"} and cache:
            action = DetailAction.VERIFY_IDENTITY.value
            reason = "详情 ASIN 与榜单 ASIN 需要独立身份复核"
        elif cache_state == "NON_RETRYABLE_FAILURE":
            action = DetailAction.BLOCK_CODE_FIX.value
            reason = "确定性解析/程序错误不能自动重试"
        elif cache_state == "TRANSIENT_FAILED":
            action = DetailAction.RETRY_TRANSIENT_FAILURE.value
            reason = "上次为瞬时网络失败，允许下一轮重试"
        elif cache and schema < int(current_schema_version) and html_exists:
            action = DetailAction.REPARSE_SAVED_HTML.value
            reason = "详情 schema 过期，但已有保存 HTML，可离线重解析"
        elif cache and schema < int(current_schema_version):
            action = DetailAction.REFETCH_INVALID_CACHE.value
            reason = "详情 schema 过期且没有可用保存 HTML"
        elif cache_state == "VALID_PRODUCT_PAGE" and identity in {
                "MATCH", "IDENTITY_MATCH", "MATCH_BY_EXTERNAL_EVIDENCE",
                "PARENT_ASIN_MATCH", "VARIATION_RELATED", "UNKNOWN"}:
            action = (DetailAction.REFETCH_INVALID_CACHE.value
                      if policy.should_refresh(row, cache) else DetailAction.REUSE_VALID_CACHE.value)
            reason = "有效详情缓存可长期复用" if action == DetailAction.REUSE_VALID_CACHE.value else "刷新策略要求重抓"
        elif cache_state == "INVALID":
            action = DetailAction.REFETCH_INVALID_CACHE.value
            reason = "缓存分类为无效或空页面，保留旧证据后重抓"
        else:
            action = DetailAction.FETCH_NEW.value
            reason = "榜单出现新 ASIN，暂无有效详情缓存"
        items.append({
            "snapshot_id": row.get("snapshot_id") or snapshot_id,
            "ranking_asin": asin,
            "asin": asin,
            "ranking_rank": row.get("ranking_rank", row.get("bestseller_rank")),
            "research_category": row.get("research_category"),
            "ranking_product_url_raw": row.get("ranking_product_url_raw") or "",
            "ranking_product_url_normalized": row.get("ranking_product_url_normalized") or "",
            "detail_action": action,
            "action_reason": reason,
            "existing_cache_state": cache_state,
            "existing_detail_schema_version": schema,
            "identity_status": identity,
            "requested_asin": asin,
            "resolved_asin": cache.get("resolved_asin") or "",
            "parent_asin": cache.get("parent_asin") or cache.get("parent_asin_raw") or "",
            "identity_evidence": cache.get("identity_evidence") or [],
            "preferred_request_url": request_url,
            "preferred_request_url_source": request_source,
            "fallback_request_url": fallback or "https://www.amazon.es/dp/%s" % asin,
            "ranking_context_count": len(contexts[asin]),
            "ranking_contexts": contexts[asin],
            "saved_html_dir": str(saved_html) if isinstance(saved_html, (str, Path)) else "",
            "best_ranking_rank": min((int(c.get("ranking_rank", c.get("bestseller_rank")))
                                       for c in contexts[asin]
                                       if str(c.get("ranking_rank", c.get("bestseller_rank")) or "").isdigit()),
                                      default=None),
        })
        chosen[asin].pop("_context_key", None)
    summary = {"total": len(items), "actions": {action.value: 0 for action in DetailAction}}
    for item in items:
        summary["actions"][item["detail_action"]] += 1
    result = {"schema_version": 1, "snapshot_id": snapshot_id or (items[0]["snapshot_id"] if items else ""),
              "records": items, "summary": summary}
    return result


def write_detail_plan(plan: Mapping, output_dir: str | Path) -> dict[str, Path]:
    """Write the stable JSON/CSV/summary artifacts requested by V1."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    records = [dict(row) for row in (plan.get("records") or []) if isinstance(row, Mapping)]
    snapshot_id = str(plan.get("snapshot_id") or "")
    snapshot_ids = {str(row.get("snapshot_id") or "") for row in records if row.get("snapshot_id")}
    if snapshot_id and snapshot_ids - {snapshot_id}:
        raise ValueError("detail plan 包含多个 snapshot_id")
    created_at = str(plan.get("created_at") or datetime.now(timezone.utc).isoformat())
    schema_version = int(plan.get("plan_schema_version") or 1)
    payload = {"plan_schema_version": schema_version, "snapshot_id": snapshot_id,
               "records": records}
    plan_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
    plan_id = str(plan.get("plan_id") or ("detail_plan_" + plan_hash[:16]))
    artifact = {"plan_schema_version": schema_version, "plan_id": plan_id,
                "plan_hash": plan_hash, "snapshot_id": snapshot_id,
                "created_at": created_at, "summary": dict(plan.get("summary") or {}),
                "records": records}
    json_path = root / "detail_plan.json"
    csv_path = root / "detail_plan.csv"
    summary_path = root / "detail_plan_summary.json"
    json_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["snapshot_id", "ranking_asin", "ranking_rank", "research_category",
              "ranking_product_url_raw", "ranking_product_url_normalized", "detail_action",
              "action_reason", "existing_cache_state", "existing_detail_schema_version",
              "identity_status", "preferred_request_url", "fallback_request_url"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    summary_path.write_text(json.dumps({**(plan.get("summary") or {}),
                                        "plan_id": plan_id, "plan_hash": plan_hash,
                                        "snapshot_id": snapshot_id},
                                       ensure_ascii=False, indent=2),
                            encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "summary": summary_path}


def validate_detail_plan(plan: Mapping) -> dict:
    """Validate snapshot homogeneity and the immutable artifact hash."""
    if not isinstance(plan, Mapping):
        raise ValueError("detail plan 必须是 object artifact")
    records = [row for row in (plan.get("records") or []) if isinstance(row, Mapping)]
    snapshot_id = str(plan.get("snapshot_id") or "")
    ids = {str(row.get("snapshot_id") or "") for row in records if row.get("snapshot_id")}
    if snapshot_id and ids - {snapshot_id}:
        raise ValueError("detail plan 包含混合 snapshot_id")
    if plan.get("plan_hash"):
        payload = {"plan_schema_version": int(plan.get("plan_schema_version") or 1),
                   "snapshot_id": snapshot_id, "records": records}
        expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        if expected != str(plan.get("plan_hash")):
            raise ValueError("detail plan hash 校验失败，artifact 可能已被修改")
    return dict(plan)
