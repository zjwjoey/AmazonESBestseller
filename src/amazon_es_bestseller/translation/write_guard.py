"""Chinese write checks shared by the exporter and the research patch writer."""
import re
import unicodedata
from collections.abc import Mapping

from ..identity import asin_from_url
from .production_contract import PRODUCTION_FIELD_ALIASES


_ASIN_FIELDS = ("asin", "source_asin", "requested_asin", "resolved_asin")
_VARIANT_FIELDS = (*PRODUCTION_FIELD_ALIASES["selected_variation_raw"], "source_variant_es")
_SNAPSHOT_FIELDS = ("source_html_sha256", "source_sha256")

# Unambiguous substitutions seen in the Spanish-source review and write ledger.
# Qualify the Chinese signature by the Spanish product type. For example a
# generic mention of batteries is legitimate for the cordless leaf blower.
_CROSS_PRODUCT = tuple(
    (re.compile(source, re.I), re.compile(foreign, re.I))
    for source, foreign in (
        (r"videoportero|timbre inalámbrico", r"Vitalizart|(?:天鹅绒|丝绒)丝?带"),
        (r"cintas?\s+(?:de\s+)?terciopelo", r"真空收纳袋"),
        (r"soplador\s+de\s+hojas", r"D\s*[型号]\s*碱性电池"),
        (r"\blinterna\b", r"圣诞老人铝箔气球"),
    )
)


def _text(value) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _values(record: Mapping, fields: tuple[str, ...]) -> set[str]:
    return {value for key in fields if (value := _text(record.get(key)))}


def _asins(record: Mapping) -> set[str]:
    asins = _values(record, _ASIN_FIELDS)
    asins.update(asin.lower() for key in ("product_url", "source_url")
                 if (asin := asin_from_url(record.get(key))))
    return asins


def translation_binding_issue(record: Mapping, evidence: Mapping | None = None, *,
                              source_validated: bool = False) -> str | None:
    """Verify explicit identities, and bind independent text to a known variant."""
    asin = _text(record.get("asin"))
    if _asins(record) - {asin}:
        return "ASIN_MISMATCH"
    if evidence is None:
        return None
    if _asins(evidence) - {asin}:
        return "ASIN_MISMATCH"

    variant = _values(record, _VARIANT_FIELDS[:1]) or _values(record, _VARIANT_FIELDS[1:])
    source_variant = _values(evidence, _VARIANT_FIELDS)
    if variant and source_variant - variant:
        return "VARIANT_MISMATCH"

    snapshots = _values(record, _SNAPSHOT_FIELDS)
    source_snapshots = _values(evidence, _SNAPSHOT_FIELDS)
    if snapshots and source_snapshots - snapshots:
        return "SOURCE_HASH_MISMATCH"
    if bool(variant) != bool(source_variant) and not source_validated and not snapshots & source_snapshots:
        return "VARIANT_UNVERIFIED"
    return None


def translation_content_issue(record: Mapping, value: str) -> str | None:
    """Identify explicit foreign ASINs and confirmed semantic regression patterns."""
    asin = _text(record.get("asin"))
    text = _text(value)
    embedded = set(re.findall(r"\basin\s*[:：]\s*([a-z0-9]{10})\b", text))
    if embedded - {asin}:
        return "ASIN_MISMATCH"
    title = next((_text(record.get(key)) for key in PRODUCTION_FIELD_ALIASES["title_es_raw"]
                  if record.get(key)), "")
    if any(source.search(title) and foreign.search(text) for source, foreign in _CROSS_PRODUCT):
        return "CROSS_PRODUCT_CONTENT"
    return None
