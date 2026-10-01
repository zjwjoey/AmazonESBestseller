"""Small, deterministic field QA validators."""
from __future__ import annotations

import re
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List

from .protection import ProtectedText, restore
from .zh import dedupe_technical_units

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Keep long/full Spanish unit names before short abbreviations and require a
# word boundary after the unit.  Without this, ``5200 mAh`` matched the
# one-letter ``m`` alternative, and ``4,25 Kilogramos`` was not recognized at
# all.  The latter made a valid decimal-comma conversion fail QA.
_UNIT_NAME_RE = (
    r"mililitros?|centilitros?|decilitros?|litros?|mili?gramos?|"
    r"kilogramos?|centímetros?|centimetros?|milímetros?|milimetros?|"
    r"metros?|kilómetros?|kilometros?|kilovatios?|vatios?|watios?|"
    r"voltios?|amperios?|hercios?|hertzios?|megahercios?|gigahercios?|"
    r"miliamperios?\s*hora|miliamperios?hora|mAh|Ah|ml|cl|dl|l|mg|g|kg|"
    r"mm|cm|m|w|kw|v|a|hz|mhz|ghz|bar|psi|°c|%|"
    r"毫升|厘?米|毫米|千克|公斤|克|瓦|千瓦|伏|安|赫兹|升|毫安时|摄氏度|磅"
)
UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + _UNIT_NAME_RE + r")(?!\w)", re.I)
UNIT_ALIASES = {
    "毫升": "ml", "mililitro": "ml", "mililitros": "ml",
    "厘米": "cm", "centimetro": "cm", "centimetros": "cm",
    "centímetro": "cm", "centímetros": "cm", "毫米": "mm",
    "milimetro": "mm", "milimetros": "mm", "milímetro": "mm",
    "milímetros": "mm", "米": "m", "metro": "m", "metros": "m",
    "千克": "kg", "kilogramo": "kg", "kilogramos": "kg", "公斤": "kg",
    "克": "g", "gramo": "g", "gramos": "g", "毫克": "mg",
    "miligramo": "mg", "miligramos": "mg", "瓦": "w", "vatio": "w",
    "vatios": "w", "watios": "w", "千瓦": "kw", "kilovatio": "kw",
    "kilovatios": "kw", "伏": "v", "voltio": "v", "voltios": "v",
    "安": "a", "amperio": "a", "amperios": "a", "赫兹": "hz",
    "hercio": "hz", "hercios": "hz", "hertzio": "hz", "hertzios": "hz",
    "升": "l", "litro": "l", "litros": "l", "毫安时": "mah",
    "miliamperio hora": "mah", "miliamperios hora": "mah",
    "miliamperiohora": "mah", "miliamperioshora": "mah", "摄氏度": "°c",
    "磅": "lb", "mhz": "mhz", "megahercio": "mhz", "megahercios": "mhz",
    "ghz": "ghz", "gigahercio": "ghz", "gigahercios": "ghz",
}


def _units(text: str) -> List[tuple[str, str]]:
    return [(number.replace(",", "."), UNIT_ALIASES.get(unit.casefold(), unit.casefold()))
            for number, unit in UNIT_RE.findall(str(text or ""))]


def _number_key(value: str):
    """Return a numeric-equivalence key while retaining malformed tokens.

    Amazon.es commonly uses a comma decimal separator.  QA compares numeric
    meaning, not punctuation, so ``4,25`` and ``4.25`` must be equal.
    """
    raw = str(value or "").replace(",", ".")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return raw


def _normalized_numbers(values: Iterable[str]) -> List[Any]:
    return [_number_key(value) for value in values]


def validate_translation(protected: ProtectedText, translated: str,
                         source: str, *, field: str = "",
                         brand: str = "",
                         allowed_residual: Iterable[str] = ()) -> List[Dict[str, Any]]:
    restored, issues = restore(protected, translated)
    # Validate the same narrow deterministic cleanup that the display layer
    # applies.  A protected ``5200 mAh`` may be echoed as ``__T0000__mAh``;
    # restoration then temporarily yields ``5200 mAhmAh`` even though the
    # final display value is normalized back to ``5200 mAh``.
    restored = dedupe_technical_units(restored)
    source_numbers = NUMBER_RE.findall(str(source or ""))
    result_numbers = NUMBER_RE.findall(restored)
    source_number_keys = _normalized_numbers(source_numbers)
    result_number_keys = _normalized_numbers(result_numbers)
    if Counter(source_number_keys) != Counter(result_number_keys):
        source_counter = Counter(source_number_keys)
        result_counter = Counter(result_number_keys)
        added = []
        for number, count in result_counter.items():
            if count > source_counter.get(number, 0):
                added.extend([number] * (count - source_counter.get(number, 0)))
        if added:
            issues.append({"code": "ADDED_NUMBER", "numbers": [str(x) for x in added]})
        issues.append({"code": "NUMERIC_MISMATCH", "source": source_numbers, "result": result_numbers})
    source_units = _units(source)
    result_units = _units(restored)
    if sorted(source_units) != sorted(result_units):
        issues.append({"code": "UNIT_MISMATCH", "source": source_units, "result": result_units})
    if field in {"feature_bullets_es", "feature_bullets_raw"}:
        def bullet_count(value: str) -> int:
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return len([x for x in parsed if str(x).strip()])
            except (TypeError, ValueError):
                pass
            return len([line for line in str(value or "").splitlines() if line.strip()])
        if bullet_count(source) != bullet_count(restored):
            issues.append({"code": "BULLET_COUNT_MISMATCH",
                           "source_count": bullet_count(source),
                           "result_count": bullet_count(restored)})
    if field in {"brand", "brand_es"} and brand and restored != str(brand):
        issues.append({"code": "BRAND_ABNORMAL", "source": str(brand), "result": restored})
    # Spanish residual QA is intentionally conservative; technical tokens and
    # explicitly allowed brand/category terms are not false positives.
    words = re.findall(r"\b[a-záéíóúñü]{4,}\b", restored.lower())
    spanish = {"para", "con", "sin", "producto", "negro", "blanco", "rojo", "azul", "tamaño", "incluye", "material"}
    allowed = {str(x).lower() for x in allowed_residual}
    residual = sorted({w for w in words if w in spanish and w not in allowed})
    if residual:
        issues.append({"code": "SPANISH_RESIDUAL", "tokens": residual})
    return issues
