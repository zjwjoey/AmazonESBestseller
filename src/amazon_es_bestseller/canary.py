# -*- coding: utf-8 -*-
"""Bounded, fail-closed V2 Canary orchestration.

The canary is deliberately an orchestration boundary.  It does not change
the ranking/detail parsers and it never writes production state.  The pure
gates in this module are also used by the offline test suite so a real run
cannot silently skip its evidence or replay checks.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


CANARY_SCOPE_EXCEEDED = "CANARY_SCOPE_EXCEEDED"
CANARY_STOPPED_BY_ACCESS_GATE = "CANARY_STOPPED_BY_ACCESS_GATE"
MAX_SOURCES = 1
MAX_PAGES_PER_SOURCE = 2
MAX_DETAIL_ASINS = 5


class CanaryScopeExceeded(ValueError):
    code = CANARY_SCOPE_EXCEEDED

    def __init__(self, message: str):
        super().__init__(f"{self.code}: {message}")


class CanaryExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanaryProfile:
    profile_version: str = "amazon_es_v2_canary_v1"
    marketplace: str = "ES"
    ranking_parser: str = "v2"
    detail_parser: str = "v2"
    transport: str = "PLAYWRIGHT"
    max_sources: int = MAX_SOURCES
    max_pages_per_source: int = MAX_PAGES_PER_SOURCE
    max_detail_asins: int = MAX_DETAIL_ASINS
    allow_production_write: bool = False
    allow_excel_write: bool = False
    allow_translation: bool = False
    allow_image_download: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CanaryProfile":
        values = dict(value)
        profile = cls(**{field_name: values[field_name]
                         for field_name in cls.__dataclass_fields__
                         if field_name in values})
        if profile.marketplace.upper() != "ES":
            raise ValueError("Canary 只允许 marketplace=ES")
        if profile.ranking_parser.lower() not in {"v2", "collection.ranking_v2"}:
            raise ValueError("Canary 只允许 ranking_parser=v2")
        if profile.detail_parser.lower() not in {"v2", "collection.detail_v2"}:
            raise ValueError("Canary 只允许 detail_parser=v2")
        if profile.transport.upper() != "PLAYWRIGHT":
            raise ValueError("Canary 只允许 transport=PLAYWRIGHT")
        for name, maximum in (("max_sources", MAX_SOURCES),
                              ("max_pages_per_source", MAX_PAGES_PER_SOURCE),
                              ("max_detail_asins", MAX_DETAIL_ASINS)):
            actual = int(getattr(profile, name))
            if actual < 0 or actual > maximum:
                raise CanaryScopeExceeded(f"{name}={actual}，上限为 {maximum}")
        if any(getattr(profile, name) for name in (
                "allow_production_write", "allow_excel_write",
                "allow_translation", "allow_image_download")):
            raise ValueError("Canary profile 禁止生产、Excel、翻译和图片写入")
        return profile


def load_profile(path: str | Path) -> CanaryProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Canary profile 必须是 JSON object")
    return CanaryProfile.from_mapping(payload)


def validate_scope(profile: CanaryProfile, source_urls: Sequence[str],
                   pages_per_source: int, detail_asins: Sequence[str] | None = None) -> None:
    """Validate the hard budget before any browser/transport is constructed."""
    sources = [str(url).strip() for url in source_urls if str(url).strip()]
    detail_count = len(list(detail_asins or []))
    if not sources:
        raise CanaryScopeExceeded("sources 至少为 1")
    if len(sources) > profile.max_sources or len(sources) > MAX_SOURCES:
        raise CanaryScopeExceeded(f"sources={len(sources)}，上限为 {MAX_SOURCES}")
    if int(pages_per_source) > profile.max_pages_per_source or int(pages_per_source) > MAX_PAGES_PER_SOURCE:
        raise CanaryScopeExceeded(
            f"pages_per_source={pages_per_source}，上限为 {MAX_PAGES_PER_SOURCE}")
    if int(pages_per_source) < 1:
        raise CanaryScopeExceeded("pages_per_source 必须至少为 1")
    if detail_count > profile.max_detail_asins or detail_count > MAX_DETAIL_ASINS:
        raise CanaryScopeExceeded(f"detail_asins={detail_count}，上限为 {MAX_DETAIL_ASINS}")


@dataclass
class RequestAccounting:
    ranking_page_requests: int = 0
    acp_requests: int = 0
    detail_page_requests: int = 0

    @property
    def total_requests(self) -> int:
        return (self.ranking_page_requests + self.acp_requests +
                self.detail_page_requests)

    def to_dict(self) -> dict[str, int]:
        return {"ranking_page_requests": self.ranking_page_requests,
                "acp_requests": self.acp_requests,
                "detail_page_requests": self.detail_page_requests,
                "total_requests": self.total_requests}


def ranking_tuple(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("asin") or row.get("ranking_asin") or "").upper(),
        str(row.get("ranking_rank") or row.get("bestseller_rank") or ""),
        str(row.get("ranking_source_url") or ""),
        str(row.get("ranking_page_url") or row.get("source_url") or ""),
        str(row.get("ranking_page_number") or row.get("page_number") or ""),
    )


def compare_replay_tuples(online: Sequence[Mapping], offline: Sequence[Mapping]) -> dict:
    online_tuples = [ranking_tuple(row) for row in online]
    offline_tuples = [ranking_tuple(row) for row in offline]
    return {"equal": online_tuples == offline_tuples,
            "online_count": len(online_tuples),
            "offline_count": len(offline_tuples),
            "online": online_tuples,
            "offline": offline_tuples,
            "mismatch_index": next((index for index, pair in enumerate(zip(online_tuples, offline_tuples))
                                     if pair[0] != pair[1]),
                                    min(len(online_tuples), len(offline_tuples))
                                    if len(online_tuples) != len(offline_tuples) else None)}


def _truth(value: Any) -> bool:
    return value is True or str(value).upper() in {"TRUE", "1", "YES", "NORMAL", "PASS"}


def gate_stage_a(manifest: Mapping[str, Any]) -> dict:
    """Evaluate the one-page ranking authority gate without side effects."""
    audit = manifest.get("ranking_v2_audit") or manifest.get("ranking_audit") or {}
    pages = list(audit.get("pages") or [])
    identity = manifest.get("authority_gates") or {}
    status_rows = list(manifest.get("page_statuses") or manifest.get("source_statuses") or [])
    access_states = [str(state).upper() for state in (manifest.get("access_state_summary") or {})]
    access_states.extend(str(row.get("access_state") or row.get("status") or "NORMAL").upper()
                         for row in [*status_rows, *pages] if isinstance(row, Mapping))
    access_normal = not access_states or all(state == "NORMAL" for state in access_states)
    acp_not_needed = bool(pages) and all(
        int(page.get("acp_hydrated_count") or 0) == 0 and
        page.get("expected_count") is not None and
        int(page.get("server_rendered_count") or 0) >= int(page.get("expected_count") or 0)
        for page in pages)
    checks = {
        "access_normal": access_normal and str(manifest.get("access_state") or "NORMAL").upper() == "NORMAL",
        "expected_count_known": bool(pages) and all(page.get("expected_count") is not None for page in pages),
        "ranking_complete": _truth(manifest.get("ranking_complete") or audit.get("ranking_complete")),
        "identity_ready": _truth(manifest.get("ranking_identity_ready") or identity.get("identity_ready")),
        "identity_complete": _truth(manifest.get("ranking_identity_complete") or identity.get("identity_complete")),
        "ranking_slot_complete": _truth(manifest.get("ranking_slot_complete") or identity.get("ranking_slots_complete")),
        "identity_conflicts_zero": int(manifest.get("identity_conflict_count") or 0) == 0,
        "rank_gaps_zero": int(audit.get("rank_gap_count") or 0) == 0,
        "rank_duplicates_zero": int(audit.get("rank_duplicate_count") or 0) == 0,
        "final_authoritative": _truth(manifest.get("final_authoritative") or
                                       manifest.get("latest_authoritative")),
        "acp_evidence_saved": bool(manifest.get("acp_evidence_files") or
                                    manifest.get("acp_not_required") or
                                    (manifest.get("ranking_v2_audit") or {}).get("acp_not_required") or
                                    acp_not_needed),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {"status": "PASS" if not failed else "FAIL", "checks": checks,
            "failed_checks": failed}


def gate_stage_b(manifest: Mapping[str, Any], replay: Mapping[str, Any] | None = None) -> dict:
    audit = manifest.get("ranking_v2_audit") or manifest.get("ranking_audit") or {}
    pages = list(audit.get("pages") or [])
    checks = {
        "two_pages": len(pages) == 2,
        "page_instance_bound": bool(pages) and all(bool(page.get("page_instance_id")) for page in pages),
        "each_page_authoritative": bool(pages) and all(_truth(page.get("page_authoritative")) for page in pages),
        "each_page_complete": bool(pages) and all(_truth(page.get("page_complete")) for page in pages),
        "final_authoritative": _truth(manifest.get("final_authoritative") or manifest.get("latest_authoritative")),
        "ranking_slot_complete": _truth(manifest.get("ranking_slot_complete") or
                                         (manifest.get("authority_gates") or {}).get("ranking_slots_complete")),
        "identity_complete": _truth(manifest.get("ranking_identity_complete") or
                                     (manifest.get("authority_gates") or {}).get("identity_complete")),
        "rank_gaps_zero": int(audit.get("rank_gap_count") or 0) == 0,
        "rank_duplicates_zero": int(audit.get("rank_duplicate_count") or 0) == 0,
        "offline_replay_equal": bool(replay and replay.get("equal")),
    }
    failed = [name for name, passed in checks.items() if not passed]
    page_reports = [{
        "page_instance_id": page.get("page_instance_id"),
        "ranking_source_url": page.get("ranking_source_url") or page.get("source_url"),
        "ranking_page_url": page.get("ranking_page_url") or page.get("source_url"),
        "ranking_page_number": page.get("page_number"),
        "server_rendered_count": page.get("server_rendered_count"),
        "expected_count": page.get("expected_count"),
        "acp_hydrated_count": page.get("acp_hydrated_count"),
        "final_record_count": page.get("unique_asin_count") or page.get("observed_count") or 0,
        "page_authoritative": bool(page.get("page_authoritative")),
    } for page in pages]
    required_page_fields = ("page_instance_id", "ranking_source_url", "ranking_page_url",
                            "ranking_page_number", "expected_count", "server_rendered_count",
                            "acp_hydrated_count")
    checks["page_fields_present"] = bool(pages) and all(
        all(report.get(field) is not None for field in required_page_fields)
        for report in page_reports)
    if not checks["page_fields_present"]:
        failed.append("page_fields_present")
    return {"status": "PASS" if not failed else "FAIL", "checks": checks,
            "failed_checks": failed, "page_reports": page_reports}


def sample_detail_asins(records: Sequence[Mapping], limit: int = MAX_DETAIL_ASINS) -> tuple[list[str], dict]:
    unique = {}
    for row in records:
        asin = str(row.get("asin") or row.get("ranking_asin") or "").strip().upper()
        if asin and asin not in unique:
            unique[asin] = dict(row)
    def rank_value(row: Mapping) -> int:
        try:
            return int(row.get("ranking_rank") or row.get("bestseller_rank") or 10**9)
        except (TypeError, ValueError):
            return 10**9

    ordered = sorted(unique.values(), key=rank_value)
    if len(ordered) < limit:
        raise CanaryExecutionError(f"Stage C 需要 {limit} 个来自 Stage B 的唯一 ASIN，实际 {len(ordered)}")
    selected: list[dict] = []
    for index in (0, len(ordered) // 2, len(ordered) - 1):
        candidate = ordered[index]
        if candidate not in selected:
            selected.append(candidate)
    variation = next((row for row in ordered if row.get("parent_asin") or
                      row.get("variation_family_asins") or row.get("variation_evidence")), None)
    standalone = next((row for row in ordered if not (row.get("parent_asin") or
                       row.get("variation_family_asins") or row.get("variation_evidence"))), None)
    if variation and variation not in selected:
        selected.append(variation)
    if standalone and standalone not in selected:
        selected.append(standalone)
    for row in ordered:
        if len(selected) >= limit:
            break
        if row not in selected:
            selected.append(row)
    return [str(row.get("asin") or row.get("ranking_asin")).upper() for row in selected[:limit]], {
        "variation_observed": bool(variation),
        "variation_status": "OBSERVED" if variation else "VARIATION_CASE_NOT_OBSERVED",
        "standalone_observed": bool(standalone),
    }


def gate_stage_c(details: Sequence[Mapping], sampled_asins: Sequence[str], sampling: Mapping) -> dict:
    return gate_stage_c_with_replay(details, sampled_asins, sampling, None)


def gate_stage_c_with_replay(details: Sequence[Mapping], sampled_asins: Sequence[str],
                             sampling: Mapping, offline_replay: Mapping | None) -> dict:
    by_asin = {str(row.get("asin") or row.get("requested_asin") or "").upper(): row
               for row in details}
    legal_identity = {"MATCH", "PARENT_ASIN_MATCH", "VARIATION_RELATED",
                      "MATCH_BY_EXTERNAL_EVIDENCE"}
    checks = {"sample_count": len(sampled_asins) == MAX_DETAIL_ASINS,
              "all_sampled_have_results": all(asin in by_asin for asin in sampled_asins),
              "all_v2": bool(details) and all(str(row.get("detail_parser_version") or
                                                   row.get("parser_version") or "").lower() in
                                                {"v2", "collection.detail_v2"} for row in details),
              "requested_asins_match": bool(details) and all(
                  str(row.get("requested_asin") or "").upper() == asin
                  for asin, row in by_asin.items() if asin in set(sampled_asins)),
              "access_normal": bool(details) and all(str(row.get("access_state") or
                                                         row.get("final_access_state") or "NORMAL").upper()
                                                      == "NORMAL" for row in details),
              "identity_review_free": bool(details) and all(str(row.get("identity_status") or "").upper()
                                                             not in {"IDENTITY_MISMATCH", "IDENTITY_REVIEW",
                                                                     "IDENTITY_UNCONFIRMED"}
                                                             for row in details),
              "identity_status_legal": bool(details) and all(
                  str(row.get("identity_status") or "").upper() in legal_identity
                  for row in details),
              "variation_reported": bool(sampling.get("variation_status")),
              "offline_reparse_equal": bool(offline_replay and offline_replay.get("equal")),
              "ordered_evidence_present": bool(details) and all(
                  isinstance(row.get("ordered_detail_evidence"), list) for row in details),
              "category_provenance_present": bool(details) and all(
                  isinstance(row.get("category_evidence"), Mapping) for row in details),
              "saved_html_replay_available": bool(offline_replay),
              "detail_contract": bool(details) and all(int(row.get("detail_parser_contract_version") or 0) == 2
                                                        for row in details)}
    failed = [name for name, passed in checks.items() if not passed]
    return {"status": "PASS" if not failed else "FAIL", "checks": checks,
            "failed_checks": failed,
            "variation_status": sampling.get("variation_status")}


def compare_detail_replay(online: Sequence[Mapping], offline: Sequence[Mapping]) -> dict:
    """Compare stable V2 detail evidence fields from live and saved HTML replay."""
    fields = ("requested_asin", "resolved_asin", "parent_asin", "variation_family_asins",
              "identity_status", "detail_parser_version", "detail_parser_contract_version",
              "ordered_detail_evidence", "category_evidence")
    online_by_asin = {str(row.get("requested_asin") or row.get("asin") or "").upper(): row
                      for row in online}
    offline_by_asin = {str(row.get("requested_asin") or row.get("asin") or "").upper(): row
                       for row in offline}
    asins = list(online_by_asin)
    mismatches = []
    for asin in asins:
        left, right = online_by_asin[asin], offline_by_asin.get(asin)
        if right is None or any(left.get(field) != right.get(field) for field in fields):
            mismatches.append(asin)
    return {"equal": not mismatches and set(online_by_asin) == set(offline_by_asin),
            "online_count": len(online_by_asin), "offline_count": len(offline_by_asin),
            "mismatched_asins": mismatches}


def new_run_dir(output_root: str | Path) -> Path:
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    path = Path(output_root) / now.strftime("%Y-%m-%d") / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def dry_run(profile: CanaryProfile, source_urls: Sequence[str], pages_per_source: int,
            detail_asins: Sequence[str] | None, output_root: str | Path) -> dict:
    validate_scope(profile, source_urls, pages_per_source, detail_asins)
    sources = [str(url) for url in source_urls]
    return {"status": "DRY_RUN", "network_requests": 0,
            "source_urls": sources,
            "planned_pages": [{"source_url": url, "page_number": page}
                              for url in sources for page in range(1, int(pages_per_source) + 1)],
            "max_acp_requests": len(sources) * int(pages_per_source),
            "planned_detail_asins": len(list(detail_asins or [])),
            "max_detail_asins": profile.max_detail_asins,
            "output_root": str(Path(output_root)),
            "ranking_parser": profile.ranking_parser,
            "detail_parser": profile.detail_parser,
            "transport": profile.transport,
            "allow_production_write": profile.allow_production_write,
            "actual_requests": RequestAccounting().to_dict()}


def build_manifest(profile: CanaryProfile, run_dir: str | Path, *, source_urls: Sequence[str],
                   stage_status: Mapping[str, str], accounting: RequestAccounting,
                   **extra: Any) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stage_status = dict(stage_status)
    manifest = {"run_id": Path(run_dir).name, "started_at": now, "completed_at": now,
                "git_sha": str(extra.pop("git_sha", "")),
                "marketplace": profile.marketplace, "source_urls": list(source_urls),
                "ranking_parser": profile.ranking_parser,
                "detail_parser": profile.detail_parser, "transport": profile.transport,
                "ranking_parser_version": profile.ranking_parser,
                "detail_parser_version": profile.detail_parser,
                "stage": next((name for name in ("C", "B", "A")
                                if stage_status.get(name) not in {"NOT_RUN", None}), "A"),
                "stage_status": stage_status, "request_budget": {
                    "max_sources": profile.max_sources,
                    "max_pages_per_source": profile.max_pages_per_source,
                    "max_detail_asins": profile.max_detail_asins},
                "actual_requests": accounting.to_dict(),
                "access_state": extra.get("access_state", "NORMAL"),
                "ranking_authoritative": bool(extra.get("ranking_authoritative", False)),
                "identity_authoritative": bool(extra.get("identity_authoritative", False)),
                "offline_replay_equal": bool(extra.get("offline_replay_equal", False)),
                "detail_v2_pass": bool(extra.get("detail_v2_pass", False)),
                "final_status": extra.pop("final_status", "CANARY_FAILED")}
    manifest.update(extra)
    path = Path(run_dir) / "canary_manifest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return manifest


class _CountingTransport:
    """Count requests while preserving the existing transport contract."""

    def __init__(self, delegate, accounting: RequestAccounting):
        self.delegate = delegate
        self.accounting = accounting
        self.name = getattr(delegate, "name", "PLAYWRIGHT")
        self.primary = getattr(delegate, "primary", True)

    def fetch_page(self, *args, **kwargs):
        self.accounting.ranking_page_requests += 1
        return self.delegate.fetch_page(*args, **kwargs)

    def fetch_ajax(self, *args, **kwargs):
        self.accounting.acp_requests += 1
        return self.delegate.fetch_ajax(*args, **kwargs)


class _CountingSession:
    def __init__(self, delegate, accounting: RequestAccounting):
        self._delegate = delegate
        self._accounting = accounting

    def goto(self, *args, **kwargs):
        self._accounting.detail_page_requests += 1
        return self._delegate.goto(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def run_live(profile: CanaryProfile, source_urls: Sequence[str], pages_per_source: int,
             output_root: str | Path, *, headful: bool = False,
             profile_dir: str = "", git_sha: str = "") -> dict:
    """Run the explicitly-authorized bounded canary.

    This function is intentionally unreachable from the CLI without the
    explicit ``--execute-real-amazon`` switch.  It reuses the existing browser,
    ranking and detail V2 implementations and only gives them an independent
    output root.
    """
    validate_scope(profile, source_urls, pages_per_source, [])
    if profile.allow_production_write or profile.allow_excel_write or profile.allow_translation or profile.allow_image_download:
        raise CanaryExecutionError("Canary profile 禁止生产写入、Excel、翻译和图片下载")
    from .access.browser import BrowserSession
    from .access.detector import AccessStopError
    from .collection.detail_executor import execute_detail_plan
    from .collection.detail import collect_details
    from .collection.detail_v2 import parse_detail_evidence_v2
    from .collection.ranking_v2 import replay_ranking_snapshot_v2
    from .monitoring.detail_planner import build_detail_plan
    from .monitoring.snapshot import collect_ranking_snapshot
    from .transport.playwright import PlaywrightTransport

    def raise_if_access_blocked(manifest: Mapping) -> None:
        states = list((manifest.get("access_state_summary") or {}).keys())
        states.extend(str(row.get("access_state") or row.get("status") or "")
                      for row in (manifest.get("page_statuses") or [])
                      if isinstance(row, Mapping))
        blocked = {"BLOCKED", "CHALLENGE", "RATE_LIMITED", "CAPTCHA", "ACCESS_DENIED",
                   "BOT_BLOCK", "INTERSTITIAL", "403", "429"}
        if any(str(state).upper() in blocked for state in states):
            raise AccessStopError(str(manifest.get("error") or "Canary observed access restriction"))

    run_dir = new_run_dir(output_root)
    accounting = RequestAccounting()
    stage_status = {"A": "NOT_RUN", "B": "NOT_RUN", "C": "NOT_RUN"}
    extra: dict[str, Any] = {"access_state": "NORMAL", "ranking_authoritative": False,
                             "identity_authoritative": False, "offline_replay_equal": False}
    try:
        with BrowserSession(headless=not headful, profile_dir=profile_dir or None) as session:
            transport = _CountingTransport(PlaywrightTransport(session), accounting)
            stage_a_result = collect_ranking_snapshot(
                [str(source_urls[0])], session, run_dir / "stage_a",
                pages_per_url=1, parser_version="v2", transport=transport)
            stage_a_manifest = stage_a_result["manifest"]
            raise_if_access_blocked(stage_a_manifest)
            replay_a = replay_ranking_snapshot_v2(stage_a_result["path"])
            compare_a = compare_replay_tuples(stage_a_result["records"], replay_a["records"])
            gate_a = gate_stage_a(stage_a_manifest)
            gate_a["offline_replay"] = compare_a
            if not compare_a["equal"]:
                gate_a["status"] = "FAIL"
                gate_a["failed_checks"] = list(gate_a["failed_checks"]) + ["offline_replay_equal"]
            stage_status["A"] = gate_a["status"]
            extra.update({"stage_a_gate": gate_a,
                          "ranking_authoritative": bool(stage_a_manifest.get("final_authoritative")),
                          "identity_authoritative": bool(stage_a_manifest.get("ranking_identity_complete"))})
            if gate_a["status"] != "PASS":
                raise CanaryExecutionError("Stage A gate 未通过")

            stage_b_result = collect_ranking_snapshot(
                [str(source_urls[0])], session, run_dir / "stage_b",
                pages_per_url=2, parser_version="v2", transport=transport)
            stage_b_manifest = stage_b_result["manifest"]
            raise_if_access_blocked(stage_b_manifest)
            replay_b = replay_ranking_snapshot_v2(stage_b_result["path"])
            compare_b = compare_replay_tuples(stage_b_result["records"], replay_b["records"])
            gate_b = gate_stage_b(stage_b_manifest, compare_b)
            stage_status["B"] = gate_b["status"]
            extra["stage_b_gate"] = gate_b
            extra["offline_replay_equal"] = bool(compare_b["equal"])
            if gate_b["status"] != "PASS":
                raise CanaryExecutionError("Stage B gate 未通过")

            sampled, sampling = sample_detail_asins(stage_b_result["records"], MAX_DETAIL_ASINS)
            plan = build_detail_plan({**stage_b_manifest, "records": stage_b_result["records"]},
                                     target_parser_version="v2",
                                     current_access_state="NORMAL")
            selected_plan = dict(plan)
            selected_plan["records"] = [row for row in plan["records"]
                                         if str(row.get("asin") or "").upper() in set(sampled)]
            detail_session = _CountingSession(session, accounting)
            detail_result = execute_detail_plan(
                selected_plan, detail_session, str(run_dir / "stage_c"),
                collector=collect_details, parser_version="v2")
            details = detail_result.get("details") or []
            offline_details = []
            for row in details:
                asin = str(row.get("requested_asin") or row.get("asin") or "").upper()
                html_path = run_dir / "stage_c" / "html" / f"{asin}.html"
                if not html_path.exists():
                    continue
                offline_details.append(parse_detail_evidence_v2(
                    html_path.read_text(encoding="utf-8"), asin,
                    requested_url=str(row.get("requested_url") or ""),
                    final_url=str(row.get("final_url") or ""),
                    ranking_context=row.get("ranking_context")
                    if isinstance(row.get("ranking_context"), Mapping) else None))
            detail_replay = compare_detail_replay(details, offline_details)
            gate_c = gate_stage_c_with_replay(details, sampled, sampling, detail_replay)
            stage_status["C"] = gate_c["status"]
            extra.update({"stage_c_gate": gate_c, "sampled_detail_asins": sampled,
                          "sampling": sampling, "detail_offline_replay": detail_replay,
                          "detail_v2_pass": gate_c["status"] == "PASS"})
            final_status = "CANARY_PASS" if gate_c["status"] == "PASS" else "CANARY_FAILED"
    except AccessStopError as exc:
        if stage_status["A"] == "NOT_RUN":
            stage_status["A"] = "BLOCKED_BY_ACCESS"
        elif stage_status["B"] == "NOT_RUN" and stage_status["A"] == "PASS":
            stage_status["B"] = "BLOCKED_BY_ACCESS"
        elif stage_status["C"] == "NOT_RUN" and stage_status["B"] == "PASS":
            stage_status["C"] = "BLOCKED_BY_ACCESS"
        extra.update({"access_state": "BLOCKED", "access_stop_reason": str(exc),
                      "stop_code": CANARY_STOPPED_BY_ACCESS_GATE})
        final_status = "CANARY_BLOCKED_BY_ACCESS"
    except Exception as exc:
        extra["error"] = str(exc)
        final_status = "CANARY_FAILED"
    manifest = build_manifest(profile, run_dir, source_urls=source_urls,
                              stage_status=stage_status, accounting=accounting,
                              git_sha=git_sha, final_status=final_status, **extra)
    return manifest
