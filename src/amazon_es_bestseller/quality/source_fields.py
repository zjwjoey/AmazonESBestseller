"""Evidence-backed source field audit before Spanish Master promotion."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from ..models import is_valid_asin, normalize_asin
from .models import QualityStatus, check_result, issue


_HTML_TAG = r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+(?:[^<>\s]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?))*\s*/?>"
_JUNK = re.compile(rf"(?:{_HTML_TAG}|\b(?:javascript|cookie|captcha|robot check)\b)", re.I)
SOURCE_FIELD_AUDIT_RULES_VERSION = "source-fields-unit-v2"


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
import hashlib as _hashlib
from collections import Counter as _Counter
from datetime import date as _date

from ..identity import asin_from_url as _asin_from_url, resolve_identity as _resolve_identity
from ..normalization.dates import parse_es_date as _parse_es_date
from ..translation.preclean import find_real_html_tag

PASS = "PASS"
WARN = "WARN"
SOURCE_MISSING = "SOURCE_MISSING"
PARSER_MISSED = "PARSER_MISSED"
MAPPING_MISSED = "MAPPING_MISSED"
DERIVED_MISSING = "DERIVED_MISSING"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"
SOURCE_READY = "SOURCE_READY"

_TEXT_JUNK = re.compile(rf"(?:{_HTML_TAG}|\b(?:javascript|cookie|captcha|robot\s*check|add to cart|selecciona|privacy|css)\b|[{{}}]\s*[\"'][\w-]+[\"']\s*:)", re.I)
_BAD_TEXT = re.compile(r"(?:�{2,}|Ã[\x80-\xBF]|Â[\x80-\xBF]|[\x00-\x08\x0b\x0c\x0e-\x1f])")
_REPEATED_TEXT = re.compile(r"(.{8,}?)(?:\s*\1){2,}", re.S)
_STRICT_UI_TEXT = re.compile(
    r"\b(?:javascript|captcha|robot\s*check|add\s+to\s+cart|privacy|"
    r"(?:accept|manage|configure)\s+(?:all\s+)?cookies?|cookies?\s+(?:policy|settings|preferences))\b", re.I)
_EXPLICIT_BAD_TEXT = re.compile(r"\ufffd|\?{2,}|[\x00-\x08\x0b\x0c\x0e-\x1f]")
_UNIT = re.compile(
    r"(?<![a-z0-9])\d+(?:[.,]\d+)?\s*(?P<unit>m(?:3|³)\s*/\s*h|cm(?:3|³)|cc|mililitros?|litros?|gramos?|"
    r"kilogramos?|cent[ií]metros?|metros?|vatios?|voltios?|ml|kg|mm|cm|pcs|pack|uds|"
    r"unidades|piezas|[lgwmv])(?![a-z])", re.I)
_LABEL_UNIT_TYPES = (
    # Specific labels must win before generic ``capacidad`` / ``peso`` words.
    (r"\b(?:capacidad\s+(?:de\s+)?(?:carga|peso)|peso\s+maxim)\b", {"g", "kg"}),
    (r"\b(?:capacidad|volumen)\b", {"ml", "l"}),
    (r"\b(?:dimension|dimensiones)\b", {"mm", "cm", "m"}),
    (r"\b(?:peso|weight)\b", {"g", "kg"}),
    (r"\b(?:potencia|power)\b", {"w"}),
    (r"\b(?:voltaje|tension)\b", {"v"}),
    (r"\b(?:cantidad|unidades|piezas)\b", {"pcs", "pack", "uds", "unidades", "piezas"}),
)
_UNIT_CANONICAL = {
    "m3/h": "m3/h", "cm3": "ml", "cc": "ml",
    "mililitro": "ml", "mililitros": "ml", "ml": "ml",
    "litro": "l", "litros": "l", "l": "l",
    "gramo": "g", "gramos": "g", "g": "g",
    "kilogramo": "kg", "kilogramos": "kg", "kg": "kg",
    "centimetro": "cm", "centimetros": "cm", "cm": "cm",
    "metro": "m", "metros": "m", "m": "m", "mm": "mm",
    "vatio": "w", "vatios": "w", "w": "w",
    "voltio": "v", "voltios": "v", "v": "v",
}
_BINDING_EXCLUDED = {
    "notes", "remark", "remarks", "备注", "title_zh", "specification_zh",
    "product_details_zh", "feature_bullets_zh", "audit_status", "auditstatus",
    "source_hash", "sourcehash", "artifact_hash", "raw_source", "observed_source",
}
_RAW_EVIDENCE_FIELDS = {
    "title_es_raw", "brand_raw", "current_price_raw", "current_price", "original_price_raw",
    "original_price", "rating_raw", "rating", "review_count_raw", "review_count",
    "selected_variation_raw", "product_details_es", "details_json", "attributes",
    "feature_bullets_es", "feature_bullets_raw", "product_description_raw",
    "date_first_available_raw", "detail_bsr_raw",
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
    issues.append({"asin": asin, "field": field, "stage": "source_fields", "check": "source_fields", "severity": severity,
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
        _sf_issue(issues, fields, asin, "LOCALE_INVALID", REVIEW_REQUIRED, "P1", "locale is not es-ES", "locale", {"locale": locale})
    raw_date, normalized = row.get("date_first_available_raw"), row.get("date_first_available")
    if raw_date and not _parse_es_date(raw_date):
        _sf_issue(issues, fields, asin, "DATE_INVALID", BLOCKED, "P1", "Spanish date is invalid", "date_first_available_raw")
    if normalized:
        try:
            _date.fromisoformat(str(normalized))
        except ValueError:
            _sf_issue(issues, fields, asin, "DATE_INVALID", BLOCKED, "P1", "normalized date is not ISO", "date_first_available")


def _semantic_text(value) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()


def _compound_units(value) -> set[str]:
    text = _semantic_text(value)
    units = set()
    if re.search(r"\bmetros?\s+cubicos?\s+por\s+horas?\b", text):
        units.add("m3/h")
    if re.search(r"\bmetros?\s+cubicos?\s+por\s+minutos?\b", text):
        units.add("m3/min")
    if re.search(r"\blitros?\s+por\s+minutos?\b", text):
        units.add("l/min")
    if re.search(r"\blitros?\s+por\s+horas?\b", text):
        units.add("l/h")
    if re.search(r"\blitros?\s+por\s+segundos?\b", text):
        units.add("l/s")
    if re.search(r"\blitros?\s+por\s+d(?:i)?as?\b", text):
        units.add("l/day")
    if re.search(r"\bcentimetros?\s+cubicos?\s+por\s+segundos?\b", text):
        units.add("cm3/s")
    if re.search(r"\b\d+(?:[.,]\d+)?\s*centimetros?\s+cubicos?\b(?!\s+por\s+segundos?\b)", text):
        units.add("ml")
    raw = str(value or "")
    if re.search(r"\b\d+(?:[.,]\d+)?\s*cm(?:3|\u00b3)(?!\s*/|[a-z0-9])", raw, re.I):
        units.add("ml")
    for unit, canonical in ((r"cm(?:3|\u00b3)\s*/\s*s", "cm3/s"),
                            (r"l\s*/\s*s", "l/s"),
                            (r"l\s*/\s*h", "l/h"),
                            (r"l\s*/\s*(?:day|d[ií]a)", "l/day")):
        if re.search(rf"\b\d+(?:[.,]\d+)?\s*{unit}(?![a-z0-9])", raw, re.I):
            units.add(canonical)
    return units


def _without_compound_units(value) -> str:
    text = _semantic_text(value)
    text = re.sub(r"\bmetros?\s+cubicos?\s+por\s+(?:horas?|minutos?)\b", "", text)
    text = re.sub(r"\blitros?\s+por\s+(?:minutos?|horas?|segundos?|d(?:i)?as?)\b", "", text)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*centimetros?\s+cubicos?\s+por\s+segundos?\b", "", text)
    text = re.sub(r"\b\d+(?:[.,]\d+)?\s*centimetros?\s+cubicos?\b", "", text)
    return re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:cm3(?:\s*/\s*s)?|l\s*/\s*(?:s|h|day|dia))", "", text)


def _structured_attribute_pairs(row) -> list[tuple[str, str, str]]:
    pairs = []
    for attr in row.get("attributes") or []:
        if not isinstance(attr, Mapping):
            continue
        label = attr.get("label_raw") or attr.get("label")
        value = attr.get("value_raw") or attr.get("value")
        if label not in (None, "") and value not in (None, ""):
            pairs.append((str(label), str(value), "attributes"))
    details = row.get("details_json")
    if isinstance(details, Mapping):
        for label, value in details.items():
            if label not in (None, "") and value not in (None, ""):
                pairs.append((str(label), str(value), "details_json"))
    return pairs


def _labelled_text_pairs(text, field) -> list[tuple[str, str, str]]:
    pairs = []
    for segment in re.split(r"[/;\n]", str(text or "")):
        if ":" not in segment and "：" not in segment:
            continue
        label, value = re.split(r"[:：]", segment, maxsplit=1)
        if label.strip() and value.strip():
            pairs.append((label.strip(), value.strip(), field))
    return pairs


def _allowed_units_for_label(label) -> set[str]:
    normalized = _semantic_text(label)
    for pattern, allowed in _LABEL_UNIT_TYPES:
        if re.search(pattern, normalized):
            return allowed
    return set()


def _unit_evidence(row, label, value, field, *, kind, corroborated_by="", corroborated_signature="") -> dict:
    source = {"label": str(label), "value": str(value), "field": field}
    if corroborated_by:
        source["corroborated_by"] = corroborated_by
    if corroborated_signature:
        source["corroborated_signature"] = corroborated_signature
    record_binding = _sf_record_binding(row)
    return {
        "match_kind": kind,
        "label": str(label),
        "value": str(value),
        "evidence_locator": {"source": field, "label_raw": str(label), "value_raw": str(value)},
        "source_hash": hashlib.sha256(_json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "attribute_label_value_hash": _sf_hash({"label_raw": str(label), "value_raw": str(value), "field": field}),
        "same_asin_record_binding": record_binding["record_hash"],
        **({"corroborated_by": corroborated_by,
            "corroborated_source_field": corroborated_by,
            "corroborated_source_hash": _sf_hash({corroborated_by: row.get(corroborated_by)}),
            "corroborated_signature": corroborated_signature} if corroborated_by else {}),
    }


def _measure_signatures(value) -> set[str]:
    signatures = set()
    text = _without_compound_units(value)
    for match in _UNIT.finditer(text):
        unit = _UNIT_CANONICAL.get(_semantic_text(match.group("unit")), _semantic_text(match.group("unit")))
        number = match.group(0)[:match.group(0).lower().rfind(match.group("unit").lower())].strip().replace(",", ".")
        if number:
            signatures.add(f"{number}{unit}")
    for unit in _compound_units(value):
        signatures.add(unit)
    return signatures


def _corroborated_measure(row, value) -> tuple[str, str]:
    signatures = _measure_signatures(value)
    for field in ("title_es_raw", "selected_variation_raw"):
        matches = signatures & _measure_signatures(row.get(field))
        if matches:
            return field, sorted(matches)[0]
    return "", ""


def _unit_policy(row, label, value, field) -> tuple[set[str], str | None, dict | None, str | None]:
    """Return a conservative label policy without turning ambiguity into PASS."""
    normalized = _semantic_text(label)
    units = _units_for_labeled_value(label, value)
    evidence = lambda kind, corroborated_by="", corroborated_signature="": _unit_evidence(
        row, label, value, field, kind=kind, corroborated_by=corroborated_by,
        corroborated_signature=corroborated_signature)
    if re.search(r"\bcapacidad\s+de\s+la\s+bateria\b", normalized) and units & {"v"}:
        return set(), "battery capacity label conflicts with voltage value", evidence("label_value_conflict"), None
    if re.search(r"\bcapacidad\s+de\s+peso\b", normalized) and units & {"ml", "l"}:
        return set(), "weight-capacity label conflicts with volume value", evidence("label_value_conflict"), None
    if re.search(r"\bvolumen\s+liquido\b", normalized) and units & {"g", "kg"}:
        return set(), "liquid-volume label conflicts with weight value", evidence("label_value_conflict"), None
    if re.search(r"\bvoltaje(?:\s+maximo)?\b", normalized) and units & {"w"}:
        return set(), "voltage label conflicts with power value", evidence("label_value_conflict"), None
    if re.search(r"\b(?:caudal|flujo)\s+de\s+aire\b|\bairflow\b", normalized):
        if "l/day" in units:
            return set(), "dehumidifier rate must not be classified as airflow", evidence("rate_semantics_unresolved"), None
        return {"m3/h", "m3/min", "l/min", "cm3/s", "l/s", "l/h"}, None, evidence("airflow"), None
    if re.search(r"\bcapacidad\s+de\s+perfor", normalized):
        return {"mm", "cm", "m"}, None, evidence("drill_capacity"), None
    if re.search(r"\btension\b", normalized):
        if units & {"v"}:
            return {"v"}, None, evidence("voltage"), None
        if units & {"g", "kg"}:
            if re.search(r"\b(?:mano|grip|hand\s*gripper|ejercitador)\b", _semantic_text(row.get("title_es_raw"))):
                return {"g", "kg"}, None, evidence("grip_resistance"), None
            return set(), "tension weight requires hand-gripper domain evidence", evidence("tension_weight_unresolved"), None
    if re.search(r"\bcantidad\s+de\s+pilas\b", normalized) and units & {"v"}:
        return {"v"}, None, evidence("battery_count_and_voltage"), None
    if re.search(r"\btamano\b", normalized):
        dimensional = units & {"mm", "cm", "m"}
        # ``Tamaño`` is an Amazon option label, not proof that a number is a
        # physical dimension.  Only an actual dimension unit lets us constrain
        # it; ml/L/pieces alone remain reviewable rather than a false BLOCK.
        if dimensional and not (units - dimensional):
            return {"mm", "cm", "m"}, None, evidence("explicit_dimension"), None
        if units:
            corroborated_by, signature = _corroborated_measure(row, value)
            if corroborated_by:
                return (set(units), None,
                        evidence("generic_measure_correlated", corroborated_by, signature),
                        "generic label measure corroborated by product evidence")
            return (set(), "generic Tamaño value has no unambiguous dimension semantics",
                    evidence("generic_measure_unresolved"), None)
        return set(), None, None, None
    if re.search(r"\b(?:numero|cantidad)\s+de\s+unidades\b", normalized):
        count_units = {"pcs", "pack", "uds", "unidades", "piezas"}
        if units - count_units:
            corroborated_by, signature = _corroborated_measure(row, value)
            if corroborated_by:
                return (set(units), None,
                        evidence("unit_count_measure_correlated", corroborated_by, signature),
                        "unit-count label measure corroborated by product evidence")
            return (set(), "unit-count label contains a packaging amount rather than an unambiguous count",
                    evidence("unit_count_unresolved"), None)
    generic_capacity = bool(re.search(r"\bcapacidad(?:\s+de\s+salida)?\b", normalized))
    specific_capacity = bool(re.search(r"\bcapacidad\s+de\s+(?:carga|peso|la\s+bateria|perfor)", normalized))
    if generic_capacity and not specific_capacity and units - {"ml", "l"}:
        return set(), "generic capacity measure lacks domain evidence", evidence("generic_capacity_unresolved"), None
    return _allowed_units_for_label(label), None, None, None


def _units_for_labeled_value(label, value) -> set[str]:
    units = _compound_units(value)
    dimension_label = bool(re.search(r"\b(?:dimension|dimensiones|tamano)\b", _semantic_text(label)))
    text = _without_compound_units(value)
    for match in _UNIT.finditer(text):
        raw_unit = _semantic_text(match.group("unit"))
        # Spanish Amazon dimensions use ``l.`` / ``an.`` for largo/ancho.
        # ``14,6l. x 6,7an. centímetros`` is not a 14.6 L capacity.
        if raw_unit == "l" and dimension_label and text[match.end():].lstrip().startswith("."):
            continue
        units.add(_UNIT_CANONICAL.get(raw_unit, raw_unit))
    return units


def _units_for_unit_audit(label, value) -> set[str]:
    """Ignore an attached weight segment in an otherwise dimensional value."""
    units = _units_for_labeled_value(label, value)
    if not re.search(r"\b(?:dimension|dimensiones)\b", _semantic_text(label)):
        return units
    segments = [segment for segment in re.split(r"[;|]", str(value or "")) if segment.strip()]
    segment_units = [_units_for_labeled_value(label, segment) for segment in segments]
    has_dimensions = any(part & {"mm", "cm", "m"} for part in segment_units)
    if has_dimensions:
        for part in segment_units:
            if part <= {"g", "kg"}:
                units -= part
    return units


def _category_leaf_supported(row, provenance, values) -> bool:
    """Require provenance to bind to an actual context on this exact record."""
    if not isinstance(provenance, Mapping) or str(provenance.get("source") or "") != "ranking_context":
        return False
    l1, l2, l3, leaf = values
    if not l3 or l3 != leaf or provenance.get("leaf_category") != leaf:
        return False
    levels = provenance.get("levels")
    if not isinstance(levels, Mapping):
        return False
    if levels.get("category_l1") != l1 or levels.get("category_l2") != l2 or levels.get("category_l3") != l3:
        return False
    path = str(provenance.get("ranking_source_category_path") or "")
    parts = [part.strip() for part in path.split(">") if part.strip()]
    if len(parts) < 3 or parts[-1] != leaf:
        return False
    contexts = row.get("ranking_contexts") or []
    if not isinstance(contexts, list):
        return False
    expected_hash = str(provenance.get("ranking_context_hash") or "")
    expected_url = str(provenance.get("ranking_source_url") or "")
    expected_page = provenance.get("ranking_page_number")
    if not expected_hash or not expected_url or expected_page in (None, ""):
        return False
    for context in contexts:
        if not isinstance(context, Mapping) or _sf_hash(dict(context)) != expected_hash:
            continue
        if (str(context.get("ranking_source_url") or context.get("source_url") or "") != expected_url
                or context.get("ranking_page_number", context.get("page_number")) != expected_page
                or str(context.get("ranking_source_category_path") or "") != path):
            continue
        if (context.get("category_l1") == l1 and context.get("category_l2") == l2
                and context.get("category_l3") == l3 and context.get("leaf_category") == leaf):
            return True
    return False


def _text_locator(text: str, match: re.Match[str], kind: str) -> dict:
    snippet = text[max(0, match.start() - 24):match.end() + 24]
    return {
        "match_kind": kind,
        "offset": match.start(),
        "snippet_hash": hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16],
    }


def _has_visible_attribute_duplicate(row) -> bool:
    seen = set()
    for label, value, field in _structured_attribute_pairs(row):
        key = (_semantic_text(label), _semantic_text(value))
        if key in seen:
            return True
        seen.add(key)
    return False


def _explicit_same_asin_brand_evidence(row, brand: str) -> dict | None:
    """Require an explicit Marca/Brand attribute before accepting numeric brands."""
    expected = _semantic_text(brand)
    if not expected:
        return None
    for label, value, field in _structured_attribute_pairs(row):
        if _semantic_text(label) in {"marca", "brand"} and _semantic_text(value) == expected:
            return {
                "source": "attributes:Marca/Brand",
                "label_raw": label,
                "value_raw": value,
                "same_asin_record_binding": _sf_record_binding(row)["record_hash"],
            }
    return None


def _text_semantic_finding(row, field: str, text: str):
    """Flag only strict UI/corruption; ordinary product repetition is reviewable."""
    decoded_text, html_tag = find_real_html_tag(text)
    if html_tag:
        return ("MISPLACED", REVIEW_REQUIRED, "P1",
                "strict HTML/UI/control or damaged text is not product evidence",
                _text_locator(decoded_text, html_tag, "html_tag"))
    for pattern, kind in ((_STRICT_UI_TEXT, "explicit_ui"), (_EXPLICIT_BAD_TEXT, "mojibake")):
        match = pattern.search(text)
        if match:
            return ("MISPLACED", REVIEW_REQUIRED, "P1",
                    "strict HTML/UI/control or damaged text is not product evidence", _text_locator(text, match, kind))
    match = _REPEATED_TEXT.search(text)
    if not match:
        return None
    if field == "product_details_es" and _has_visible_attribute_duplicate(row):
        return None
    return ("TEXT_REPETITION_REVIEW", REVIEW_REQUIRED, "P2",
            "repeated product text requires review but is not treated as UI text", _text_locator(text, match, "repeated_text"))


def _sf_semantics(row, asin, issues, fields):
    for field in ("title_es_raw", "brand", "specification", "specification_es", "product_details_es", "feature_bullets_es"):
        _sf_absence(row, asin, field, issues, fields)
        text = str(row.get(field) or "")
        if text:
            finding = _text_semantic_finding(row, field, text)
            if finding:
                code, classification, severity, message, evidence = finding
                _sf_issue(issues, fields, asin, code, classification, severity, message, field, evidence)
    title, brand, spec = (str(row.get(k) or "").strip() for k in ("title_es_raw", "brand", "specification"))
    if "http://" in title or "https://" in title:
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "title contains URL", "title_es_raw")
    if brand and (brand == spec or re.search(r"\b\d+(?:[,.]\d+)?\s*(?:ml|l|g|kg|cm|mm|m|w|v|€|eur)\b", brand, re.I)):
        explicit_brand = _explicit_same_asin_brand_evidence(row, brand)
        if explicit_brand:
            _sf_field(fields, asin, "brand", PASS, "INFO",
                      "numeric brand is backed by explicit same-ASIN Marca/Brand evidence", explicit_brand)
        else:
            _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "brand contains spec or price", "brand")
    if title and spec and title.casefold() == spec.casefold():
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "spec duplicates title", "specification")
    for field in ("category_l1", "category_l2", "category_l3", "leaf_category"):
        _sf_absence(row, asin, field, issues, fields)
    values = [str(row.get(field) or "").strip() for field in ("category_l1", "category_l2", "category_l3", "leaf_category")]
    provenance = row.get("category_provenance")
    if any(values) and not provenance:
        _sf_issue(issues, fields, asin, "CATEGORY_PROVENANCE_MISSING", REVIEW_REQUIRED, "P2", "category lacks Amazon provenance", "category_l1")
    l1, l2, l3, leaf = values
    copied_hierarchy = bool((l1 and l1 == l2) or (l2 and l2 == l3))
    # A three-segment Amazon path legitimately has ``leaf == L3``.  Accept it
    # only when the exact ranking category path/provenance says the same;
    # otherwise it remains a likely fill-down error.
    leaf_l3_without_evidence = bool(l3 and l3 == leaf and not _category_leaf_supported(row, provenance, values))
    if copied_hierarchy or leaf_l3_without_evidence:
        _sf_issue(issues, fields, asin, "CATEGORY_COPIED", MAPPING_MISSED, "P1", "category levels must not be copied to fill blanks", "category_l2")
    if any(re.search(r"(?:€|\bEUR\b|\d+[,.]\d{2})", value, re.I) for value in values):
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "category contains price text", "category_l1")
    details = str(row.get("product_details_es") or "")
    if details and re.search(r"\b(?:best sellers rank|m[aá]s vendidos|ranking source url)\b", details, re.I):
        _sf_issue(issues, fields, asin, "FIELD_MISPLACED", MAPPING_MISSED, "P1", "details contain ranking/UI text", "product_details_es")
    labelled_units = _structured_attribute_pairs(row)
    # Structured attributes and compact evidence are independent sources.  A
    # present attribute (for example ``Marca: Acme``) must not make a malformed
    # compact specification invisible.  Keep the pairs separate and audit the
    # union; do not flatten unrelated attribute fields into specification text.
    labelled_units.extend(_labelled_text_pairs(spec, "specification"))
    labelled_units.extend(_labelled_text_pairs(str(row.get("selected_variation_raw") or ""),
                                               "selected_variation_raw"))
    for label, value, field in labelled_units:
        allowed, review_reason, unit_evidence, explanation = _unit_policy(row, label, value, field)
        if review_reason:
            code = ("SOURCE_SEMANTIC_CONFLICT" if unit_evidence and
                    unit_evidence.get("match_kind") == "label_value_conflict"
                    else "UNIT_SEMANTICS_AMBIGUOUS")
            _sf_issue(issues, fields, asin, code, REVIEW_REQUIRED, "P1",
                      review_reason, field, unit_evidence or {"label": label, "value": value})
            continue
        if explanation:
            _sf_field(fields, asin, field, PASS, "INFO", explanation, unit_evidence)
        if not allowed:
            continue
        units = _units_for_unit_audit(label, value)
        if units and not units <= allowed:
            evidence = dict(unit_evidence or {})
            evidence.update({"units": sorted(units), "allowed": sorted(allowed)})
            _sf_issue(issues, fields, asin, "SPEC_UNIT_TYPE_MISMATCH", MAPPING_MISSED, "P1",
                      f"{label} has the wrong unit type", field,
                      evidence)
    for text in (spec, str(row.get("selected_variation_raw") or "")):
        if text.lstrip().startswith(("{", "[{")):
            try:
                _json.loads(text)
            except ValueError:
                _sf_issue(issues, fields, asin, "MISPLACED", REVIEW_REQUIRED, "P1", "malformed JSON text", "specification")


def _ranking_context_rows(row):
    contexts = row.get("ranking_contexts") or ([row] if row.get("ranking_source_url") else [])
    return contexts if isinstance(contexts, list) else []


def _sf_rankings(rows, issues, fields, *, ranking_matrix=None):
    slots, slot_count, pages = defaultdict(set), defaultdict(int), defaultdict(set)
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        contexts = _ranking_context_rows(row)
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
            has_path = bool([part.strip() for part in str(ctx.get("ranking_source_category_path") or "").split(">") if part.strip()])
            if not ctx.get("leaf_category") and not ctx.get("browse_node_id") and not has_path:
                _sf_issue(issues, fields, asin, "RANK_CATEGORY_EVIDENCE_MISSING", SOURCE_MISSING, "P2", "ranking lacks category/node", "ranking_contexts")
        if row.get("detail_bsr") not in (None, "") and row.get("bestseller_rank") == row.get("detail_bsr"):
            _sf_issue(issues, fields, asin, "RANK_BSR_MIXED", BLOCKED, "P1", "detail BSR cannot populate bestseller rank", "bestseller_rank")
    for (source, page, rank), asins in slots.items():
        if len(asins) > 1 or slot_count[(source, page, rank)] > 1:
            for asin in asins:
                _sf_issue(issues, fields, asin, "DUPLICATE_RANK_SLOT", BLOCKED, "P1", "multiple ASINs occupy rank slot", "bestseller_rank", {"source": source, "page": page, "rank": rank})
    # Gap authority must be the complete reviewed ranking matrix.  The current
    # owner scope is a subset, so treating its absent ASINs as missing ranks
    # would manufacture a gap.  Per-record context checks above remain bound
    # to the current records.
    matrix_pages = defaultdict(set)
    for row in (ranking_matrix if ranking_matrix is not None else rows):
        if not isinstance(row, Mapping):
            continue
        for ctx in _ranking_context_rows(row):
            if not isinstance(ctx, Mapping):
                continue
            try:
                page = int(ctx.get("ranking_page_number", ctx.get("page_number")))
                rank = int(ctx.get("bestseller_rank", ctx.get("ranking_rank")))
            except (TypeError, ValueError):
                continue
            source = str(ctx.get("ranking_source_url") or ctx.get("source_url") or "")
            if source and page >= 1 and rank >= 1:
                matrix_pages[(source, page)].add(rank)
    for (source, page), ranks in matrix_pages.items():
        if len(ranks) > 2:
            missing = sorted(set(range(min(ranks), max(ranks) + 1)) - ranks)
            if missing:
                _sf_issue(issues, fields, "", "RANK_GAP", REVIEW_REQUIRED, "P2", "rank page has gaps", "bestseller_rank", {"source": source, "page": page, "missing": missing})


def _sf_hash(value) -> str:
    payload = _json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return _hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sf_record_binding(row: Mapping) -> dict:
    """Bind audit conclusions to exact stage data rather than ASIN/status alone."""
    source = {str(key): value for key, value in row.items() if key not in _BINDING_EXCLUDED}
    raw_evidence = {key: value for key, value in source.items()
                    if (key in _RAW_EVIDENCE_FIELDS or key.endswith("_raw")) and not _sf_empty(value)}
    return {
        "record_hash": _sf_hash(source),
        "raw_evidence_hash": _sf_hash(raw_evidence),
        "raw_evidence_present": bool(raw_evidence),
        "detail_schema_version": source.get("detail_schema_version"),
        "ranking_schema_version": source.get("ranking_schema_version", source.get("ranking_parser_version")),
        "parser_version": source.get("parser_version", source.get("detail_parser_version")),
    }


def audit_source_fields(products: Iterable[Mapping], *, ranking_matrix: Iterable[Mapping] | None = None, progress=None) -> dict:
    """Perform the production source audit without network access or mutation."""
    emit = progress or (lambda *_args, **_kwargs: None)
    # The audit does not mutate source rows; keep only one shallow container.
    rows = [row for row in (products or ()) if isinstance(row, Mapping)]
    issues, fields = [], []
    if not rows:
        _sf_issue(issues, fields, "", "EMPTY_SOURCE_INPUT", REVIEW_REQUIRED, "P1",
                  "no product records were supplied for source audit", "asin")
    for index, row in enumerate(rows, start=1):
        if index % 100 == 0:
            emit("SOURCE_AUDIT_PROGRESS", records=index)
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
    matrix_rows = [row for row in (ranking_matrix or ()) if isinstance(row, Mapping)] if ranking_matrix is not None else None
    _sf_rankings(rows, issues, fields, ranking_matrix=matrix_rows)
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
    bindings = defaultdict(list)
    for row in rows:
        asin = normalize_asin(row.get("asin"))
        if is_valid_asin(asin):
            bindings[asin].append(_sf_record_binding(row))
    return {"check": "source_fields", "status": overall, "summary": {"record_count": len(rows), "issue_count": len(issues),
            "classifications": dict(_Counter(item["classification"] for item in fields))}, "issues": issues,
            "field_audits": fields, "sku_status": sku_status, "record_bindings": dict(bindings)}


def source_gate(audit: Mapping) -> tuple[bool, str]:
    """Legacy tuple adapter; use ``evaluate_source_gate`` for a bound gate record."""
    statuses = set((audit.get("sku_status") or {}).values())
    if BLOCKED in statuses or "SOURCE_BLOCKED" in statuses:
        return False, "SOURCE_BLOCKED"
    if REVIEW_REQUIRED in statuses or "SOURCE_REVIEW_REQUIRED" in statuses:
        return False, "SOURCE_REVIEW_REQUIRED"
    return True, SOURCE_READY


__all__ = ["audit_source_fields", "source_gate", "PASS", "WARN", "SOURCE_MISSING", "PARSER_MISSED", "MAPPING_MISSED", "DERIVED_MISSING", "REVIEW_REQUIRED", "BLOCKED", "SOURCE_READY"]
