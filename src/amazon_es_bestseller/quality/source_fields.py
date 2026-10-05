"""Evidence-backed source field audit before Spanish Master promotion."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from ..models import is_valid_asin, normalize_asin
from .models import QualityStatus, check_result, issue


_JUNK = re.compile(r"(?:<[^>]+>|\b(?:javascript|cookie|captcha|robot check)\b)", re.I)


def _number(value: object) -> Decimal | None:
    text = str(value or "").strip().replace("€", "")
    # Spanish prices use a comma decimal separator and may use dots for
    # thousands. Ratings commonly arrive with a decimal point; never turn 4.2
    # into 42 while normalizing either representation.
    text = text.replace(".", "").replace(",", ".") if "," in text else text
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _valid_amazon_url(url: object, asin: str) -> bool:
    parsed = urlparse(str(url or ""))
    return (parsed.scheme == "https" and parsed.hostname in {"amazon.es", "www.amazon.es"}
            and (not asin or asin.casefold() in parsed.path.casefold()))


def audit_source_fields(products: Iterable[Mapping]) -> object:
    """Check identity, rank, URL, price and source-text invariants.

    The audit intentionally reports missing public evidence as reviewable rather
    than inventing it.  Identity, rank/BSR mixing, URL mismatch and invalid
    prices block promotion to Spanish Master.
    """
    issues = []
    rows = [dict(row) for row in products if isinstance(row, Mapping)]
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if not is_valid_asin(asin):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P0", "IDENTITY_CONFLICT", asin=asin,
                                message="invalid canonical ASIN"))
            continue
        for name in ("requested_asin", "ranking_asin", "detail_asin", "final_asin", "variation_asin"):
            value = normalize_asin(row.get(name))
            if value and value != asin:
                issues.append(issue("source_fields", QualityStatus.BLOCK, "P0", "IDENTITY_CONFLICT", asin=asin,
                                    message=f"{name} differs from canonical ASIN", evidence={name: value}))
        if row.get("bestseller_rank") in (None, "") and row.get("ranking_contexts"):
            issues.append(issue("source_fields", QualityStatus.REVIEW, "P1", "RANK_SLOT_MISSING", asin=asin,
                                message="ranking context has no bestseller rank"))
        if row.get("detail_bsr") not in (None, "") and row.get("bestseller_rank") == row.get("detail_bsr"):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "RANK_BSR_MIXED", asin=asin,
                                message="detail BSR must not populate bestseller rank"))
        url = row.get("product_url") or row.get("final_url")
        if url and not _valid_amazon_url(url, asin):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P0", "SOURCE_FIELD_INVALID", asin=asin,
                                message="product URL is not an https Amazon.es ASIN URL", evidence={"url": url}))
        current = _number(row.get("current_price"))
        original = _number(row.get("original_price"))
        if row.get("current_price") not in (None, "") and (current is None or current <= 0):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "SOURCE_FIELD_INVALID", asin=asin,
                                message="current price is invalid"))
        if original is not None and current is not None and original <= current:
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "SOURCE_FIELD_INVALID", asin=asin,
                                message="original price must exceed current price"))
        rating = _number(row.get("rating"))
        if row.get("rating") not in (None, "") and (rating is None or not (Decimal("0") <= rating <= Decimal("5"))):
            issues.append(issue("source_fields", QualityStatus.BLOCK, "P1", "SOURCE_FIELD_INVALID", asin=asin,
                                message="rating must be in range 0..5"))
        for field in ("title_es_raw", "specification", "product_details_es", "feature_bullets_es"):
            value = str(row.get(field) or "")
            if value and _JUNK.search(value):
                issues.append(issue("source_fields", QualityStatus.REVIEW, "P1", "MISPLACED", asin=asin,
                                    message=f"{field} contains UI/HTML junk", evidence={"field": field}))
    result = check_result("source_fields", issues, summary={"record_count": len(rows)})
    by_asin: dict[str, list] = defaultdict(list)
    for row in result.issues:
        by_asin[row.asin].append(row)
    result_dict = result.to_dict()
    result_dict["sku_status"] = {
        asin: ("SOURCE_BLOCKED" if any(item.status == "BLOCK" for item in values)
               else "SOURCE_REVIEW_REQUIRED" if values else "SOURCE_READY")
        for asin, values in by_asin.items()
    }
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if asin and asin not in result_dict["sku_status"]:
            result_dict["sku_status"][asin] = "SOURCE_READY"
    return result_dict


def source_gate(audit: Mapping) -> tuple[bool, str]:
    """A Spanish Master can be built only from source-ready audit results."""
    statuses = set((audit.get("sku_status") or {}).values())
    if "SOURCE_BLOCKED" in statuses:
        return False, "SOURCE_BLOCKED"
    if "SOURCE_REVIEW_REQUIRED" in statuses:
        return False, "SOURCE_REVIEW_REQUIRED"
    return True, "SOURCE_READY"


__all__ = ["audit_source_fields", "source_gate"]


# Production V1 replacement.  It intentionally lives below the initial small
# adapter so old import paths remain stable while the richer contract can be
# integrated independently by the orchestration task.
import json as _json
from collections import Counter as _Counter
from datetime import date as _date

from ..identity import asin_from_url as _asin_from_url, resolve_identity as _resolve_identity
from ..normalization.dates import parse_es_date as _parse_es_date

PASS = "PASS"
WARN = "WARN"
SOURCE_MISSING = "SOURCE_MISSING"
PARSER_MISSED = "PARSER_MISSED"
MAPPING_MISSED = "MAPPING_MISSED"
DERIVED_MISSING = "DERIVED_MISSING"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"
SOURCE_READY = "SOURCE_READY"

_TEXT_JUNK = re.compile(r"(?:<[^>]+>|\b(?:javascript|cookie|captcha|robot\s*check|add to cart|selecciona|privacy|css)\b|[{}]\s*[\"'][\w-]+[\"']\s*:)", re.I)
_BAD_TEXT = re.compile(r"(?:�{2,}|Ã[\x80-\xBF]|Â[\x80-\xBF]|[\x00-\x08\x0b\x0c\x0e-\x1f])")
_REPEATED_TEXT = re.compile(r"(.{8,}?)(?:\s*\1){2,}", re.S)
_UNIT = re.compile(r"(?<![a-z0-9])\d+(?:[.,]\d+)?\s*(ml|kg|mm|cm|pcs|pack|uds|unidades|piezas|[lgwmv])(?![a-z])", re.I)
_SPEC_TYPES = {
    "capacidad": {"ml", "l"}, "volumen": {"ml", "l"},
    "dimensiones": {"mm", "cm", "m"}, "dimension": {"mm", "cm", "m"},
    "peso": {"g", "kg"}, "weight": {"g", "kg"},
    "potencia": {"w"}, "power": {"w"}, "voltaje": {"v"}, "tension": {"v"},
    "cantidad": {"pcs", "pack", "uds", "unidades", "piezas"},
    "unidades": {"pcs", "pack", "uds", "unidades", "piezas"},
    "piezas": {"pcs", "pack", "uds", "unidades", "piezas"},
}


def _sf_number(value):
    text = str(value or "").strip().replace("EUR", "").replace("€", "").strip()
    text = text.replace(".", "").replace(",", ".") if "," in text else text
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _sf_empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _sf_source_state(row, field):
    evidence = row.get("source_fields") or row.get("source_evidence") or {}
    if isinstance(evidence, Mapping) and field in evidence:
        value = evidence[field]
        if isinstance(value, Mapping):
            value = value.get("present", value.get("exposed"))
        return bool(value)
    exposed = row.get("source_exposed_fields")
    if isinstance(exposed, (list, tuple, set)):
        return field in {str(x) for x in exposed}
    return None


def _sf_field(fields, asin, field, classification, severity, message, evidence=None):
    fields.append({"asin": asin, "field": field, "classification": classification,
                   "severity": severity, "message": message, "evidence": dict(evidence or {})})


def _sf_issue(issues, fields, asin, code, classification, severity, message, field="", evidence=None):
    status = "REVIEW" if classification == REVIEW_REQUIRED else ("BLOCK" if severity in {"P0", "P1"} else "WARN")
    issues.append({"asin": asin, "stage": "source_fields", "check": "source_fields", "severity": severity,
                   "status": status, "issue_code": code, "field_classification": classification,
                   "message": message, "source_file": "", "evidence": dict(evidence or {})})
    _sf_field(fields, asin, field or code, classification, severity, message, evidence)


def _sf_url(value, asin):
    parsed = urlparse(str(value or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {"amazon.es", "www.amazon.es"}:
        return False, "URL must be https Amazon.es"
    target = _asin_from_url(value)
    if not target:
        return False, "URL does not contain a canonical ASIN path"
    if target != asin:
        return False, "URL ASIN differs from canonical ASIN"
    return True, ""


def _sf_absence(row, asin, field, issues, fields):
    if not _sf_empty(row.get(field)):
        return
    state = _sf_source_state(row, field)
    if state is True:
        _sf_issue(issues, fields, asin, "PARSER_MISSED", PARSER_MISSED, "P1",
                  "source evidence says the field was visible but raw value is absent", field)
    elif state is False:
        _sf_field(fields, asin, field, SOURCE_MISSING, "P2", "source explicitly does not expose this field")
    else:
        _sf_field(fields, asin, field, REVIEW_REQUIRED, "P2", "blank field has no source-exposure evidence")


def _sf_identity(row, asin, issues, fields):
    if not is_valid_asin(asin):
        _sf_issue(issues, fields, asin, "IDENTITY_CONFLICT", BLOCKED, "P0", "canonical ASIN is invalid", "asin")
        return
    family = row.get("variation_family_asins") or row.get("family_asins") or []
    if isinstance(family, str):
        family = [family]
    family = [normalize_asin(v) for v in family if is_valid_asin(v)]
    for field in ("requested_asin", "ranking_asin", "detail_asin", "canonical_asin", "final_asin", "parent_asin", "variation_asin", "parsed_detail_asin", "page_asin"):
        value = normalize_asin(row.get(field))
        if value and not is_valid_asin(value):
            _sf_issue(issues, fields, asin, "IDENTITY_CONFLICT", BLOCKED, "P0", f"{field} is invalid", field, {field: value})
        # A parent ASIN is family-level evidence, not a claim that the child
        # details are identical.  Other alternate identities need an explicit
        # family list before they may describe this canonical child.
        if value and value != asin and field != "parent_asin" and is_valid_asin(value) and not ({asin, value} <= set(family)):
            _sf_issue(issues, fields, asin, "IDENTITY_CONFLICT", BLOCKED, "P0",
                      f"{field} differs from canonical ASIN without family evidence", field,
                      {field: value, "variation_family_asins": family})
    resolved = _resolve_identity(
        ranking_asin=row.get("ranking_asin") or asin, requested_asin=row.get("requested_asin") or asin,
        requested_url=row.get("requested_url") or row.get("product_url") or "",
        final_url_asin=row.get("final_url") or row.get("final_asin") or "",
        canonical_asin=row.get("canonical_asin") or asin,
        parsed_detail_asin=row.get("detail_asin") or row.get("parsed_detail_asin") or asin,
        parent_asin=row.get("parent_asin") or row.get("parent_asin_raw") or "", variation_family_asins=family)
    final_asin = normalize_asin(row.get("final_asin")) or _asin_from_url(row.get("final_url"))
    if final_asin and final_asin != asin and not ({asin, final_asin} <= set(family)):
        _sf_issue(issues, fields, asin, "IDENTITY_CONFLICT", BLOCKED, "P0",
                  "redirected final ASIN has no explicit variation-family explanation", "final_asin",
                  {"final_asin": final_asin, "variation_family_asins": family})
    if resolved["identity_status"] == "IDENTITY_MISMATCH":
        _sf_issue(issues, fields, asin, "IDENTITY_CONFLICT", BLOCKED, "P0",
                  "requested/ranking/detail/final identity has no explicit variation explanation", "asin", {"resolver": resolved})
    elif resolved["identity_status"] == "IDENTITY_UNCONFIRMED":
        _sf_issue(issues, fields, asin, "IDENTITY_UNCONFIRMED", REVIEW_REQUIRED, "P1",
                  "identity evidence is insufficient", "asin", {"resolver": resolved})
    else:
        _sf_field(fields, asin, "asin", PASS, "INFO", "identity is evidence-backed", {"resolver": resolved})


def _sf_values(row, asin, issues, fields):
    current, original = _sf_number(row.get("current_price")), _sf_number(row.get("original_price"))
    unit_price = _sf_number(row.get("unit_price"))
    if not _sf_empty(row.get("current_price")) and (current is None or current <= 0):
        _sf_issue(issues, fields, asin, "SOURCE_FIELD_INVALID", BLOCKED, "P1", "current price must be a positive EUR decimal", "current_price")
    if not _sf_empty(row.get("original_price")) and (original is None or original <= 0):
        _sf_issue(issues, fields, asin, "SOURCE_FIELD_INVALID", BLOCKED, "P1", "original price must be a positive EUR decimal", "original_price")
    if current is not None and original is not None and original <= current:
        _sf_issue(issues, fields, asin, "SOURCE_FIELD_INVALID", BLOCKED, "P1", "original price must exceed current price", "original_price")
    # Unit price is auxiliary display evidence.  It must never become the
    # canonical current price merely because a whole-product price is absent.
    if not _sf_empty(row.get("unit_price")) and (unit_price is None or unit_price <= 0):
        _sf_issue(issues, fields, asin, "UNIT_PRICE_INVALID", WARN, "P2", "unit price is not a positive decimal", "unit_price")
    if row.get("discount_rate") not in (None, "") and not (current and original and original > current):
        _sf_issue(issues, fields, asin, "DISCOUNT_DERIVED_WITHOUT_EVIDENCE", DERIVED_MISSING, "P1",
                  "discount lacks a valid current/original source chain", "discount_rate")
    for field, lower, upper, code in (("rating", Decimal("0"), Decimal("5"), "RATING_INVALID"),
                                      ("review_count", Decimal("0"), None, "REVIEW_COUNT_INVALID")):
        value = _sf_number(row.get(field))
        if not _sf_empty(row.get(field)) and (value is None or value < lower or (upper is not None and value > upper)):
            _sf_issue(issues, fields, asin, code, BLOCKED, "P1", f"{field} is invalid", field)
    locale = str(row.get("locale") or row.get("marketplace") or "").strip()
    if locale and locale.casefold().replace("_", "-") not in {"es", "es-es"}:
        _sf_issue(issues, fields, asin, "LOCALE_INVALID", REVIEW_REQUIRED, "P2", "locale is not es-ES", "locale", {"locale": locale})
    raw_date, normalized = row.get("date_first_available_raw"), row.get("date_first_available")
    if raw_date and not _parse_es_date(raw_date):
        _sf_issue(issues, fields, asin, "DATE_INVALID", BLOCKED, "P1", "Spanish date is invalid", "date_first_available_raw")
    if normalized:
        try:
            _date.fromisoformat(str(normalized))
        except ValueError:
            _sf_issue(issues, fields, asin, "DATE_INVALID", BLOCKED, "P1", "normalized date is not ISO", "date_first_available")


def _sf_semantics(row, asin, issues, fields):
    for field in ("title_es_raw", "brand", "specification", "specification_es", "product_details_es", "feature_bullets_es"):
        _sf_absence(row, asin, field, issues, fields)
        text = str(row.get(field) or "")
        if text and (_TEXT_JUNK.search(text) or _BAD_TEXT.search(text) or _REPEATED_TEXT.search(text)):
            _sf_issue(issues, fields, asin, "MISPLACED", REVIEW_REQUIRED, "P1",
                      "HTML/JS/CSS/UI/control/mojibake/repeated text is not product evidence", field)
    title, brand, spec = (str(row.get(k) or "").strip() for k in ("title_es_raw", "brand", "specification"))
    if "http://" in title or "https://" in title:
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "title contains URL", "title_es_raw")
    if brand and (brand == spec or re.search(r"\b(?:ml|kg|cm|mm|\d+[,.]?\d*\s*€)\b", brand, re.I)):
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "brand contains spec or price", "brand")
    if title and spec and title.casefold() == spec.casefold():
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "spec duplicates title", "specification")
    for field in ("category_l1", "category_l2", "category_l3", "leaf_category"):
        _sf_absence(row, asin, field, issues, fields)
    values = [str(row.get(field) or "").strip() for field in ("category_l1", "category_l2", "category_l3", "leaf_category")]
    if any(values) and not row.get("category_provenance"):
        _sf_issue(issues, fields, asin, "CATEGORY_PROVENANCE_MISSING", REVIEW_REQUIRED, "P2", "category lacks Amazon provenance", "category_l1")
    if any(left and left == right for left, right in zip(values, values[1:])):
        _sf_issue(issues, fields, asin, "CATEGORY_COPIED", MAPPING_MISSED, "P1", "category levels must not be copied to fill blanks", "category_l2")
    if any(re.search(r"(?:€|\bEUR\b|\d+[,.]\d{2})", value, re.I) for value in values):
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "category contains price text", "category_l1")
    details = str(row.get("product_details_es") or "")
    if details and re.search(r"\b(?:best sellers rank|m[aá]s vendidos|ranking source url)\b", details, re.I):
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "details contain ranking/UI text", "product_details_es")
    for text in (spec, str(row.get("selected_variation_raw") or "")):
        low = text.casefold()
        for label, allowed in _SPEC_TYPES.items():
            if label in low:
                # Evaluate the value belonging to this label, not every unit
                # in a multi-part compact specification (capacity and weight
                # commonly appear in the same string).
                labelled = re.findall(r"%s\s*[:：]?\s*([^/;\n]+)" % re.escape(label), low)
                unit_text = " ".join(labelled) if labelled else low
                units = {match.group(1).casefold() for match in _UNIT.finditer(unit_text)}
                if units and not units <= allowed:
                    _sf_issue(issues, fields, asin, "SPEC_UNIT_TYPE_MISMATCH", MAPPING_MISSED, "P1",
                              f"{label} has the wrong unit type", "specification", {"units": sorted(units), "allowed": sorted(allowed)})
        if text.lstrip().startswith(("{", "[")):
            try:
                _json.loads(text)
            except ValueError:
                _sf_issue(issues, fields, asin, "MISPLACED", REVIEW_REQUIRED, "P1", "malformed JSON text", "specification")


def _sf_rankings(rows, issues, fields):
    slots, slot_count, pages = defaultdict(set), defaultdict(int), defaultdict(set)
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        contexts = row.get("ranking_contexts") or ([row] if row.get("ranking_source_url") else [])
        if not isinstance(contexts, list):
            contexts = []
        for ctx in contexts:
            if not isinstance(ctx, Mapping):
                _sf_issue(issues, fields, asin, "RANKING_CONTEXT_INVALID", BLOCKED, "P1", "ranking context must be mapping", "ranking_contexts")
                continue
            source = ctx.get("ranking_source_url") or ctx.get("source_url")
            parsed = urlparse(str(source or ""))
            if not source or parsed.scheme != "https" or (parsed.hostname or "").casefold() not in {"amazon.es", "www.amazon.es"}:
                _sf_issue(issues, fields, asin, "RANKING_SOURCE_INVALID", BLOCKED, "P1", "ranking source must be https Amazon.es", "ranking_source_url")
            try:
                page, rank = int(ctx.get("ranking_page_number", ctx.get("page_number"))), int(ctx.get("bestseller_rank", ctx.get("ranking_rank")))
                if page < 1 or rank < 1:
                    raise ValueError
            except (TypeError, ValueError):
                _sf_issue(issues, fields, asin, "RANK_SLOT_MISSING", PARSER_MISSED, "P1", "ranking slot missing", "bestseller_rank")
                continue
            slot = (str(source), page, rank)
            slots[slot].add(asin)
            slot_count[slot] += 1
            pages[(str(source), page)].add(rank)
            if not ctx.get("leaf_category") and not ctx.get("browse_node_id"):
                _sf_issue(issues, fields, asin, "RANK_CATEGORY_EVIDENCE_MISSING", SOURCE_MISSING, "P2", "ranking lacks category/node", "ranking_contexts")
        if row.get("detail_bsr") not in (None, "") and row.get("bestseller_rank") == row.get("detail_bsr"):
            _sf_issue(issues, fields, asin, "RANK_BSR_MIXED", BLOCKED, "P1", "detail BSR cannot populate bestseller rank", "bestseller_rank")
    for (source, page, rank), asins in slots.items():
        if len(asins) > 1 or slot_count[(source, page, rank)] > 1:
            for asin in asins:
                _sf_issue(issues, fields, asin, "DUPLICATE_RANK_SLOT", BLOCKED, "P1", "multiple ASINs occupy rank slot", "bestseller_rank", {"source": source, "page": page, "rank": rank})
    for (source, page), ranks in pages.items():
        if len(ranks) > 2:
            missing = sorted(set(range(min(ranks), max(ranks) + 1)) - ranks)
            if missing:
                _sf_issue(issues, fields, "", "RANK_GAP", REVIEW_REQUIRED, "P2", "rank page has gaps", "bestseller_rank", {"source": source, "page": page, "missing": missing})


def audit_source_fields(products: Iterable[Mapping]) -> dict:
    """Perform the production source audit without network access or mutation."""
    rows = [dict(row) for row in (products or ()) if isinstance(row, Mapping)]
    issues, fields = [], []
    if not rows:
        _sf_issue(issues, fields, "", "EMPTY_SOURCE_INPUT", REVIEW_REQUIRED, "P1",
                  "no product records were supplied for source audit", "asin")
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        _sf_identity(row, asin, issues, fields)
        if not is_valid_asin(asin):
            continue
        for name in ("product_url", "requested_url", "final_url", "canonical_url"):
            if not _sf_empty(row.get(name)):
                valid, message = _sf_url(row[name], asin)
                if not valid and name == "final_url":
                    redirected = _asin_from_url(row[name])
                    family = row.get("variation_family_asins") or row.get("family_asins") or []
                    if isinstance(family, str):
                        family = [family]
                    family = {normalize_asin(value) for value in family}
                    if redirected and {asin, redirected} <= family:
                        valid, message = True, ""
                if not valid:
                    _sf_issue(issues, fields, asin, "URL_INVALID", BLOCKED, "P0", message, name, {name: row[name]})
                else:
                    _sf_field(fields, asin, name, PASS, "INFO", "canonical https Amazon.es ASIN URL")
        _sf_values(row, asin, issues, fields)
        _sf_semantics(row, asin, issues, fields)
        material = (row.get("product_url"), row.get("final_url"), row.get("ranking_contexts"), row.get("title_es_raw"), row.get("current_price"), row.get("rating"))
        if not any(not _sf_empty(value) for value in material):
            _sf_issue(issues, fields, asin, "EMPTY_SOURCE_INPUT", REVIEW_REQUIRED, "P1", "ASIN-only record has no source evidence", "asin")
    _sf_rankings(rows, issues, fields)
    per_asin = defaultdict(list)
    for item in issues:
        if item["asin"]:
            per_asin[item["asin"]].append(item)
    sku_status = {}
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if not asin:
            continue
        relevant = per_asin.get(asin, [])
        if any(item["severity"] in {"P0", "P1"} and item["field_classification"] != REVIEW_REQUIRED for item in relevant):
            sku_status[asin] = BLOCKED
        elif any(item["field_classification"] == REVIEW_REQUIRED and item["severity"] == "P1" for item in relevant):
            sku_status[asin] = REVIEW_REQUIRED
        else:
            sku_status[asin] = SOURCE_READY
    overall = "BLOCK" if BLOCKED in sku_status.values() else "REVIEW" if REVIEW_REQUIRED in sku_status.values() else "PASS"
    return {"check": "source_fields", "status": overall, "summary": {"record_count": len(rows), "issue_count": len(issues),
            "classifications": dict(_Counter(item["classification"] for item in fields))}, "issues": issues,
            "field_audits": fields, "sku_status": sku_status}


def source_gate(audit: Mapping) -> tuple[bool, str]:
    """Legacy tuple adapter; use ``evaluate_source_gate`` for a bound gate record."""
    statuses = set((audit.get("sku_status") or {}).values())
    if BLOCKED in statuses or "SOURCE_BLOCKED" in statuses:
        return False, "SOURCE_BLOCKED"
    if REVIEW_REQUIRED in statuses or "SOURCE_REVIEW_REQUIRED" in statuses:
        return False, "SOURCE_REVIEW_REQUIRED"
    return True, SOURCE_READY


__all__ = ["audit_source_fields", "source_gate", "PASS", "WARN", "SOURCE_MISSING", "PARSER_MISSED", "MAPPING_MISSED", "DERIVED_MISSING", "REVIEW_REQUIRED", "BLOCKED", "SOURCE_READY"]
