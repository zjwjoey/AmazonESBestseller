"""Small, deterministic field QA validators."""
from __future__ import annotations

import re
import json
from typing import Any, Dict, Iterable, List

from .protection import ProtectedText, restore

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(ml|cl|dl|l|mg|g|kg|mm|cm|m|w|kw|v|a|hz|ghz|mah|bar|psi|°c|%|"
    r"毫升|厘?米|毫米|千克|公斤|克|瓦|千瓦|伏|安|赫兹|升|毫安时|摄氏度|磅)", re.I)
UNIT_ALIASES = {
    "毫升": "ml", "厘米": "cm", "毫米": "mm", "米": "m", "千克": "kg",
    "公斤": "kg", "克": "g", "瓦": "w", "千瓦": "kw", "伏": "v",
    "安": "a", "赫兹": "hz", "升": "l", "毫安时": "mah", "摄氏度": "°c",
    "磅": "lb",
}


def _units(text: str) -> List[tuple[str, str]]:
    return [(number.replace(",", "."), UNIT_ALIASES.get(unit.casefold(), unit.casefold()))
            for number, unit in UNIT_RE.findall(str(text or ""))]


def validate_translation(protected: ProtectedText, translated: str,
                         source: str, *, field: str = "",
                         brand: str = "",
                         allowed_residual: Iterable[str] = ()) -> List[Dict[str, Any]]:
    restored, issues = restore(protected, translated)
    source_numbers = NUMBER_RE.findall(str(source or ""))
    result_numbers = NUMBER_RE.findall(restored)
    if sorted(source_numbers) != sorted(result_numbers):
        source_counter = {n: source_numbers.count(n) for n in set(source_numbers)}
        result_counter = {n: result_numbers.count(n) for n in set(result_numbers)}
        added = []
        for number, count in result_counter.items():
            if count > source_counter.get(number, 0):
                added.extend([number] * (count - source_counter.get(number, 0)))
        if added:
            issues.append({"code": "ADDED_NUMBER", "numbers": added})
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
