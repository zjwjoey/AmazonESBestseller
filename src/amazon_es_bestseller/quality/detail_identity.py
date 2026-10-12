"""Detail identity gate using the shared identity resolver."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import re

from ..identity import IDENTITY_MATCH, MATCH_BY_EXTERNAL_EVIDENCE, PARENT_ASIN_MATCH, VARIATION_RELATED, resolve_identity
from ..models import normalize_asin
from .models import QualityStatus, check_result, issue


_ACCEPTED = {IDENTITY_MATCH, PARENT_ASIN_MATCH, VARIATION_RELATED, MATCH_BY_EXTERNAL_EVIDENCE, "MATCH"}

DETAIL_IDENTITY_REVIEW_VERSION = "visible-asin-roles-v1"
_DIRECTION_CONTROLS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def normalized_identity_value(value: object) -> str:
    """Normalize only the comparison value; never rewrite the raw source."""
    return _DIRECTION_CONTROLS.sub("", str(value or "")).strip().upper()


def extract_detail_identity_signals(html: str) -> dict:
    """Collect typed signals from archived HTML, excluding recommendation ASINs."""
    from lxml import html as lxml_html

    dom = lxml_html.fromstring(html)
    inputs = dom.xpath('//input[@id="ASIN" or @name="ASIN" or @id="productAsin"]/@value')
    canonicals = dom.xpath('//link[contains(concat(" ", normalize-space(@rel), " "), " canonical ")]/@href')
    from ..identity import asin_from_url

    def valid(values):
        return sorted({normalized_identity_value(v) for v in values
                       if re.fullmatch(r"[A-Z0-9]{10}", normalized_identity_value(v))})

    scripts = "\n".join(dom.xpath('//script/text()'))
    return {"producer": "saved_html_identity_review", "rules_version": DETAIL_IDENTITY_REVIEW_VERSION,
            "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "input_asins": valid(inputs), "canonical_asins": valid(asin_from_url(v) for v in canonicals),
            "current_asins": valid(re.findall(r'"currentAsin"\s*:\s*"([A-Z0-9]{10})"', scripts, re.I)),
            "parent_asins": valid(re.findall(r'"parentAsin"\s*:\s*"([A-Z0-9]{10})"', scripts, re.I))}


def review_visible_asin_identity(row: Mapping, signals: Mapping | None = None) -> dict:
    """Distinguish exact values, source-proven parent values and contradictions.

    A parent difference remains REVIEW: a shared detail table does not prove
    every measurement belongs to the selected child. A bare parent field or
    matching endpoint must never mask unrelated strong page signals.
    """
    asin = normalized_identity_value(row.get("asin"))
    signals = signals or {}
    occurrences = []
    for index, attr in enumerate(row.get("attributes") or []):
        if isinstance(attr, Mapping) and normalized_identity_value(attr.get("label_raw")) == "ASIN":
            occurrences.append({"field": "attributes", "index": index, "raw": attr.get("value_raw")})
    for index, bullet in enumerate(row.get("detail_bullets_raw") or []):
        match = re.match(r"^\s*ASIN\s*[:：]\s*(.+)$", _DIRECTION_CONTROLS.sub("", str(bullet)), re.I)
        if match:
            occurrences.append({"field": "detail_bullets_raw", "index": index,
                                "raw": re.split(r"[:：]", str(bullet), maxsplit=1)[1]})
    values = [normalized_identity_value(o["raw"]) for o in occurrences]
    parent_set = set(signals.get("parent_asins") or [])
    child_signals = set(signals.get("input_asins") or []) | set(signals.get("current_asins") or [])
    # Require an unambiguous child anchor and one explicit parent in HTML.
    parent_proven = len(parent_set) == 1 and child_signals == {asin} and parent_set != {asin}
    allowed = {asin} | (parent_set if parent_proven else set())
    strong_conflicts = [dict(source=k, asin=v) for k in ("input_asins", "current_asins", "canonical_asins")
                        for v in signals.get(k, [])
                        if v not in ({asin} if k != "canonical_asins" else allowed)]
    conflicts = [v for v in values if v not in allowed]
    related = sorted(set(values) & (parent_set if parent_proven else set()))
    # A canonical parent remains reviewable even if a visible ASIN is absent.
    related = sorted(set(related) | (set(signals.get("canonical_asins") or []) & (parent_set if parent_proven else set())))
    normalized = [o for o, v in zip(occurrences, values) if str(o["raw"] or "").strip().upper() != v]
    if conflicts or strong_conflicts:
        status = "IDENTITY_CONFLICT"
    elif related:
        status = "PARENT_DETAIL_REVIEW_REQUIRED"
    elif occurrences or child_signals:
        status = "EXACT_ASIN"
    else:
        status = "NO_DETAIL_ASIN"
    return {"rules_version": DETAIL_IDENTITY_REVIEW_VERSION, "asin": asin, "status": status,
            "occurrences": occurrences, "normalized_only": normalized,
            "conflicts": conflicts, "strong_signal_conflicts": strong_conflicts,
            "parent_related_values": related, "signals": dict(signals),
            "child_attribute_scope_confirmed": status == "EXACT_ASIN"}


def _family(row: Mapping) -> list[str]:
    value = row.get("variation_family_asins") or row.get("family_asins") or []
    if isinstance(value, str):
        value = [value]
    return [normalize_asin(item) for item in value if normalize_asin(item)]


def audit_detail_identity(
    details: Iterable[Mapping], rankings: Iterable[Mapping] = (), *,
    asins: set[str] | None = None,
) -> object:
    detail_rows = [dict(row) for row in (details or []) if isinstance(row, Mapping)]
    ranking_by_asin = {}
    for row in rankings or ():
        if isinstance(row, Mapping):
            key = normalize_asin(row.get("asin") or row.get("ranking_asin"))
            if key:
                ranking_by_asin.setdefault(key, row)
    issues = []
    checked = 0
    for row in detail_rows:
        requested = normalize_asin(row.get("requested_asin") or row.get("asin"))
        ranking = normalize_asin(row.get("ranking_asin") or requested)
        if asins and ranking not in asins and requested not in asins:
            continue
        checked += 1
        ranking_row = ranking_by_asin.get(ranking, {})
        final_url = row.get("final_url") or row.get("final_url_asin") or ""
        canonical = row.get("canonical_asin") or ""
        parsed = row.get("parsed_detail_asin") or row.get("page_asin") or row.get("resolved_asin") or row.get("asin")
        result = resolve_identity(
            ranking_asin=ranking,
            requested_asin=requested,
            requested_url=row.get("requested_url") or row.get("requested_url_raw")
            or ranking_row.get("ranking_product_url_raw") or "",
            final_url_asin=final_url,
            canonical_asin=canonical,
            embedded_asins=row.get("embedded_asins") or row.get("page_asin_candidates") or [],
            parsed_detail_asin=parsed,
            parent_asin=row.get("parent_asin") or row.get("parent_asin_raw") or "",
            variation_family_asins=_family(row),
        )
        status = str(row.get("identity_status") or result.get("identity_status") or "").upper()
        detail_status = str(row.get("detail_status") or "").upper()
        identity_event = str(row.get("identity_event") or "").upper()
        if (detail_status == "REQUEST_URL_BINDING_MISMATCH" or
                identity_event == "REQUEST_URL_BINDING_MISMATCH"):
            issues.append(issue(
                "detail_identity", QualityStatus.BLOCK, "P0", "REQUEST_URL_BINDING_MISMATCH",
                asin=ranking or requested, source="detail",
                message="详情请求 URL 与冻结候选计划绑定不一致；页面未被可信请求。",
                evidence={"record": row, "resolver": result}))
            continue
        if (detail_status == "SUCCESS_WITH_IDENTITY_CHANGE" or
                identity_event == "NAVIGATION_IDENTITY_CHANGED"):
            issues.append(issue(
                "detail_identity", QualityStatus.REVIEW, "P1", "NAVIGATION_IDENTITY_CHANGED",
                asin=ranking or requested, source="detail",
                message="冻结榜单链接落地到不同或未确认的商品身份；已保留详情证据，需人工复核。",
                evidence={"record": row, "resolver": result}))
            continue
        if status in _ACCEPTED:
            # A contradictory final URL is not masked by a matching parsed
            # field unless explicit variation-family evidence explains it.
            final_asin = str(result.get("identity_evidence") and next(
                (item.get("asin") for item in result["identity_evidence"]
                 if item.get("source") == "final_url_asin"), "") or "")
            family = set(_family(row))
            if final_asin and final_asin != ranking and final_asin not in family:
                issues.append(issue(
                    "detail_identity", QualityStatus.BLOCK, "P1", "IDENTITY_MISMATCH",
                    asin=ranking or requested, source="detail",
                    message="详情最终 URL 指向另一 ASIN，且没有变体族证据解释。",
                    evidence={"requested_asin": requested, "ranking_asin": ranking,
                              "final_url_asin": final_asin, "resolver": result}))
        elif status in {"IDENTITY_UNCONFIRMED", "UNCONFIRMED", ""}:
            issues.append(issue(
                "detail_identity", QualityStatus.REVIEW, "P1", "IDENTITY_UNCONFIRMED",
                asin=ranking or requested, source="detail",
                message="详情页身份证据不足，需要人工确认。",
                evidence={"resolver": result, "record": row}))
        else:
            issues.append(issue(
                "detail_identity", QualityStatus.BLOCK, "P1", "IDENTITY_MISMATCH",
                asin=ranking or requested, source="detail",
                message="详情页明确不是榜单请求的商品。",
                evidence={"resolver": result, "record_status": status}))
    if not detail_rows:
        issues.append(issue(
            "detail_identity", QualityStatus.REVIEW, "P2", "DETAIL_EVIDENCE_MISSING",
            source="detail", message="没有可审查的详情记录。"))
    return check_result(
        "detail_identity", issues,
        summary={"records_checked": checked, "records_total": len(detail_rows)},
    )
