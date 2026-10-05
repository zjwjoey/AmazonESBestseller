"""Offline-only V2 parser canary and promotion evidence gate.

This module deliberately does not navigate Amazon or change a parser default.
It compares supplied V1/V2 and saved-HTML replay evidence under the reviewed
one-source/two-page/five-detail budget.  A passing candidate is only
``PROMOTABLE`` evidence; promotion remains an explicit later operation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


CANARY_SCHEMA_VERSION = "amazon-es-v2-canary-v2"
MAX_SOURCES, MAX_PAGES_PER_SOURCE, MAX_DETAIL_ASINS = 1, 2, 5
NORMAL_ACCESS = {"NORMAL", "SUCCESS", "COMPLETE", "AUTHORITATIVE", "200"}
CHALLENGE_ACCESS = {"CHALLENGE", "CAPTCHA", "BOT_BLOCK", "ACCESS_DENIED", "BLOCKED", "RATE_LIMITED", "403", "429"}


class CanaryScopeError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _asin(row: Mapping[str, Any]) -> str:
    return str(row.get("asin") or row.get("ranking_asin") or row.get("requested_asin") or "").strip().upper()


def _rank(row: Mapping[str, Any]) -> str:
    return str(row.get("bestseller_rank") or row.get("ranking_rank") or "")


def _url(row: Mapping[str, Any]) -> str:
    return str(row.get("product_url") or row.get("ranking_product_url_raw") or row.get("final_url") or "")


def _access_is_normal(value: Any) -> bool:
    return str(value or "NORMAL").strip().upper() in NORMAL_ACCESS


@dataclass(frozen=True)
class CanaryProfile:
    profile_version: str = CANARY_SCHEMA_VERSION
    candidate_parser: str = "v2"
    stable_parser: str = "v1"
    enabled: bool = False
    max_sources: int = MAX_SOURCES
    max_pages_per_source: int = MAX_PAGES_PER_SOURCE
    max_detail_asins: int = MAX_DETAIL_ASINS
    promote_by_default: bool = False
    allow_network: bool = False
    excluded_task_ids: tuple[str, ...] = ("amazon_es_bestseller_5000_202610",)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanaryProfile":
        data = dict(value)
        if isinstance(data.get("excluded_task_ids"), list):
            data["excluded_task_ids"] = tuple(str(item) for item in data["excluded_task_ids"])
        profile = cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})
        if profile.candidate_parser.lower() not in {"v2", "collection.ranking_v2"}:
            raise CanaryScopeError("candidate parser must be V2")
        if profile.stable_parser.lower() not in {"v1", "collection.ranking"}:
            raise CanaryScopeError("stable parser must remain V1")
        if profile.enabled or profile.promote_by_default or profile.allow_network:
            raise CanaryScopeError("canary profile must remain disabled, offline, and non-promoting by default")
        if (not 1 <= int(profile.max_sources) <= MAX_SOURCES or
                not 1 <= int(profile.max_pages_per_source) <= MAX_PAGES_PER_SOURCE or
                not 0 <= int(profile.max_detail_asins) <= MAX_DETAIL_ASINS):
            raise CanaryScopeError("canary scope exceeds reviewed maximum")
        return profile


def load_profile(path: str | Path) -> CanaryProfile:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise CanaryScopeError("canary profile must be an object")
    return CanaryProfile.from_mapping(value)


def validate_scope(profile: CanaryProfile, source_urls: Sequence[str], *, pages_per_source: int,
                   detail_asins: Sequence[str] = (), task_id: str = "") -> None:
    sources = [str(url).strip() for url in source_urls if str(url).strip()]
    if task_id and task_id in profile.excluded_task_ids:
        raise CanaryScopeError("reviewed 5500-SKU V1 task must not use V2 canary")
    if not sources or len(sources) > min(profile.max_sources, MAX_SOURCES):
        raise CanaryScopeError("canary permits exactly one source")
    if not 1 <= int(pages_per_source) <= min(profile.max_pages_per_source, MAX_PAGES_PER_SOURCE):
        raise CanaryScopeError("canary permits at most two pages")
    if len(set(str(asin).upper() for asin in detail_asins)) > min(profile.max_detail_asins, MAX_DETAIL_ASINS):
        raise CanaryScopeError("canary permits at most five unique detail ASINs")
    for url in sources:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"amazon.es", "www.amazon.es"}:
            raise CanaryScopeError("canary source must be https Amazon.es")
        parts = [item.lower() for item in parsed.path.split("/") if item]
        if "zgbs" not in parts and parts[:2] != ["gp", "bestsellers"]:
            raise CanaryScopeError("canary source must be a Best Sellers URL")


def dry_run(profile: CanaryProfile, source_urls: Sequence[str], *, pages_per_source: int,
            detail_asins: Sequence[str] = (), task_id: str = "") -> dict[str, Any]:
    validate_scope(profile, source_urls, pages_per_source=pages_per_source, detail_asins=detail_asins, task_id=task_id)
    return {"schema_version": CANARY_SCHEMA_VERSION, "status": "DRY_RUN", "network_requests": 0,
            "stable_parser": profile.stable_parser, "candidate_parser": profile.candidate_parser,
            "source_urls": list(source_urls), "pages_per_source": int(pages_per_source),
            "detail_budget": min(profile.max_detail_asins, MAX_DETAIL_ASINS),
            "default_parser_changed": False, "promotion_requested": False}


def seal_stage(stage: str, input_data: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Bind each stage to the exact prior data and its output evidence."""
    return {"stage": stage, "schema_version": CANARY_SCHEMA_VERSION,
            "input_hash": _hash(input_data), "evidence_hash": _hash(evidence),
            "evidence": dict(evidence)}


def _verify_stage(value: Any, stage: str, input_data: Mapping[str, Any]) -> tuple[bool, Mapping[str, Any] | None, str]:
    if not isinstance(value, Mapping) or value.get("stage") != stage or value.get("schema_version") != CANARY_SCHEMA_VERSION:
        return False, None, "STAGE_MISSING_OR_VERSION_INVALID"
    evidence = value.get("evidence")
    if not isinstance(evidence, Mapping) or value.get("input_hash") != _hash(input_data) or value.get("evidence_hash") != _hash(evidence):
        return False, None, "STAGE_HASH_INVALID"
    return True, evidence, ""


def _ranking_index(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, tuple[str, str]], list[str], int]:
    index, duplicates, slots = {}, [], set()
    slot_duplicates = 0
    for row in rows:
        asin, rank, url = _asin(row), _rank(row), _url(row)
        if not asin or asin in index:
            duplicates.append(asin or "<empty>")
        index[asin] = (rank, url)
        slot = (str(row.get("ranking_source_url") or row.get("source_url") or ""),
                str(row.get("ranking_page_number") or row.get("page_number") or ""), rank)
        if rank and slot in slots:
            slot_duplicates += 1
        slots.add(slot)
    return index, duplicates, slot_duplicates


def shadow_diff(stable: Sequence[Mapping[str, Any]], candidate: Sequence[Mapping[str, Any]], *,
                expected_count: int | None = None) -> dict[str, Any]:
    """Compare V1 and V2 ranking outputs without choosing a winner."""
    left, left_dupes, left_slots = _ranking_index(stable)
    right, right_dupes, right_slots = _ranking_index(candidate)
    missing, extra = sorted(set(left) - set(right)), sorted(set(right) - set(left))
    changed = sorted(asin for asin in set(left) & set(right) if left[asin] != right[asin])
    ranks = {int(value[0]) for value in right.values() if str(value[0]).isdigit()}
    gaps = []
    if expected_count is not None and ranks:
        gaps = sorted(set(range(min(ranks), min(ranks) + int(expected_count))) - ranks)
    return {"equal": not (missing or extra or changed or left_dupes or right_dupes or left_slots or right_slots or gaps),
            "stable_count": len(left), "candidate_count": len(right), "expected_count": expected_count,
            "missing_asins": missing, "extra_asins": extra, "changed_asins": changed,
            "stable_duplicate_asins": left_dupes, "candidate_duplicate_asins": right_dupes,
            "stable_duplicate_slots": left_slots, "candidate_duplicate_slots": right_slots,
            "candidate_missing_slots": gaps}


def detail_diff(online: Sequence[Mapping[str, Any]], replay: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = ("requested_asin", "resolved_asin", "identity_status", "parent_asin",
              "variation_family_asins", "ordered_detail_evidence", "attributes", "category_evidence")
    left = {_asin(row): row for row in online}; right = {_asin(row): row for row in replay}
    mismatches = [asin for asin in set(left) | set(right)
                  if asin not in left or asin not in right or any(left[asin].get(field) != right[asin].get(field) for field in fields)]
    return {"equal": not mismatches, "online_count": len(left), "replay_count": len(right),
            "mismatched_asins": sorted(mismatches)}


def _stage_a(evidence: Mapping[str, Any]) -> bool:
    return (_access_is_normal(evidence.get("access_state")) and bool(evidence.get("expected_count"))
            and int(evidence.get("server_rendered_count") or 0) >= int(evidence.get("expected_count") or 0)
            and int(evidence.get("duplicate_asins") or 0) == 0 and int(evidence.get("missing_slots") or 0) == 0)


def _stage_c(evidence: Mapping[str, Any]) -> bool:
    legal = {"IDENTITY_MATCH", "PARENT_ASIN_MATCH", "VARIATION_RELATED", "MATCH_BY_EXTERNAL_EVIDENCE"}
    details = evidence.get("details") or []
    return (bool(details) and bool(evidence.get("replay_equal")) and
            all(_access_is_normal(row.get("access_state") or row.get("final_access_state")) and
                str(row.get("identity_status") or "").upper() in legal and
                "parent_asin" in row and isinstance(row.get("attributes") or row.get("ordered_detail_evidence"), list)
                for row in details))


def evaluate_candidate(profile: CanaryProfile, *, stable_rankings: Sequence[Mapping[str, Any]],
                       candidate_rankings: Sequence[Mapping[str, Any]], expected_count: int,
                       stage_a: Mapping[str, Any] | None, stage_b: Mapping[str, Any] | None,
                       stage_c: Mapping[str, Any] | None, candidate_replay_rankings: Sequence[Mapping[str, Any]],
                       details: Sequence[Mapping[str, Any]], detail_replay: Sequence[Mapping[str, Any]],
                       request_count: int = 0, access_state: str = "NORMAL") -> dict[str, Any]:
    """Evaluate bound A/B/C evidence. Passing does not alter V1 defaults."""
    base = {"stable_rankings": list(stable_rankings), "candidate_rankings": list(candidate_rankings),
            "expected_count": expected_count, "candidate_replay_rankings": list(candidate_replay_rankings),
            "details": list(details), "detail_replay": list(detail_replay), "request_count": request_count,
            "access_state": access_state}
    findings: list[str] = []
    if request_count > 0:
        findings.append("NETWORK_REQUESTS_NOT_ALLOWED_BY_OFFLINE_EVALUATOR")
    if not _access_is_normal(access_state):
        findings.append("ACCESS_STOP_ALL")
    if len({ _asin(row) for row in details if _asin(row) }) > min(profile.max_detail_asins, MAX_DETAIL_ASINS):
        findings.append("DETAIL_BUDGET_EXCEEDED")
    ok_a, ev_a, code_a = _verify_stage(stage_a, "A", {"stable_rankings": base["stable_rankings"], "expected_count": expected_count})
    if not ok_a or not _stage_a(ev_a): findings.append("STAGE_A_" + (code_a if not ok_a else "FAILED"))
    diff = shadow_diff(stable_rankings, candidate_rankings, expected_count=expected_count)
    replay_diff = shadow_diff(candidate_rankings, candidate_replay_rankings, expected_count=expected_count)
    ok_b, ev_b, code_b = _verify_stage(stage_b, "B", {"candidate_rankings": base["candidate_rankings"], "expected_count": expected_count})
    if not ok_b or not bool(ev_b.get("shadow_equal")) or not diff["equal"] or not replay_diff["equal"]:
        findings.append("STAGE_B_" + (code_b if not ok_b else "SHADOW_OR_REPLAY_DIFF"))
    detail_replay_result = detail_diff(details, detail_replay)
    ok_c, ev_c, code_c = _verify_stage(stage_c, "C", {"details": base["details"], "detail_replay": base["detail_replay"]})
    if not ok_c or not bool(ev_c.get("detail_replay_equal")) or not detail_replay_result["equal"] or not _stage_c(ev_c):
        findings.append("STAGE_C_" + (code_c if not ok_c else "DETAIL_OR_REPLAY_DIFF"))
    status = "CANDIDATE_NOT_PROMOTABLE" if findings else "PROMOTABLE"
    return {"schema_version": CANARY_SCHEMA_VERSION, "status": status, "promoted": False,
            "default_parser_changed": False, "findings": findings, "shadow_diff": diff,
            "ranking_replay_diff": replay_diff, "detail_replay_diff": detail_replay_result,
            "evidence_hash": _hash(base)}


__all__ = ["CANARY_SCHEMA_VERSION", "CanaryProfile", "CanaryScopeError", "load_profile", "validate_scope",
           "dry_run", "seal_stage", "shadow_diff", "detail_diff", "evaluate_candidate"]
