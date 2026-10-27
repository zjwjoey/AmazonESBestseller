# -*- coding: utf-8 -*-
"""Deterministic Translation V2 input preparation and audit.

Pre-clean never overwrites source evidence.  It emits a derived translation
input view plus auditable quality reports, and it never constructs a provider.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from .dictionary_only import load_records
from .dictionary_service import DictionaryService, is_identity_attribute, normalize_key, resolve_exact
from .production_contract import PRODUCTION_FIELD_ALIASES
from .terminology import specification_is_deterministic
from .capacity_scenarios import compatible_reservoir_modes, scenario_exclusions, title_bound_components
from .capacity_sets import compatible_explicit_sets


CLEAN_SCHEMA_VERSION = "preclean-v1"
FIELD_ALIASES = dict(PRODUCTION_FIELD_ALIASES)
AUDIT_FIELDS = tuple(FIELD_ALIASES)
NON_BLOCKING_ISSUES = frozenset({"SAFE_DUPLICATE", "DUPLICATE_BULLET", "CROSS_FIELD_OVERLAP",
                                "EQUIVALENT_SOURCE_VALUES"})
# Match actual identity/metadata syntax, not ordinary Spanish words beginning
# with B or prose about sorting, product grades and customer appreciation.
DESCRIPTION_METADATA_RE = re.compile(
    r"\bASIN\b|商品编号|\bclasificaci[oó]n\s+(?:en\s+)?(?:los\s+)?m[aá]s\s+vendidos\b|"
    r"\bclasificaci[oó]n\s*:\s*#?\d|\b(?:valoraci[oó]n|opiniones|reviews?)\s*:\s*\d|"
    r"\b\d+(?:[.,]\d+)?\s+(?:de|out of)\s+5\s+(?:estrellas?|stars?)\b", re.I)
DESCRIPTION_ASIN_RE = re.compile(r"\bB(?=[A-Z0-9]{0,8}[0-9])[A-Z0-9]{9}\b")
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]")
# Only known HTML element names are removable syntax. Angle-bracketed
# comparisons and product identifiers are source facts, not arbitrary tags.
HTML_TAG_RE = re.compile(
    r"</?(?:a|abbr|article|b|blockquote|br|code|dd|div|dl|dt|em|h[1-6]|hr|i|img|li|"
    r"ol|p|pre|section|small|span|strong|sub|sup|table|tbody|td|th|thead|tr|u|ul)"
    r"(?:\s[^<>]*?)?\s*/?>", re.I)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
UI_RE = re.compile(r"(?:…|\.\.\.)?\s*(?:Ver\s+(?:m[aá]s|menos)|查看更多|展开|收起)\b", re.I)
PROTECTED_TOKEN_RE = re.compile(
    r"(?<!\w)(?:USB[- ]?[A-Z]|PD\s*\d+(?:\.\d+)?|QC\s*\d+(?:\.\d+)?|"
    r"IP\w+|E\d{2}|A\d|M\d+|\d+(?:[.,]\d+)?\s*(?:mAh|Ah|PSI|bar|V|W|kW|Hz|MHz|GHz|°C|%)|"
    r"\d+(?:[-/]\d+)+)(?!\w)", re.I)
NUMERIC_UNIT_RE = re.compile(
    r"(?<!\w)(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>"
    r"miliamperios?\s*hora|amperios?\s*hora|mil[ií]metros?|cent[ií]metros?|kil[oó]metros?|"
    r"kilogramos?|miligramos?|mililitros?|centilitros?|litros?|gramos?|metros?|"
    r"megahercios?|gigahercios?|kilohercios?|hercios?|kilovatios?|vatios?|voltios?|amperios?|"
    r"mAh|Ah|MHz|GHz|kHz|Hz|kW|W|V|PSI|bar|°C|kg|mg|ml|cl|cc|L|g|km|cm|mm|m|A|%)"
    r"(?![A-Za-zÁÉÍÓÚÜÑáéíóúüñ])",
    re.I,
)
UNIT_CANONICAL = {
    "gramo": "g", "gramos": "g", "kg": "kg", "kilogramo": "kg", "kilogramos": "kg",
    "mg": "mg", "miligramo": "mg", "miligramos": "mg", "ml": "ml", "mililitro": "ml", "mililitros": "ml",
    "cl": "cl", "centilitro": "cl", "centilitros": "cl", "cc": "ml", "l": "L", "litro": "L", "litros": "L",
    "mm": "mm", "milímetro": "mm", "milímetros": "mm", "cm": "cm", "centímetro": "cm", "centímetros": "cm",
    "m": "m", "metro": "m", "metros": "m", "km": "km", "kilómetro": "km", "kilómetros": "km",
    "v": "V", "voltio": "V", "voltios": "V", "w": "W", "vatio": "W", "vatios": "W",
    "kw": "kW", "kilovatio": "kW", "kilovatios": "kW", "a": "A", "amperio": "A", "amperios": "A",
    "hz": "Hz", "hercio": "Hz", "hercios": "Hz", "khz": "kHz", "kilohercio": "kHz", "kilohercios": "kHz",
    "mhz": "MHz", "megahercio": "MHz", "megahercios": "MHz", "ghz": "GHz", "gigahercio": "GHz", "gigahercios": "GHz",
    "mah": "mAh", "miliamperio hora": "mAh", "miliamperios hora": "mAh", "ah": "Ah", "amperio hora": "Ah", "amperios hora": "Ah",
    "bar": "bar", "psi": "PSI", "°c": "°C", "%": "%",
}
SPANISH_WORDS = {"para", "del", "de", "con", "sin", "producto", "color", "tamaño", "modelo", "material", "peso", "número"}
ENGLISH_WORDS = {"the", "for", "with", "without", "product", "color", "size", "model", "material", "weight", "number"}
GERMAN_WORDS = {"und", "der", "die", "das", "gewicht", "farbe", "größe", "modell"}
FRENCH_WORDS = {"pour", "avec", "sans", "produit", "couleur", "taille", "modèle", "poids"}
ITALIAN_WORDS = {"per", "con", "senza", "prodotto", "colore", "taglia", "modello", "peso"}
LANGUAGE_SHARED_WORDS = {"color", "material", "modelo", "model", "producto", "product",
                         "peso", "weight", "número", "number", "para", "con", "sin", "size"}


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


def _source_value(record: Mapping[str, Any], field: str) -> Any:
    for alias in FIELD_ALIASES[field]:
        value = record.get(alias)
        if value not in (None, "", [], {}):
            return value
    return ""


def _collapse_repeated(text: str) -> tuple[str, bool]:
    stripped = text.strip()
    if len(stripped) < 4 or len(stripped) % 2:
        return stripped, False
    half = len(stripped) // 2
    left, right = stripped[:half].strip(), stripped[half:].strip()
    if left and left == right:
        return left, True
    return stripped, False


def clean_text(value: Any, *, field: str = "") -> dict[str, Any]:
    """Clean UI/control noise while retaining source and an action ledger."""
    source = _text(value)
    if not source.strip():
        return {"source_text": source, "clean_text": "", "clean_status": "SOURCE_MISSING",
                "actions": [], "issues": [], "translate_allowed": False,
                "removed_ui_artifacts": 0, "removed_zero_width": 0,
                "html_detected": False}
    text = html.unescape(source)
    actions: list[str] = []
    issues: list[str] = []
    html_detected = bool(HTML_TAG_RE.search(text))
    if html_detected:
        text = HTML_TAG_RE.sub(" ", text)
        actions.append("REMOVE_HTML_TAGS")
    ui_hits = len(UI_RE.findall(text))
    if ui_hits:
        text = UI_RE.sub(" ", text)
        actions.append("REMOVE_UI_ARTIFACT")
    zero_hits = len(ZERO_WIDTH_RE.findall(text))
    if zero_hits:
        text = ZERO_WIDTH_RE.sub("", text)
        actions.append("REMOVE_ZERO_WIDTH")
    if CONTROL_RE.search(text):
        text = CONTROL_RE.sub("", text)
        actions.append("REMOVE_CONTROL_CHARS")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    collapsed, repeated = _collapse_repeated(text)
    if repeated:
        text = collapsed
        actions.append("DEDUPLICATE_CONTIGUOUS_REPEAT")
    if not text:
        issues.append("EMPTY_AFTER_CLEAN")
    status = "NORMALIZED" if actions or text != source else "CLEAN"
    return {"source_text": source, "clean_text": text, "clean_status": status,
            "actions": actions, "issues": issues, "translate_allowed": not issues,
            "protected_tokens": sorted(set(PROTECTED_TOKEN_RE.findall(text)), key=str.casefold),
            "removed_ui_artifacts": ui_hits, "removed_zero_width": zero_hits,
            "html_detected": html_detected}


_FACT_NUMBER = r"\d+(?:[.,]\d+)?"
_FACT_MASS = r"(?P<mass>" + _FACT_NUMBER + r")\s*(?P<mass_unit>kilogramos?|gramos?|kg|g)"
_FACT_AXIS = r"\s*(?:l\.?|f\.?|an\.?|al\.?|p\.?|largo|ancho|alto|fondo|profundidad|grosor)?\s*"
_DIMENSION_FACT_RE = re.compile(
    r"(?P<a>" + _FACT_NUMBER + r")" + _FACT_AXIS + r"[x×]\s*(?P<b>" + _FACT_NUMBER + r")" +
    _FACT_AXIS + r"(?:[x×]\s*(?P<c>" + _FACT_NUMBER + r")" + _FACT_AXIS + r")?"
    r"(?P<unit>milimetros?|centimetros?|metros?|mm|cm|m)(?:\s*;\s*" + _FACT_MASS + r")?")


def _equivalent_detail_facts(label: str, values: list[str]) -> bool:
    """Compare explicit typed facts exactly, keeping every source row/value."""
    texts = [normalize_key(value) for value in values]
    # A nonzero integer followed by exactly three decimal/grouping digits
    # can mean a decimal or a thousands separator. Do not guess its locale.
    if any(re.search(r"[1-9]\d*[.,]\d{3}(?!\d)", text) for text in texts):
        return False
    def number(text: str) -> Decimal:
        return Decimal(text.replace(",", "."))
    def mass(match: Any) -> Decimal | None:
        if not match or not match.group("mass"):
            return None
        unit = match.group("mass_unit")
        return number(match.group("mass")) * (1000 if unit.startswith("k") else 1)
    if label == "numero de unidades":
        matches = [re.fullmatch(r"(\d+(?:[.,]0{1,2})?)(?:\s*(?:conteo|unidades?))?", t) for t in texts]
        return all(matches) and len({number(m.group(1)) for m in matches}) == 1
    if label == "forma articulo":
        return any(set(texts) <= synonyms for synonyms in (
            {"circular", "redonda", "redondo"}, {"cuadrado", "cuadrada"}))
    if label == "edad recomendada por el fabricante":
        # Singular/plural spelling does not change an explicit lower bound.
        # A finite age range or another unit must still be reviewed.
        matches = [re.fullmatch(r"(\d+) anos? y mas", t) for t in texts]
        return all(matches) and len({int(m.group(1)) for m in matches}) == 1
    if label == "peso del producto":
        matches = [re.fullmatch(_FACT_MASS, t) for t in texts]
        return all(matches) and len({mass(m) for m in matches}) == 1
    if label in {"dimensiones del producto", "dimensiones del paquete"}:
        matches = [_DIMENSION_FACT_RE.fullmatch(t) for t in texts]
        if not all(matches):
            return False
        dimensions = []
        for m in matches:
            unit = m.group("unit")
            factor = Decimal("0.001") if unit.startswith("mili") or unit == "mm" else (
                Decimal("0.01") if unit.startswith("centi") or unit == "cm" else Decimal(1))
            dimensions.append(tuple(number(m.group(axis)) * factor for axis in ("a", "b", "c") if m.group(axis)))
        return len(set(dimensions)) == 1 and len({mass(m) for m in matches if m.group("mass")}) <= 1
    return False


def parse_details(value: Any, *, source_attributes: Any = None) -> dict[str, Any]:
    """Parse details without discarding malformed source rows."""
    source = _text(value)
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    if isinstance(value, Mapping):
        iterable = list(value.items())
    elif isinstance(value, list):
        iterable = []
        for item in value:
            if isinstance(item, Mapping):
                label = item.get("label_raw") or item.get("label") or item.get("name")
                raw_value = item.get("value_raw") or item.get("value")
                iterable.append((label, raw_value))
            else:
                iterable.append((None, item))
    else:
        iterable = []
        # A flattened label may itself contain ':'. Recover its boundary
        # only when both its full label and value agree with a raw attribute.
        # Keep the selected source text/hash; do not replace it with stale RAW.
        hints: dict[str, set[str]] = defaultdict(set)
        if isinstance(source_attributes, list):
            for attribute in source_attributes:
                if not isinstance(attribute, Mapping):
                    continue
                label = clean_text(attribute.get("label_raw") or attribute.get("label"))["clean_text"]
                raw_value = attribute.get("value_raw") or attribute.get("value")
                if ":" in label or "：" in label:
                    hints[label].add(normalize_key(clean_text(raw_value)["clean_text"]))
        for line in source.splitlines():
            if not line.strip():
                continue
            if ":" not in line and "：" not in line:
                issues.append("VALUE_WITHOUT_LABEL")
                continue
            label, raw_value = re.split(r"[:：]", line, maxsplit=1)
            for candidate in sorted(hints, key=len, reverse=True):
                match = re.match(r"^\s*" + re.escape(candidate) + r"\s*[:：]", line, re.I)
                if match and normalize_key(clean_text(line[match.end():])["clean_text"]) in hints[candidate]:
                    label, raw_value = candidate, line[match.end():]
                    break
            iterable.append((label, raw_value))
    for position, (label, raw_value) in enumerate(iterable):
        label_clean = clean_text(label, field="detail_label") if label is not None else None
        value_clean = clean_text(raw_value, field="detail_value") if raw_value is not None else None
        if label_clean is None or not label_clean["clean_text"]:
            issues.append("LABEL_MISSING")
            continue
        if value_clean is None or not value_clean["clean_text"]:
            issues.append("VALUE_MISSING")
            continue
        rows.append({"label": label_clean["clean_text"], "value": value_clean["clean_text"],
                     "position": position, "source": "preclean", "label_clean": label_clean,
                     "value_clean": value_clean})
    raw_rows = list(rows)
    # Deduplicate only exact label/value pairs in the derived input.  The
    # original source remains intact in source_text and raw_fields.  A safe
    # duplicate is informational. Explicit, exactly equivalent typed values
    # are also informational, but both literal rows remain in the derived
    # detail list so additional evidence such as an appended mass is retained.
    exact_seen: set[tuple[str, str]] = set()
    deduped_rows: list[dict[str, Any]] = []
    exact_duplicates = 0
    for row in rows:
        pair = (normalize_key(row["label"]), normalize_key(row["value"]))
        if pair in exact_seen:
            exact_duplicates += 1
            continue
        exact_seen.add(pair)
        deduped_rows.append(row)
    rows = deduped_rows
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        groups[normalize_key(row["label"])].append(row["value"])
    conflicting_duplicate_labels = equivalent_source_groups = 0
    for label, values in groups.items():
        if len(values) < 2:
            continue
        if _equivalent_detail_facts(label, values):
            equivalent_source_groups += 1
        else:
            conflicting_duplicate_labels += len(values) - 1
    safe_duplicate_rows = exact_duplicates
    if safe_duplicate_rows:
        issues.append("SAFE_DUPLICATE")
    if conflicting_duplicate_labels:
        issues.append("CONFLICTING_DUPLICATE")
    if equivalent_source_groups:
        issues.append("EQUIVALENT_SOURCE_VALUES")
    if not source.strip():
        status = "SOURCE_MISSING"
    elif not rows:
        status = "BLOCKED" if issues else "SUSPICIOUS"
    elif conflicting_duplicate_labels or any(issue not in NON_BLOCKING_ISSUES for issue in issues):
        status = "NEEDS_REVIEW"
    elif safe_duplicate_rows or equivalent_source_groups:
        status = "NORMALIZED"
    else:
        status = "CLEAN"
    return {"source_text": source, "rows": rows, "raw_rows": raw_rows, "issues": sorted(set(issues)),
            "duplicate_labels": conflicting_duplicate_labels,
            "safe_duplicate_rows": safe_duplicate_rows,
            "conflicting_duplicate_labels": conflicting_duplicate_labels,
            "equivalent_source_groups": equivalent_source_groups,
            "actions": ["DEDUPLICATE_DETAIL_ROW"] if safe_duplicate_rows else [],
            "status": status,
            "fully_structured": bool(rows) and not any(issue not in NON_BLOCKING_ISSUES for issue in issues),
            "partially_structured": bool(rows) and any(issue not in NON_BLOCKING_ISSUES for issue in issues),
            "unstructured": bool(source.strip()) and not rows}


def parse_bullets(value: Any) -> dict[str, Any]:
    source = _text(value)
    raw_items = value if isinstance(value, list) else source.splitlines()
    items = []
    issues: list[str] = []
    seen = set()
    for item in raw_items:
        cleaned = clean_text(item, field="feature_bullet")
        text = cleaned["clean_text"]
        if not text:
            issues.append("EMPTY_BULLET")
            continue
        key = normalize_key(text)
        if key in seen:
            issues.append("DUPLICATE_BULLET")
            continue
        seen.add(key)
        if len(text) > 1000:
            issues.append("LONG_BULLET")
        items.append({"source_text": _text(item), "clean_text": text, "clean": cleaned})
    if not source.strip():
        status = "SOURCE_MISSING"
    elif any(issue != "DUPLICATE_BULLET" for issue in issues):
        status = "NEEDS_REVIEW"
    elif issues or any(item["clean"]["clean_status"] == "NORMALIZED" for item in items):
        status = "NORMALIZED"
    else:
        status = "CLEAN"
    return {"source_text": source, "items": items, "issues": sorted(set(issues)), "status": status,
            "actions": ["DEDUPLICATE_BULLET"] if "DUPLICATE_BULLET" in issues else [],
            "duplicate_bullets": sum(1 for issue in issues if issue == "DUPLICATE_BULLET"),
            "empty_bullets": sum(1 for issue in issues if issue == "EMPTY_BULLET")}


def language_label(value: str) -> str:
    words = set(re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}", value.casefold()))
    exclusive = words - LANGUAGE_SHARED_WORDS
    scores = {
        "Spanish": len(exclusive & SPANISH_WORDS), "English": len(exclusive & ENGLISH_WORDS),
        "German": len(exclusive & GERMAN_WORDS), "French": len(exclusive & FRENCH_WORDS),
        "Italian": len(exclusive & ITALIAN_WORDS),
    }
    ranked = sorted(scores.items(), key=lambda row: (-row[1], row[0]))
    if not words or ranked[0][1] == 0:
        return "Unknown"
    positive = [score for _, score in ranked if score > 0]
    if len(positive) > 1 and positive[1] >= 2 and positive[1] / max(1, positive[0]) >= 0.5:
        return "Mixed"
    return ranked[0][0]


def _explicit_capacity_total(value: str, capacities: list[tuple[Any, Decimal]], *, conflict_only: bool = False) -> bool:
    """Validate a completely bound, exact component/total statement.

    Keep occurrences, not just distinct amounts: one correct equation must
    never exempt a stray quantity or a second, incompatible count/total.
    No result of this calculation is written back as a source fact.
    ``conflict_only`` requires a literal total declaration. A completely
    bound equation may contradict it, as may an independently bound positive
    subtotal already exceeding it; unknown quantities cannot subtract volume.
    """
    if len(capacities) < 2:
        return False
    count_words = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3,
                   "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
                   "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12}
    number = r"(?:[1-9]\d*|" + "|".join(count_words) + r")"
    noun = r"(?:contenedor(?:es)?|recipientes?|compartimentos?|bolsas?|cajas?|capas?|tazas?|cestas?|cubos?|botellas?|vasos?|cuencos?|frascos?)"
    counted = re.compile(r"(?<![\w.,])(?P<count>" + number + r")\s+(?P<noun>" + noun + r")\b", re.I)

    def positive_claim(text: str) -> bool:
        return not re.search(r"\b(?:no\s+(?:incluye|contiene|tiene|ofrece)|sin\s+incluir|"
                             r"por\s+ejemplo|ejemplo\s+de\s+otro\s+producto|"
                             r"proporci[oó]n|relaci[oó]n|diluci[oó]n)\b", text, re.I)

    def count_number(text: str) -> int:
        return int(text) if text.isdigit() else count_words[text.casefold()]

    def noun_key(text: str) -> str:
        text = text.casefold()
        return "contenedor" if text == "contenedores" else text.removesuffix("s")

    declarations: dict[str, set[int]] = defaultdict(set)
    for declaration in counted.finditer(value):
        statement = re.split(r"[\n;]|(?<!\d)\.\s+", value[:declaration.start()])[-1]
        if not positive_claim(statement):
            continue
        declarations[noun_key(declaration.group("noun"))].add(count_number(declaration.group("count")))

    def prefix(index: int) -> str:
        start = capacities[index - 1][0].end() if index else 0
        # Decimal points are not sentence boundaries.
        return re.split(r"[\n;]|(?<!\d)\.\s+", value[start:capacities[index][0].start()])[-1]

    def explicit_total(index: int) -> bool:
        before, after = prefix(index), value[capacities[index][0].end():]
        return bool(re.search(r"\btotal(?:\s+(?:de|del)\s+(?:juego|set|pack|cesto|cubo|recipiente))?\s*(?:de|:)?\s*$",
                              before, re.I)
                    or re.match(r"\s*(?:de\s+capacidad\s+total|total(?!\s*(?:(?:de|:)\s*)?\d))\b", after, re.I))

    def nominal_total(index: int) -> bool:
        before, after = prefix(index), value[capacities[index][0].end():]
        if re.search(r"\b(?:otro\s+volumen|otra\s+capacidad|volumen\s+desconocido|capacidad\s+desconocida)\b", before, re.I):
            return False
        return bool(re.search(r"\b(?:capacidad|volumen)(?:\s+(?:generosa|mega))?\s*(?:es\s*)?(?:de|:)?\s*$", before, re.I)
                    or re.match(r"\s+de\s+capacidad\b", after, re.I)
                    or re.search(r"\bincluye\s*:\s*[^\d\n.;:]{0,60}\bfreidora\b[^\d\n.;:]{0,50}$", before, re.I)
                    or (index == 0 and re.match(r"\s*cubo\b", before, re.I)
                        and not re.search(r"[\n.;]", before)))

    def counted_component(index: int) -> tuple[str, int] | None:
        before = prefix(index)
        literal = re.search(r"(?<![\w.,])([1-9]\d*)\s*[x×]\s*$", before, re.I)
        if literal:
            # A dilution ratio is not a count of containers.
            if re.search(r"\b(?:proporci[oó]n|relaci[oó]n|diluci[oó]n|escala)\b", before, re.I):
                return None
            return "literal:" + str(capacities[index][0].start()), int(literal.group(1))
        hits = list(counted.finditer(before))
        if len(hits) == 1:
            hit = hits[0]
            tail = before[hit.end():]
            # Allow an explicit intervening height, but never borrow another
            # bare number such as a model/count or an unrelated mixing ratio.
            untyped = re.sub(r"\d+(?:[.,]\d+)?\s*(?:mm|cm)\b", "", tail, flags=re.I)
            if not re.search(r"\d|[:/]", untyped) and not re.search(r"\b" + noun + r"\b", tail, re.I):
                return "counted:" + str(capacities[index][0].start()), count_number(hit.group("count"))
        after = value[capacities[index][0].end():]
        each = re.search(r"\bcada\s+(?P<noun>" + noun + r")\b[^\d\n.;]{0,45}$", before, re.I)
        following = re.match(r"\s+(?:para\s+)?cada\s+(?P<noun>" + noun + r"|una|uno)\b", after, re.I)
        reference = each or following
        if reference:
            name = reference.group("noun").casefold()
            if name in {"una", "uno"}:
                pack = re.match(r"\s+cada\s+(?:una|uno),\s*(?:este\s+|el\s+)?(?:pack|juego|set)\s+de\s+(?P<count>" +
                                number + r")\s+(?P<noun>" + noun + r")\b", after, re.I)
                if pack:
                    return "each:" + str(capacities[index][0].start()), count_number(pack.group("count"))
                return None
            counts = declarations.get(noun_key(name), set())
            if len(counts) == 1:
                return "each:" + str(capacities[index][0].start()), next(iter(counts))
        return None

    def named_component(index: int) -> str | None:
        before, after = prefix(index), value[capacities[index][0].end():]
        for pattern, role in (
            (r"\bcesta\s+superior\s+de\s*$", "upper_basket"),
            (r"\bcesta\s+inferior\s+de\s*$", "lower_basket"),
            (r"\brecipiente\s+de\s*$", "main_container"),
            (r"\baccesorio\s+de\s*$", "accessory"),
        ):
            if re.search(pattern, before, re.I):
                return role
        for pattern, role in ((r"\s+para\s+los\s+encurtidos\b", "pickles"),
                              (r"\s+para\s+el\s+caldo\b", "brine")):
            if re.match(pattern, after, re.I):
                return role
        return None

    # A literal '+' is an additive component list. A comma/and list needs
    # an explicit matching compartment count, not a list of available sizes.
    chained: dict[int, tuple[str, int]] = {}
    additive_lists: list[range] = []
    start = 0
    while start < len(capacities):
        end = start
        links = []
        while end + 1 < len(capacities):
            link = value[capacities[end][0].end():capacities[end + 1][0].start()].strip().casefold()
            if link not in {"+", ",", "y"}:
                break
            links.append(link)
            end += 1
        compartment_counts = declarations.get("compartimento", set())
        if end > start and (all(link == "+" for link in links)
                            or compartment_counts == {end - start + 1}):
            for index in range(start, end + 1):
                chained[index] = ("list:" + str(index), 1)
            if all(link == "+" for link in links):
                additive_lists.append(range(start, end + 1))
        start = end + 1

    totals: set[Decimal] = set()
    stated_totals: set[Decimal] = set()
    components: dict[str, tuple[Decimal, int]] = {}
    independently_bound_subtotals: list[Decimal] = []
    bound_positions: set[int] = set()
    incomplete = False
    for index, (_, amount) in enumerate(capacities):
        if not positive_claim(prefix(index)):
            if not conflict_only:
                return False
            incomplete = True
            continue
        if explicit_total(index):
            totals.add(amount)
            stated_totals.add(amount)
            continue
        component = counted_component(index)
        if index in chained:
            component = (chained[index][0], component[1] if component else 1)
        if not component:
            name = named_component(index)
            component = ("named:" + name, 1) if name else None
        if component:
            role, count = component
            bound_positions.add(index)
            if index not in chained:
                independently_bound_subtotals.append(amount * count)
            if role in components and components[role] != (amount, count):
                if not conflict_only:
                    return False
                incomplete = True
                continue
            components[role] = (amount, count)
        elif nominal_total(index):
            totals.add(amount)
            # Plain 'Capacidad 500 ml' can be the capacity of each cup in
            # a pack; it is insufficient evidence of the pack's total.
            if conflict_only:
                incomplete = True
        else:
            if not conflict_only:
                return False
            incomplete = True
    if conflict_only:
        if not stated_totals:
            return False
        for sequence in additive_lists:
            if all(index in bound_positions for index in sequence):
                # Literal '+' terms give a positive lower bound without
                # borrowing a count or assigning any unknown quantity.
                independently_bound_subtotals.append(sum(
                    (capacities[index][1] for index in sequence), Decimal(0)))
        if any(subtotal > total for subtotal in independently_bound_subtotals for total in stated_totals):
            return True
        if incomplete:
            return False
    if not totals or not components:
        return False
    computed = sum((amount * count for amount, count in components.values()), Decimal(0))
    if conflict_only:
        return any(total != computed for total in stated_totals)
    return totals == {computed}


def _named_capacity_scopes(value: str) -> tuple[dict[int, str], bool]:
    """Bind literal named parts, and only complete repetitions of those parts."""
    matches = [m for m in NUMERIC_UNIT_RE.finditer(value)
               if UNIT_CANONICAL.get(m.group("unit").casefold()) in {"ml", "cl", "L"}]
    factors = {"ml": Decimal(1), "cl": Decimal(10), "L": Decimal(1000)}
    def amount(m: Any) -> Decimal:
        return _numeric_amount(value, m) * factors[UNIT_CANONICAL[m.group("unit").casefold()]]

    scopes: dict[int, str] = {}
    context = bool(re.search(
        r"\b(?:tanque|dep[oó]sito)\s+de\s+(?:agua|leche)\b|"
        r"\b(?:primer|segundo)\s+compartimento\b|"
        r"\b(?:capa|parte|compartimento|recipiente|envase|vaso)\s+(?:superior|inferior)\b|"
        r"\brecipiente\s+(?:grande|pequeño|principal)\b|\bcaj[oó]n\s+(?:XXL|est[aá]ndar)\b",
        value, re.I))
    layered = bool(re.search(r"\b(?:capa|parte|compartimento)\s+superior\b", value, re.I)
                   and re.search(r"\b(?:capa|parte|compartimento)\s+inferior\b", value, re.I))
    for i, m in enumerate(matches):
        start = max(matches[i - 1].end() if i else 0, m.start() - 160)
        prefix = value[start:m.start()]
        tank = re.search(r"\b(?:tanque|dep[oó]sito)\s+de\s+(agua(?:\s+(?:limpia|sucia))?|leche)"
                         r"(?:\s+extra[ií]ble)?\s*:?\s*(?:gran\s+)?(?:capacidad\s+)?(?:de\s*)?$", prefix, re.I)
        ordinal = re.search(r"\b(primer|segundo)\s+compartimento\s*(?:tiene\s+una\s+capacidad\s+)?de\s*$", prefix, re.I)
        direct = re.search(r"\b(?:capa|parte|compartimento|recipiente|envase|vaso)\s+(superior|inferior)"
                           r"(?:\s+independiente)?\s*(?:(?:tiene\s+(?:una\s+)?capacidad|es)\s+)?de\s*$", prefix, re.I)
        inverted = re.search(r"\bcapacidad\s+de\s+la\s+capa\s+(superior|inferior)\s+es\s+de\s*$", prefix, re.I)
        capacity_side = re.search(r"\bcapacidad\s+(superior|inferior)\s+de\s*$", prefix, re.I) if layered else None
        ellipsis = re.search(r"\b(?:la\s+de\s+la|la|el)\s+(superior|inferior)\s*,?\s*"
                             r"de\s*(?:gran\s+capacidad\s*\(\s*)?$", prefix, re.I)
        drawer = re.search(r"\bcaj[oó]n\s+(XXL|est[aá]ndar)\s+de\s*$", prefix, re.I)
        vessel = re.search(r"\brecipiente\s+(grande|pequeño|principal)\s+de\s*$", prefix, re.I)
        if tank:
            scopes[m.start()] = "tank:" + tank.group(1).lower()
        elif ordinal:
            scopes[m.start()] = "compartment:" + ordinal.group(1).lower()
        elif direct or inverted or capacity_side:
            direction = (direct or inverted or capacity_side).group(1).lower()
            scopes[m.start()] = "upper_container" if direction == "superior" else "lower_container"
        elif (ellipsis and i and scopes.get(matches[i - 1].start()) in {"upper_container", "lower_container"}
              and not re.search(r"[\n;]|(?<!\d)\.\s+", prefix)):
            scopes[m.start()] = "upper_container" if ellipsis.group(1).lower() == "superior" else "lower_container"
        elif drawer:
            scopes[m.start()] = "drawer:" + ("xxl" if drawer.group(1).lower() == "xxl" else "standard")
        elif vessel:
            scopes[m.start()] = "vessel:" + vessel.group(1).lower()
        # A replacement clause gives both container antecedents explicitly;
        # the included-parts list below must independently confirm the pair.
        elif i and re.fullmatch(r"\s+o\s+c[aá]mbialo\s+por\s+el\s+de\s*", prefix, re.I):
            previous = matches[i - 1]
            if re.search(r"\brecipiente\s+de\s+cristal\s+de\s*$", value[max(0, previous.start()-60):previous.start()], re.I):
                scopes[previous.start()] = "vessel:primary"
                scopes[m.start()] = "vessel:alternate"
                context = True

    # A complete two-drawer contrast names the second object as "the other"
    # without guessing that its capacity belongs to the standard drawer.
    if re.search(r"\bcaj[oó]n\s+XXL\s+y\s+caj[oó]n\s+est[aá]ndar\b", value, re.I):
        for previous, current in zip(matches, matches[1:]):
            if scopes.get(previous.start()) != "drawer:xxl" or current.start() in scopes:
                continue
            lead = value[value.rfind("\n", 0, previous.start())+1:previous.start()]
            bridge = value[previous.end():current.start()]
            if (re.search(r"\butiliza\s+el\s+caj[oó]n\s+XXL\s+de\s*$", lead, re.I)
                    and re.fullmatch(r"\s+para\s+cocinar[^.\n;]{0,110}\b(?:ambos|los\s+dos)\s+cajones\b"
                                     r"[^.\n;]{0,110}\bo\s+el\s+caj[oó]n\s+de\s*", bridge, re.I)
                    and not re.search(r"\b(?:no|sin|ejemplo|hipot[eé]tico|otro\s+modelo)\b", lead+bridge, re.I)):
                scopes[current.start()] = "drawer:explicit_other"

    # An unqualified water tank may be the clean or dirty one; never create
    # an additional independent object merely because its adjective is absent.
    if any(s in {"tank:agua limpia", "tank:agua sucia"} for s in scopes.values()):
        scopes = {pos: s for pos, s in scopes.items() if s != "tank:agua"}

    number = r"\d+(?:[.,]\d+)?"
    unit = r"(?:mililitros?|centilitros?|litros?|ml|cl|L)"
    pair = rf"(?P<a>{number})\s*(?P<ua>{unit})?\s*(?:\+|y)\s*(?P<b>{number})\s*(?P<ub>{unit})"
    lists = []
    for pattern, requires_included_parts in (
        (r"\b(?:2|dos)\s+recipientes\s+de\s+(?:vidrio|cristal)[^()\n;]{0,90}\((?P<body>[^()]+)\)", True),
        (rf"\b(?:2|dos)\s+recipientes\s+de\s+(?:vidrio|cristal)(?:\s+[A-Za-z]+){{0,2}}?\s+de\s+(?P<body>{pair})", True),
        (rf"\bCapacidad\s*:\s*(?P<body>{pair})", False),
        (rf"(?P<body>{pair})\s*:\s*capacidad\b", False),
    ):
        for item in re.finditer(pattern, value, re.I):
            body = re.fullmatch(pair, item.group("body").strip(), re.I)
            if not body or re.match(rf"\s*(?:[+]\s*\d|y\s+\d|,\s*{number}\s*{unit}\b)", value[item.end():], re.I):
                continue
            if requires_included_parts:
                line_prefix = value[:item.start()].rsplit("\n", 1)[-1]
                if not re.match(r"\s*INCLUYE(?:\s*:|\s+)", line_prefix, re.I):
                    continue
            listed = [Decimal(body.group(k).replace(",", ".")) *
                      factors[UNIT_CANONICAL[(body.group("ua") if k == "a" and body.group("ua") else body.group("ub")).casefold()]]
                      for k in ("a", "b")]
            prior = {s: {amount(m) for m in matches if m.start() < item.start() and scopes.get(m.start()) == s}
                     for s in set(scopes.values())}
            prior = {s: values for s, values in prior.items() if values}
            if len(prior) != 2 or any(len(values) != 1 for values in prior.values()):
                continue
            unique = {next(iter(values)): s for s, values in prior.items()}
            if len(unique) != 2 or sorted(listed) != sorted(unique):
                continue
            for m in matches:
                if item.start("body") <= m.start() < item.end("body"):
                    scopes[m.start()] = unique[amount(m)]
            lists.append(set(prior))

    vessel_pair = {"vessel:grande", "vessel:pequeño"}
    replacement_pair = {"vessel:primary", "vessel:alternate"}
    for required in (vessel_pair, replacement_pair):
        if not any(required == found for found in lists):
            scopes = {pos: s for pos, s in scopes.items() if s not in required}
    if any(replacement_pair == found for found in lists):
        primary = {amount(m) for m in matches if scopes.get(m.start()) == "vessel:primary"}
        for m in matches:
            if (amount(m) in primary
                    and re.search(r"\b(?:air\s+fryer|freidora\s+de\s+aire)\s+de\s+cristal\s+extragrande\s*$",
                                  value[max(0,m.start()-90):m.start()], re.I)
                    and re.match(r"\s+DE\s+CAPACIDAD\b", value[m.end():], re.I)):
                scopes[m.start()] = "vessel:primary"
    return scopes, context


def _capacity_scope(value: str, match: Any) -> str:
    """Use literal object qualifiers only; an unqualified value stays unknown."""
    named, named_context = _named_capacity_scopes(value)
    if named_context:
        return named.get(match.start(), "unknown")
    prefix = re.split(r"[\n;]|(?<!\d)\.\s+", value[:match.start()])[-1][-120:]
    candidates = []
    for pattern, scope in (
        (r"(?:compatibles?\s+con|ideales?\s+para|capacidad\s+para)\s+(?:una\s+)?bolsas?\b", "accessory_bag"),
        (r"\bescala\b", "graduation_scale"),
        (r"\bcapacidad\s+de\s+filtrado\b", "filtered_water"),
        (r"\b(?:recipiente|envase|vaso|compartimento)\s+inferior\b", "lower_container"),
        (r"\b(?:recipiente|envase|vaso|compartimento)\s+superior\b", "upper_container"),
        (r"\bjarra\b", "jug"),
    ):
        candidates.extend((m.start(), scope) for m in re.finditer(pattern, prefix, re.I))
    component = re.compile(r"\b\d+\s*[x×]\s*(cuenco|recipiente|vaso|taza|copa|molde|botella)\s+"
                           r"(\d+(?:[.,]\d+)?)\s*(cm|mm)\b", re.I)
    for m in component.finditer(prefix):
        size = Decimal(m.group(2).replace(",", ".")) * (Decimal("0.01") if m.group(3).lower() == "cm" else Decimal("0.001"))
        candidates.append((m.start(), "component:" + m.group(1).lower() + ":" + str(size.normalize())))
    if re.match(r"\s+de\s+capacidad\s+de\s+filtrado\b", value[match.end():], re.I):
        return "filtered_water"
    return max(candidates, default=(-1, "unknown"))[1]


def _capacity_limit_role(value: str, match: Any) -> str:
    """Recognize literal fill limits; do not inherit a role past another number."""
    prefix = value[max(0, match.start() - 140):match.start()]
    # A recommended 220-250 ml interval keeps its explicit recommendation.
    prefix = re.sub(r"(?<![\d.,])\d+(?:[.,]\d+)?\s*[-–]\s*$", "", prefix)
    tail = r"\s*(?:es\s*)?(?:de|:)?\s*(?:aprox\.?\s*)?$"
    patterns = (
        (r"\b(?:capacidad|cantidad|nivel(?:\s+de\s+agua)?)\s+m[ií]nim[oa]"
         r"(?:\s+de\s+llenado)?" + tail, "minimum"),
        (r"\b(?:capacidad|cantidad|nivel(?:\s+de\s+agua)?)\s+m[aá]xim[oa]" + tail, "nominal"),
        (r"\bm[aá]x\.\s*capacidad" + tail, "nominal"),
        (r"\bcapacidad(?:\s+de\s+uso)?\s+[uú]til"
         r"(?:\s+hasta\s+la\s+marca\s+de\s+llenado\s+m[aá]ximo)?" + tail, "useful"),
        (r"\b(?:capacidad(?:\s+de\s+uso)?|cantidad\s+de\s+agua|nivel\s+de\s+agua)"
         r"\s+recomendad[oa]" + tail, "recommended"),
        (r"\brecomendad[oa]" + tail, "recommended"),
        (r"\brecomendamos\s+llenar\s+(?:el\s+vaso|la\s+taza)\s+con\s*$", "recommended"),
        (r"\bal\s+llenarlo\s+hasta\s+aproximadamente\s*$", "recommended"),
        (r"\bprepara\s+hasta\s*$", "recommended"),
        (r"\b(?:capacidad|volumen)(?:\s+total)?" + tail, "nominal"),
        (r"\bcapacidad\s+hasta(?:\s+el\s+borde(?:\s+es\s+de)?)?\s*$", "nominal"),
        (r"\b(?:vasos?|tazas?|copas?)\b[^\d\n.;]{0,70}\bde\s*$", "nominal"),
    )
    for pattern, role in patterns:
        if re.search(pattern, prefix, re.I):
            return role
    return "unknown"


def _compatible_capacity_limits(value: str, capacities: list[tuple[Any, Decimal]]) -> bool:
    """Require one upper capacity and consistent, explicitly qualified limits."""
    roles: dict[str, set[Decimal]] = defaultdict(set)
    for match, amount in capacities:
        roles[_capacity_limit_role(value, match)].add(amount)
    # No role can silently choose between competing source assertions.
    if any(len(amounts) > 1 for role, amounts in roles.items() if role != "unknown"):
        return False
    nominal = roles.get("nominal", set())
    if len(nominal) != 1 or not any(roles.get(role) for role in ("minimum", "useful", "recommended")):
        return False
    upper = next(iter(nominal))
    if roles.get("unknown", set()) - {upper}:
        return False
    minimum = next(iter(roles.get("minimum", {Decimal(0)})))
    if minimum > upper:
        return False
    for match, amount in capacities:
        prefix = value[max(0, match.start() - 100):match.start()]
        bare = re.search(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*([-–]|a|hasta|y)\s*$", prefix, re.I)
        if bare:
            # A bare bound inherits the following unit only inside an explicit
            # recommendation or stated interval. Validate both literal bounds.
            stated_range = bool(re.search(r"\b(?:entre|rango|intervalo)\b[^;\n]{0,60}$", prefix, re.I))
            role = _capacity_limit_role(value, match)
            if role not in {"useful", "recommended"} and not stated_range:
                return False
            factor = {"ml": Decimal(1), "cl": Decimal(10), "L": Decimal(1000)}[
                UNIT_CANONICAL[match.group("unit").casefold()]]
            lower = Decimal(bare.group(1).replace(",", ".")) * factor
            if not minimum <= lower <= amount <= upper:
                return False
    return all(minimum <= amount <= upper
               for role in ("useful", "recommended") for amount in roles.get(role, set()))


def _numeric_amount(value: str, match: Any) -> Decimal:
    amount = Decimal(match.group("number").replace(",", "."))
    # The Spanish phrase is an exact half-litre assertion. A following unit
    # ("y medio litro") describes another quantity and must remain ambiguous.
    if (amount == 1 and match.group("unit").casefold() == "litro"
            and re.match(r"\s+y\s+medio\b(?=$|[,.;:)\n]|\s+(?:es|con|de|para|sin)\b)",
                         value[match.end():], re.I)):
        amount += Decimal("0.5")
    return amount


def _numeric_unit_matches(value: str) -> list[Any]:
    matches = {m.start(): m for m in NUMERIC_UNIT_RE.finditer(value)}
    # The regular word boundary deliberately rejects model strings such as
    # AX320ml. Supplement only an explicit integer-count × volume expression.
    at_position = re.compile(NUMERIC_UNIT_RE.pattern.removeprefix(r"(?<!\w)"), NUMERIC_UNIT_RE.flags)
    for prefix in re.finditer(r"(?<![\w.,])\d+(?:[.,]\d+)?\s*[x×]\s*(?=\d)", value, re.I):
        match = at_position.match(value, prefix.end())
        if match and UNIT_CANONICAL.get(match.group('unit').casefold()) in {'ml', 'cl', 'L'}:
            matches.setdefault(match.start(), match)
    return [matches[pos] for pos in sorted(matches)]


def _capacity_conflict(value: str, capacities: list[tuple[Any, Decimal]], *, structured_label: bool) -> bool:
    if _explicit_capacity_total(value, capacities, conflict_only=True):
        return True
    if not structured_label and compatible_reservoir_modes(value, capacities):
        return False
    if not structured_label and compatible_explicit_sets(value, capacities):
        return False
    def explicit_hyphen_range(start: int) -> bool:
        prefix = value[max(0, start - 60):start]
        return bool(re.search(r"\b(?:rango|entre|desde|intervalo)\b[^;\n]{0,35}$|"
                              r"\b(?:medium|small|large|pequeñ[oa]|median[oa]|grande)\s*\(\s*$",
                              prefix, re.I))
    scopes = [(m, amount, "unknown" if structured_label else _capacity_scope(value, m))
              for m, amount in capacities]
    # Accessory compatibility and graduation marks do not assert the vessel's
    # scalar capacity. All numeric/unit evidence remains in the profile.
    scopes = [x for x in scopes if x[2] not in {"accessory_bag", "graduation_scale"}]
    unknown = any(scope == "unknown" for _, _, scope in scopes)
    groups: dict[str, list] = defaultdict(list)
    for match, amount, scope in scopes:
        # An unassigned value might belong to any stated object. Keep the
        # conservative comparison instead of assuming a new separate object.
        groups["unknown" if unknown else scope].append((match, amount))
    for group in groups.values():
        if _explicit_capacity_total(value, group, conflict_only=True):
            return True
        if _explicit_capacity_total(value, group):
            continue
        if not structured_label and _compatible_capacity_limits(value, group):
            continue
        intervals = []
        index = 0
        while index < len(group):
            match, amount = group[index]
            low = high = amount
            if index + 1 < len(group):
                following, next_amount = group[index + 1]
                link = re.fullmatch(r"\s*(-|–|a|hasta)\s*", value[match.end():following.start()], re.I)
                if link and (link.group(1).lower() in {"a", "hasta"} or explicit_hyphen_range(match.start())):
                    low, high = amount, next_amount
                    index += 1
            if low == high:
                prefix_start = max(0, match.start() - 60)
                bare = re.search(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(-|–|a|hasta)\s*$",
                                 value[prefix_start:match.start()], re.I)
                if bare and (bare.group(2).lower() in {"a", "hasta"} or explicit_hyphen_range(prefix_start + bare.start(1))):
                    factor = {"ml": Decimal(1), "cl": Decimal(10), "L": Decimal(1000)}[
                        UNIT_CANONICAL[match.group("unit").casefold()]]
                    low = Decimal(bare.group(1).replace(",", ".")) * factor
            if low > high:
                return True
            intervals.append((float(low), float(high)))
            index += 1
        if intervals:
            lower, upper = max(x[0] for x in intervals), min(x[1] for x in intervals)
            tolerance = max(0.01, max(x[1] for x in intervals) * 0.001)
            if any(low != high for low, high in intervals):
                tolerance = 0.0  # A stated range boundary must not be rounded away.
            if lower - upper > tolerance:
                return True
    return False


def numeric_profile(value: str, *, label: str | None = None, source_title: str = '') -> dict[str, Any]:
    matches = []
    for match in _numeric_unit_matches(value):
        tail = value[match.end():]
        # l. is Amazon's largo axis abbreviation. L/A/A followed by cm is
        # another explicit dimension spelling, not a capacity declaration.
        axis = re.match(r"\.\s*[x×]|\s*[x×]\s*\d+(?:[.,]\d+)?\s*A\s*[x×]\s*"
                        r"\d+(?:[.,]\d+)?\s*A\s*cm\b", tail, re.I)
        if match.group("unit").casefold() == "l" and axis:
            continue
        matches.append(match)
    issues = []
    if re.search(r"\b\d+(?:[.,]\d+)?\s*mAhmAh\b", value, re.I):
        issues.append("DUPLICATE_UNIT")
    # Amazon occasionally concatenates a unit with the following word (for
    # example ``12kgpeso``).  The normal parser intentionally rejects that
    # token, so detect it separately instead of silently dropping the fact.
    glued_token_re = re.compile(
        r"(?<!\w)\d+(?:[.,]\d+)?(?P<token>[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)", re.I)
    unit_prefixes = sorted(
        {name.casefold() for name in UNIT_CANONICAL if len(name) >= 2}
        | {"mah", "mhz", "ghz", "khz", "psi", "bar", "kw"},
        key=len, reverse=True)
    glued_suffixes = {"peso", "capacidad", "volumen", "potencia", "voltaje", "dimensiones"}
    if any(any(token.casefold().startswith(prefix) and
               token.casefold()[len(prefix):] in glued_suffixes
               for prefix in unit_prefixes)
           for token in (match.group("token") for match in glued_token_re.finditer(value))):
        # A glued unit is specifically a unit with no whitespace after the
        # number (``12kgpeso``).  Matching the longest known unit prefix
        # avoids treating ``400ml-Lubrica`` or ``217 Gramos`` as malformed.
        issues.append("GLUED_UNIT")
    # A details blob legitimately contains several unit types (dimensions,
    # weight and voltage). Mixed units alone are not an anomaly; duplicate or
    # malformed unit syntax is actionable without guessing product facts.
    values = []
    rate_positions = set()
    for match in matches:
        number, unit = match.group("number"), match.group("unit")
        try:
            canonical = UNIT_CANONICAL.get(unit.strip().casefold(), unit.strip())
            rate = re.match(r"\s*/\s*(min(?:uto)?s?|s|segundos?|h|horas?)\b", value[match.end():], re.I)
            raw_unit = unit
            if canonical in {"ml", "cl", "L"} and rate:
                rate_positions.add(match.start())
                suffix = rate.group(1).casefold()
                canonical += "/" + ("min" if suffix.startswith("min") else ("h" if suffix.startswith("h") else "s"))
                raw_unit += rate.group(0).strip()
            values.append({"numeric_value": float(_numeric_amount(value, match)), "unit_raw": raw_unit,
                           "canonical_unit": canonical})
        except ValueError:
            issues.append("INVALID_NUMBER")
    # Capacity values can be written in mixed units, but contradictory values
    # in the same field are an ambiguity that must be reviewed.  Equivalent
    # representations (500 ml / 0,5 L) are deliberately accepted.
    capacity_factors = {"ml": 1.0, "cl": 10.0, "L": 1000.0}
    capacity_context = bool(re.search(r"\b(?:capacidad|volumen|contenido|capacity|volume)\b", value, re.I)) if label is None else bool(
        re.search(r"\b(?:capacidad|volumen|contenido|capacity|volume)\b", str(label), re.I))
    if label is None and re.search(r"\btotal\s*:?\s*\d+(?:[.,]\d+)?\s*(?:mililitros?|centilitros?|litros?|ml|cl|l)\b", value, re.I):
        capacity_context = True
    capacities = [(match, _numeric_amount(value, match) *
                   Decimal(str(capacity_factors[UNIT_CANONICAL[match.group("unit").casefold()]])))
                  for match in matches if UNIT_CANONICAL.get(match.group("unit").casefold()) in capacity_factors
                  and match.start() not in rate_positions]
    if capacity_context:
        volume_positions = {m.start() for m, _ in capacities}
        if any((prefix.group('count').startswith('0') or re.search(r'[.,]', prefix.group('count')))
               and prefix.end() in volume_positions
               for prefix in re.finditer(r"(?<![\w.,])(?P<count>\d+(?:[.,]\d+)?)\s*[x×]\s*(?=\d)", value, re.I)):
            issues.append('INVALID_VOLUME_MULTIPLIER')
    contexts = scenario_exclusions(value, capacities) if label is None else []
    excluded = {item['start'] for item in contexts}
    checked_capacities = [(m, amount) for m, amount in capacities if m.start() not in excluded]
    title_binding = title_bound_components(value, capacities, source_title) if label is None else None
    if capacity_context and not title_binding and _capacity_conflict(value, checked_capacities, structured_label=label is not None):
        issues.append("CONTRADICTORY_CAPACITY")
    return {"values": values, "issues": sorted(set(issues)), "capacity_contexts": contexts,
            "capacity_title_binding": title_binding}


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[\wÀ-ÿ]{2,}", normalize_key(value)))


def _raw_hash(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def audit_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    service = DictionaryService()
    field_quality: dict[str, Counter] = {field: Counter() for field in AUDIT_FIELDS}
    language_quality: Counter[tuple[str, str]] = Counter()
    issue_counts: Counter[str] = Counter()
    cleanup_counts: Counter[str] = Counter()
    source_presence: dict[str, Counter[str]] = {field: Counter() for field in AUDIT_FIELDS}
    structure_issues: list[dict[str, Any]] = []
    cross_field_issues: list[dict[str, Any]] = []
    unit_issues: list[dict[str, Any]] = []
    identity_counts: Counter[str] = Counter()
    identity_rows: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    translation_input: list[dict[str, Any]] = []
    sku_quality: list[dict[str, Any]] = []
    asins: Counter[str] = Counter()
    missing_asin = 0
    for record in records:
        asin = str(record.get("asin") or record.get("ASIN") or "").strip().upper()
        if asin:
            asins[asin] += 1
        else:
            missing_asin += 1
        clean_fields: dict[str, dict[str, Any]] = {}
        raw_fields: dict[str, dict[str, Any]] = {}
        detail = parse_details(_source_value(record, "product_details"),
                               source_attributes=record.get("detail_attributes_raw", record.get("attributes")))
        bullets = parse_bullets(_source_value(record, "feature_bullets"))
        record_issues: list[str] = []
        record_identity: list[dict[str, Any]] = []
        for field in AUDIT_FIELDS:
            raw = _source_value(record, field)
            source_values = [record.get(alias) for alias in FIELD_ALIASES[field] if alias in record]
            if not source_values or all(value is None for value in source_values):
                source_presence[field]["null"] += 1
            elif not _text(raw):
                if any(isinstance(value, str) and not value for value in source_values):
                    source_presence[field]["empty"] += 1
                else:
                    source_presence[field]["whitespace_only"] += 1
            elif isinstance(raw, str) and not raw.strip():
                source_presence[field]["whitespace_only"] += 1
            else:
                source_presence[field]["nonempty"] += 1
            raw_fields[field] = {"source_hash": _raw_hash(raw), "source_present": bool(_text(raw).strip())}
            if field == "product_details":
                result = {"source_text": detail["source_text"], "clean_text": "\n".join(
                    f"{row['label']}: {row['value']}" for row in detail["rows"]),
                    "clean_status": detail["status"], "actions": detail.get("actions", []), "issues": detail["issues"],
                    "translate_allowed": detail["status"] in {"CLEAN", "NORMALIZED"},
                    "protected_tokens": sorted(set(PROTECTED_TOKEN_RE.findall(detail["source_text"])), key=str.casefold)}
            elif field == "feature_bullets":
                result = {"source_text": bullets["source_text"], "clean_text": "\n".join(
                    item["clean_text"] for item in bullets["items"]),
                    "clean_status": bullets["status"], "actions": [], "issues": bullets["issues"],
                    "translate_allowed": bullets["status"] in {"CLEAN", "NORMALIZED"},
                    "protected_tokens": sorted(set(PROTECTED_TOKEN_RE.findall(bullets["source_text"])), key=str.casefold)}
            else:
                result = clean_text(raw, field=field)
            if field == "feature_bullets":
                result["actions"] = bullets["actions"]
            if field == "product_description" and (
                    DESCRIPTION_METADATA_RE.search(result.get("clean_text", "")) or
                    DESCRIPTION_ASIN_RE.search(result.get("clean_text", ""))):
                result["issues"].append("POSSIBLE_METADATA_IN_DESCRIPTION")
                result["clean_status"] = "NEEDS_REVIEW"
                result["translate_allowed"] = False
            result["language"] = language_label(result.get("clean_text", ""))
            if field == "product_details" and detail.get("rows"):
                # Evaluate capacities per labeled detail row.  Aggregating a
                # whole table would incorrectly compare unrelated facts such
                # as airflow, package volume and product quantity.
                row_profiles = [numeric_profile(str(row.get("value") or ""), label=str(row.get("label") or ""))
                                for row in detail["rows"]]
                result["numeric"] = {
                    "values": [item for profile in row_profiles for item in profile["values"]],
                    "issues": sorted({issue for profile in row_profiles for issue in profile["issues"]}),
                }
            else:
                result["numeric"] = numeric_profile(result.get("clean_text", ""),
                                                    source_title=_text(_source_value(record, 'title_es_raw')))
            result["issues"] = sorted(set(result.get("issues", []) + result["numeric"].get("issues", [])))
            blocking_issues = [issue for issue in result["issues"] if issue not in NON_BLOCKING_ISSUES]
            if blocking_issues and result["clean_status"] in {"CLEAN", "NORMALIZED"}:
                result["clean_status"] = "NEEDS_REVIEW"
                result["translate_allowed"] = False
            cleanup_counts["removed_ui_artifacts"] += int(result.get("removed_ui_artifacts", 0))
            cleanup_counts["removed_zero_width"] += int(result.get("removed_zero_width", 0))
            cleanup_counts["html_detected"] += int(bool(result.get("html_detected", False)))
            clean_fields[field] = result
            field_quality[field][result["clean_status"]] += 1
            language_quality[(field, result["language"])] += 1
            for issue in result.get("issues", []):
                issue_counts[issue] += 1
                record_issues.append(issue)
                if issue in result.get("numeric", {}).get("issues", []):
                    unit_issues.append({"asin": asin, "field": field, "source_text": result["source_text"], "issue_code": issue})
        for row in detail.get("raw_rows", detail["rows"]):
            label = row["label"]
            if is_identity_attribute(label):
                identity_counts[label] += 1
                identity = {"asin": asin, "label": label, "value": row["value"],
                            "value_type": "identity", "translate_allowed": False,
                            "resolution": "source_preserved"}
                identity_rows.append(identity)
                record_identity.append(identity)
        if detail["issues"]:
            for issue in detail["issues"]:
                structure_issues.append({"asin": asin, "field": "product_details", "issue_code": issue,
                                         "source_text": detail["source_text"]})
        if bullets["issues"]:
            for issue in bullets["issues"]:
                structure_issues.append({"asin": asin, "field": "feature_bullets", "issue_code": issue,
                                         "source_text": bullets["source_text"]})
        values_for_overlap = {field: clean_fields[field].get("clean_text", "") for field in
                              ("title_es_raw", "specification_es", "product_description", "product_details", "feature_bullets")}
        def mark_field_review(field: str, issue: str, *, status: str = "NEEDS_REVIEW") -> None:
            result = clean_fields[field]
            result["issues"] = sorted(set(result.get("issues", [])) | {issue})
            if result["clean_status"] != "BLOCKED":
                result["clean_status"] = status
            result["translate_allowed"] = False

        for left, right in (("title_es_raw", "specification_es"), ("specification_es", "product_description"),
                            ("product_details", "feature_bullets"), ("feature_bullets", "title_es_raw")):
            l_tokens, r_tokens = _tokens(values_for_overlap[left]), _tokens(values_for_overlap[right])
            if l_tokens and r_tokens:
                overlap = len(l_tokens & r_tokens) / max(1, min(len(l_tokens), len(r_tokens)))
                if values_for_overlap[left] == values_for_overlap[right] or overlap >= 0.85:
                    row = {"asin": asin, "field_a": left, "field_b": right,
                           "issue_code": "CROSS_FIELD_OVERLAP", "overlap_ratio": round(overlap, 6),
                           "source_a": values_for_overlap[left], "source_b": values_for_overlap[right]}
                    cross_field_issues.append(row)
                    issue_counts["CROSS_FIELD_OVERLAP"] += 1
                    record_issues.append("CROSS_FIELD_OVERLAP")
                    # Repeated facts across title/spec/details/bullets are
                    # normal on Amazon. Keep the evidence flag for audit, but
                    # do not block either field or enqueue it for review.
                    for field in (left, right):
                        clean_fields[field]["issues"] = sorted(
                            set(clean_fields[field].get("issues", []))
                            | {"CROSS_FIELD_OVERLAP"}
                        )
        brand = clean_fields["brand"]["clean_text"]
        category_values = [clean_fields[field]["clean_text"] for field in ("category_l1", "category_l2", "category_l3", "leaf_category")]
        misplaced = False
        if brand and (len(brand) > 200 or "http" in brand.casefold()):
            misplaced = True
        if any("http" in value.casefold() for value in category_values if value):
            misplaced = True
        if clean_fields["title_es_raw"]["clean_text"].strip().isdigit():
            misplaced = True
        if len(clean_fields["specification_es"]["clean_text"]) > 8000:
            misplaced = True
        if misplaced:
            record_issues.append("POSSIBLE_FIELD_MISPLACEMENT")
            issue_counts["POSSIBLE_FIELD_MISPLACEMENT"] += 1
            if brand and (len(brand) > 200 or "http" in brand.casefold()):
                mark_field_review("brand", "POSSIBLE_FIELD_MISPLACEMENT", status="SUSPICIOUS")
            for field in ("category_l1", "category_l2", "category_l3", "leaf_category"):
                if "http" in clean_fields[field]["clean_text"].casefold():
                    mark_field_review(field, "POSSIBLE_FIELD_MISPLACEMENT", status="SUSPICIOUS")
            if clean_fields["title_es_raw"]["clean_text"].strip().isdigit():
                mark_field_review("title_es_raw", "POSSIBLE_FIELD_MISPLACEMENT", status="SUSPICIOUS")
            if len(clean_fields["specification_es"]["clean_text"]) > 8000:
                mark_field_review("specification_es", "POSSIBLE_FIELD_MISPLACEMENT", status="SUSPICIOUS")
        severe = {"DUPLICATE_UNIT", "MULTIPLE_UNITS", "GLUED_UNIT",
                  "CONTRADICTORY_CAPACITY"}
        status = "NEEDS_REVIEW" if any(issue in severe for issue in record_issues) else (
            "SUSPICIOUS" if "POSSIBLE_FIELD_MISPLACEMENT" in record_issues else (
            "NORMALIZED" if any(result["clean_status"] == "NORMALIZED" for result in clean_fields.values())
            else "CLEAN"))
        if any(result["clean_status"] == "BLOCKED" for result in clean_fields.values()):
            status = "BLOCKED"
        elif any(result["clean_status"] == "NEEDS_REVIEW" for result in clean_fields.values()):
            status = "NEEDS_REVIEW"
        elif any(result["clean_status"] == "SUSPICIOUS" for result in clean_fields.values()):
            status = "SUSPICIOUS"
        if not asin:
            status = "BLOCKED"
        queued_before = len(review_queue)
        if status not in {"CLEAN", "NORMALIZED"}:
            for field, result in clean_fields.items():
                blocking = [issue for issue in result.get("issues", [])
                            if issue not in NON_BLOCKING_ISSUES]
                if result["clean_status"] not in {"CLEAN", "NORMALIZED", "SOURCE_MISSING"} or blocking:
                    review_queue.append({"asin": asin, "field": field, "source_text": result["source_text"],
                                         "clean_text": result["clean_text"], "clean_status": result["clean_status"],
                                         "issue_codes": ";".join(result.get("issues", [])),
                                         "severity": "P1" if result["clean_status"] == "BLOCKED" else "P2",
                                         "suggested_action": "人工复核后再进入翻译"})
            if len(review_queue) == queued_before:
                review_queue.append({"asin": asin, "field": "", "source_text": "", "clean_text": "",
                                     "clean_status": status, "issue_codes": ";".join(sorted(set(record_issues))),
                                     "severity": "P1" if status == "BLOCKED" else "P2",
                                     "suggested_action": "人工复核后再进入翻译"})
        translation_input.append({"asin": asin, "clean_schema_version": CLEAN_SCHEMA_VERSION,
                                  "raw_fields": raw_fields, "fields": clean_fields,
                                  "details_structured": detail["rows"], "bullet_items": bullets["items"],
                                  "detail_parser_status": detail["status"],
                                  "detail_fully_structured": detail["fully_structured"],
                                  "detail_partially_structured": detail["partially_structured"],
                                  "detail_unstructured": detail["unstructured"],
                                  "detail_safe_duplicate_rows": detail["safe_duplicate_rows"],
                                  "detail_conflicting_duplicate_labels": detail["conflicting_duplicate_labels"],
                                  "detail_equivalent_source_groups": detail["equivalent_source_groups"],
                                  "duplicate_bullet_occurrences": bullets["duplicate_bullets"],
                                  "identity": record_identity,
                                  "record_status": status})
        sku_quality.append({"asin": asin, "status": status, "issue_codes": ";".join(sorted(set(record_issues))),
                            "translate_allowed": status in {"CLEAN", "NORMALIZED"}})
    # Cross-field and placement checks can change a field after its local
    # parser ran; report the final admission status, not the pre-cross-check
    # counter.
    field_quality = {
        field: Counter(row["fields"][field]["clean_status"] for row in translation_input)
        for field in AUDIT_FIELDS
    }
    def percentile(values: list[int], ratio: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * ratio))]

    field_profiles: dict[str, dict[str, Any]] = {}
    for field in AUDIT_FIELDS:
        values = [row["fields"][field]["clean_text"] for row in translation_input
                  if row["fields"][field]["clean_text"]]
        lengths = [len(value) for value in values]
        numeric_rows = [row["fields"][field]["numeric"] for row in translation_input]
        field_profiles[field] = {
            "nonempty": len(values),
            "empty": len(translation_input) - len(values),
            "unique": len(set(values)),
            "average_length": round(statistics.mean(lengths), 2) if lengths else 0.0,
            "p95_length": percentile(lengths, 0.95),
            "max_length": max(lengths) if lengths else 0,
            "numeric_recognized": sum(1 for item in numeric_rows if item.get("values")),
            "numeric_anomalies": sum(1 for item in numeric_rows if item.get("issues")),
        }
    detail_summary = {
        "total_skus": len(records),
        "fully_structured": sum(1 for item in translation_input
                                 if item["detail_fully_structured"]),
        "partially_structured": sum(1 for item in translation_input
                                     if item["detail_partially_structured"]),
        "unstructured": sum(1 for item in translation_input
                             if item["detail_unstructured"]),
        "source_missing": sum(1 for item in translation_input
                               if item["detail_parser_status"] == "SOURCE_MISSING"),
        "safe_duplicate_rows": sum(item["detail_safe_duplicate_rows"] for item in translation_input),
        "safe_duplicate_skus": sum(item["detail_safe_duplicate_rows"] > 0 for item in translation_input),
        "conflicting_duplicate_labels": sum(item["detail_conflicting_duplicate_labels"] for item in translation_input),
        "conflicting_duplicate_skus": sum(item["detail_conflicting_duplicate_labels"] > 0 for item in translation_input),
        "equivalent_source_groups": sum(item["detail_equivalent_source_groups"] for item in translation_input),
        "duplicate_labels": sum(item["detail_conflicting_duplicate_labels"] for item in translation_input),
    }
    bullet_summary = {
        "total_bullets": sum(len(item["bullet_items"]) for item in translation_input),
        "duplicate_bullets": sum(item["duplicate_bullet_occurrences"] for item in translation_input),
        "duplicate_bullet_skus": sum(item["duplicate_bullet_occurrences"] > 0 for item in translation_input),
        "long_bullets": sum(1 for issue in structure_issues if issue["issue_code"] == "LONG_BULLET"),
    }
    allowed_fields = sum(1 for item in translation_input for field in AUDIT_FIELDS
                         if item["fields"][field]["translate_allowed"])
    source_missing_fields = sum(1 for item in translation_input for field in AUDIT_FIELDS
                                if item["fields"][field]["clean_status"] == "SOURCE_MISSING")
    review_fields = sum(1 for item in translation_input for field in AUDIT_FIELDS
                        if item["fields"][field]["clean_status"] in {"SUSPICIOUS", "NEEDS_REVIEW", "BLOCKED"})
    protected_token_count = sum(len(item["fields"][field].get("protected_tokens", []))
                                for item in translation_input for field in AUDIT_FIELDS)
    workload_total = workload_deterministic = workload_identity = workload_dictionary = 0
    for item in translation_input:
        for field in AUDIT_FIELDS:
            envelope = item["fields"][field]
            if not envelope.get("translate_allowed"):
                continue
            text = envelope.get("clean_text", "")
            if not text:
                continue
            if field == "product_details":
                for detail_row in item["details_structured"]:
                    value = detail_row["value"]
                    if is_identity_attribute(detail_row["label"]):
                        workload_identity += 1
                        continue
                    workload_total += 1
                    if resolve_exact(service, value, kind="value", field=normalize_key(detail_row["label"]))["status"] == "resolved":
                        workload_dictionary += 1
            elif field == "feature_bullets":
                workload_total += len(item["bullet_items"])
            elif field == "brand":
                workload_identity += 1
            else:
                workload_total += 1
                if field == "specification_es" and specification_is_deterministic(text):
                    workload_deterministic += 1
                elif field.startswith("category_") or field == "leaf_category":
                    if resolve_exact(service, text, kind="category")["status"] == "resolved":
                        workload_dictionary += 1
                elif resolve_exact(service, text, kind="value", field=normalize_key(field))["status"] == "resolved":
                    workload_dictionary += 1
    translation_workload = {
        "potential_units": workload_total + workload_identity,
        "translation_units_total": workload_total + workload_identity,
        "deterministic_resolved": workload_deterministic,
        "identity_preserved": workload_identity,
        "dictionary_resolved": workload_dictionary,
        "remaining_for_qwen": max(0, workload_total - workload_deterministic - workload_dictionary),
        "review_blocked": review_fields,
        "source_missing": source_missing_fields,
        "remaining_for_qwen_estimate": max(0, workload_total - workload_deterministic - workload_dictionary),
        "qwen_api_calls": 0,
        "stage": "offline_preclean_estimate",
    }
    summary = {
        "input_rows": len(records), "unique_asins": len(asins),
        "final_state": "READY_FOR_TRANSLATION_V2_REMOTE_REVIEW",
        "duplicate_asins": sum(max(0, count - 1) for count in asins.values()),
        "missing_asin": missing_asin, "clean_schema_version": CLEAN_SCHEMA_VERSION,
        "total_fields": len(records) * len(AUDIT_FIELDS),
        "field_quality": {field: dict(counts) for field, counts in field_quality.items()},
        "field_profiles": field_profiles,
        "source_presence": {field: dict(counts) for field, counts in source_presence.items()},
        "issue_counts": dict(issue_counts),
        "status_counts": dict(Counter(row["status"] for row in sku_quality)),
        "structure": detail_summary,
        "bullets": bullet_summary,
        "identity_count": len(identity_rows), "cross_field_issue_count": len(cross_field_issues),
        "review_queue_count": len(review_queue),
        "protected_token_count": protected_token_count,
        "translation_readiness": {"total_fields": len(records) * len(AUDIT_FIELDS),
                                   "translate_allowed_fields": allowed_fields,
                                   "review_fields": review_fields,
                                   "source_missing_fields": source_missing_fields,
                                   "dictionary_stage": "not_run"},
        "translation_workload": translation_workload,
        "cleanup": dict(cleanup_counts),
        "language_profile": [{"field": field, "language": language, "count": count}
                             for (field, language), count in sorted(language_quality.items())],
    }
    return {"summary": summary, "translation_input_records": translation_input,
            "field_quality": field_quality, "sku_quality": sku_quality,
            "cross_field_issues": cross_field_issues, "structure_issues": structure_issues,
            "language_profile": summary["language_profile"], "identity_rows": identity_rows,
            "unit_issues": unit_issues, "review_queue": review_queue}


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_reports(result: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = result["summary"]
    paths: dict[str, str] = {}
    def write_json(name: str, data: Any) -> None:
        path = out / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[name] = str(path)
    write_json("translation_input_records.json", {"clean_schema_version": CLEAN_SCHEMA_VERSION,
                                                  "records": result["translation_input_records"]})
    write_json("preclean_audit.json", summary)
    statuses = ["CLEAN", "NORMALIZED", "SOURCE_MISSING", "SUSPICIOUS", "NEEDS_REVIEW", "BLOCKED"]
    quality_rows = [{"field": field, **{status: counts.get(status, 0) for status in statuses}
                     } for field, counts in summary["field_quality"].items()]
    _write_csv(out / "field_quality.csv", quality_rows, ["field", *statuses])
    _write_csv(out / "sku_quality.csv", result["sku_quality"], ["asin", "status", "issue_codes", "translate_allowed"])
    _write_csv(out / "cross_field_issues.csv", result["cross_field_issues"], ["asin", "field_a", "field_b", "issue_code", "overlap_ratio", "source_a", "source_b"])
    _write_csv(out / "structure_issues.csv", result["structure_issues"], ["asin", "field", "issue_code", "source_text"])
    _write_csv(out / "language_profile.csv", result["language_profile"], ["field", "language", "count"])
    identity_summary = []
    for label, count in Counter(row["label"] for row in result["identity_rows"]).most_common():
        identity_summary.append({"label": label, "occurrences": count, "source_preserved": count,
                                 "translate_allowed": False})
    _write_csv(out / "identity_summary.csv", identity_summary, ["label", "occurrences", "source_preserved", "translate_allowed"])
    _write_csv(out / "unit_numeric_issues.csv", result["unit_issues"], ["asin", "field", "source_text", "issue_code"])
    _write_csv(out / "review_queue.csv", result["review_queue"], ["asin", "field", "source_text", "clean_text", "clean_status", "issue_codes", "severity", "suggested_action"])
    for name in ("field_quality.csv", "sku_quality.csv", "cross_field_issues.csv", "structure_issues.csv", "language_profile.csv", "identity_summary.csv", "unit_numeric_issues.csv", "review_queue.csv"):
        paths[name] = str(out / name)
    md = ["# Translation V2 Pre-Clean Audit", "", f"- Input rows: {summary['input_rows']}",
          f"- Unique ASIN: {summary['unique_asins']}", f"- Duplicate ASIN rows: {summary['duplicate_asins']}",
          f"- Missing ASIN rows: {summary['missing_asin']}", f"- Total fields: {summary['total_fields']}",
          f"- Protected technical tokens: {summary['protected_token_count']}",
          f"- Schema: {CLEAN_SCHEMA_VERSION}", "",
          "## SKU status", ""]
    for name, count in summary["status_counts"].items():
        md.append(f"- {name}: {count}")
    md += ["", "## Field quality", "", "| Field | CLEAN | NORMALIZED | SOURCE_MISSING | SUSPICIOUS | NEEDS_REVIEW | BLOCKED |", "|---|---:|---:|---:|---:|---:|---:|"]
    for field, counts in summary["field_quality"].items():
        md.append("| %s | %d | %d | %d | %d | %d | %d |" % (field, counts.get("CLEAN", 0), counts.get("NORMALIZED", 0), counts.get("SOURCE_MISSING", 0), counts.get("SUSPICIOUS", 0), counts.get("NEEDS_REVIEW", 0), counts.get("BLOCKED", 0)))
    md += ["", "## Main issue counts", ""]
    for issue, count in sorted(summary["issue_counts"].items(), key=lambda row: (-row[1], row[0])):
        md.append(f"- {issue}: {count}")
    md += ["", "## Structure", "", json.dumps(summary["structure"], ensure_ascii=False, indent=2),
           "", "## Translation readiness", "", json.dumps(summary["translation_readiness"], ensure_ascii=False, indent=2),
           "", "## Offline translation workload estimate", "", json.dumps(summary["translation_workload"], ensure_ascii=False, indent=2),
           "", "## Field profiles", "", json.dumps(summary["field_profiles"], ensure_ascii=False, indent=2),
           "", "## Identity", "", f"- Identity values preserved: {summary['identity_count']}",
           "", "## Translation gate", "", "Only CLEAN and NORMALIZED records/fields have translate_allowed=true.",
           "SUSPICIOUS, NEEDS_REVIEW and BLOCKED remain in review_queue.csv.", ""]
    (out / "preclean_audit.md").write_text("\n".join(md), encoding="utf-8")
    paths["preclean_audit.md"] = str(out / "preclean_audit.md")
    paths["preclean_audit.json"] = str(out / "preclean_audit.json")
    paths["translation_input_records.json"] = str(out / "translation_input_records.json")
    return paths


def run_preclean(products: str | Path) -> dict[str, Any]:
    return audit_records(load_records(products))
