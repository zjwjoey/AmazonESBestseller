"""Small explicit measure evidence helpers; not a product-fact inference engine."""

from decimal import Decimal
import re
import unicodedata


def semantic(value):
    return (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode()
        .casefold()
    )


def _measure_text(raw):
    """Normalize accents with an original-character map, not lossy offsets.

    Symbols remain separators. NFKD can expand a character (e.g. ™ → TM),
    so normalized regex offsets must never be used directly on raw evidence.
    """
    chars, offsets = [], []
    for index, char in enumerate(raw):
        normalized = "".join(
            c
            for c in unicodedata.normalize("NFKD", char)
            if not unicodedata.combining(c)
        ).casefold()
        chars.extend(normalized)
        offsets.extend([index] * len(normalized))
    return "".join(chars), offsets


NUMBER = r"(?<![\w.,])(?:0,\s+\d+|\d+(?:[.,]\d+)?)"
RATE = re.compile(
    NUMBER + r"\s*(?P<unit>liters?_per_day|litros?\s+por\s+dias?|l\s*/\s*(?:day|dia|d)|"
    r"metros?\s+cubicos?\s+por\s+(?:horas?|minutos?)|m3\s*/\s*(?:h|min)|"
    r"centimetros?\s+cubicos?\s+por\s+segundos?|cm3\s*/\s*s|"
    r"litros?\s+por\s+(?:horas?|minutos?|segundos?)|l\s*/\s*(?:h|min|s))(?![\w/])",
    re.I,
)
AMOUNT = re.compile(
    NUMBER
    + r"\s*(?P<unit>centimetros?\s+cubicos?|mil+ilitros?|litros?|kilogramos?|gramos?|cm3|cc|ml|kg|g|l)(?!\w|/\s*[a-z])",
    re.I,
)
_AXIS_DIMENSION = re.compile(
    NUMBER + r"\s*(?:l|a|an|al|h|d)\.?\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:l|a|an|al|h|d)\.?"
    r"(?:\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:l|a|an|al|h|d)\.?)?\s*"
    r"(?:mm|cm|m|milimetros?|centimetros?|metros?)(?!\w)",
    re.I,
)
COUNT = re.compile(
    NUMBER + r"\s*(?:count|conteo|pcs|piezas?|unidad(?:es)?|uds)(?!\w)", re.I
)


def named_dimension_unit(value):
    """Return a unit only for a complete explicit named-axis dimension."""
    text, _ = _measure_text(str(value or "").strip())
    if not _AXIS_DIMENSION.fullmatch(text):
        return None
    unit = re.search(r"(mm|cm|m|milimetros?|centimetros?|metros?)$", text).group()
    return (
        "mm"
        if unit.startswith(("mm", "milimetro"))
        else "cm"
        if unit.startswith(("cm", "centimetro"))
        else "m"
    )


_SHARED_UNIT_OPTIONS = re.compile(
    r"(?<![\w.,])\d+(?:\.\d+)?"
    r"(?:(?:\s*,\s*\d+(?:\.\d+)?)+\s+(?:y|o)\s+\d+(?:\.\d+)?|"
    r"(?:\s*,\s*\d+(?:\.\d+)?){2,})\s*"
    r"(?:mil+ilitros?|litros?|kilogramos?|gramos?|ml|kg|g|l)(?!\w)",
    re.I,
)


def amount_number(match):
    return Decimal(
        re.sub(
            r"\s", "", re.match(r"(?:0,\s+\d+|\d+(?:[.,]\d+)?)", match.group(0)).group()
        ).replace(",", ".")
    )


def rate_unit(text):
    unit = semantic(text)
    if "day" in unit or "dia" in unit or re.search(r"/\s*d$", unit):
        return "l/day"
    if unit.startswith(("metro", "m3")):
        return "m3/min" if "min" in unit else "m3/h"
    if unit.startswith(("centimetro", "cm3")):
        return "cm3/s"
    if "min" in unit:
        return "l/min"
    return "l/h" if "hora" in unit or re.search(r"/\s*h$", unit) else "l/s"


def measurements(value):
    """Return amount + unit + source fragment, preserving rate magnitudes."""
    raw = str(value or "")
    text, offsets = _measure_text(raw)
    result, spans = (
        [],
        [
            m.span()
            for pattern in (_AXIS_DIMENSION, _SHARED_UNIT_OPTIONS)
            for m in pattern.finditer(text)
        ],
    )
    for match in RATE.finditer(text):
        fragment = raw[offsets[match.start()] : offsets[match.end() - 1] + 1]
        result.append((amount_number(match), rate_unit(match.group("unit")), fragment))
        spans.append(match.span())
    for match in AMOUNT.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        unit, amount = match.group("unit"), amount_number(match)
        if unit.startswith(("mili", "milli", "centimetro")) or unit in {
            "ml",
            "cc",
            "cm3",
        }:
            unit = "ml"
        elif unit.startswith("litro") or unit == "l":
            unit, amount = "ml", amount * 1000
        elif unit.startswith("kilogramo") or unit == "kg":
            unit, amount = "g", amount * 1000
        else:
            unit = "g"
        fragment = raw[offsets[match.start()] : offsets[match.end() - 1] + 1]
        result.append((amount, unit, fragment))
    return result


def signatures(value):
    return {
        format(amount.normalize(), "f") + unit
        for amount, unit, _ in measurements(value)
    }


def count_signatures(value):
    return {
        format(amount_number(m).normalize(), "f") + "pcs"
        for m in COUNT.finditer(semantic(value))
    }


def explicit_capacity_measures(text, *, variant=False, require_label=False):
    """Volume/rate options, or expressly labelled mass capacity; no title weight guessing."""
    raw = str(text or "")
    if require_label:
        # Bullet prose may describe output per day, accessories, or multiple
        # compartments. Compare only an expressly labelled capacity amount.
        result = []
        for match in re.finditer(
            r"\bcapacidad\s*(?:(?:de|del|para)\s*)?[:=]?\s*"
            r"(\d+(?:[.,]\d+)?\s*(?:mil+ilitros?|litros?|kilogramos?|gramos?|ml|kg|g|l))(?!\w)",
            raw,
            re.I,
        ):
            result.extend(measurements(match.group(1)))
        return result
    result = [(n, u, fragment) for n, u, fragment in measurements(raw) if u != "g"]
    if not variant:
        for match in re.finditer(
            r"\bcapacidad\s*(?:(?:de|del)\s*)?[:=]?\s*(\d+(?:[.,]\d+)?\s*(?:kilogramos?|gramos?|kg|g))\b",
            raw,
            re.I,
        ):
            result.extend(measurements(match.group(1)))
    return result


def preferred_capacity(variant, title):
    """One explicit measure per semantic family; mixed/ranged options fail closed."""
    preferred = {}
    for field, text in [("selected_variation_raw", variant), ("title_es_raw", title)]:
        measures = explicit_capacity_measures(
            text, variant=field == "selected_variation_raw"
        )
        for unit in {u for _, u, _ in measures}:
            values = [(n, fragment) for n, u, fragment in measures if u == unit]
            if unit not in preferred and len({n for n, _ in values}) == 1:
                amount, fragment = values[0]
                preferred[unit] = {
                    "amount": amount,
                    "value": fragment,
                    "source_field": field,
                }
    return preferred
