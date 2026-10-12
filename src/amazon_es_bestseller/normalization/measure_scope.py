"""Exact quantity-scope corroboration, never replacement of Amazon facts.

Only explicit multiplication, per-item clauses and corroborated Spanish grouping
are admissible. Different rounded capacities and unexplained rates stay open.
"""

import re

from .measure_evidence import COUNT, amount_number, count_signatures, measurements, semantic

_MEASURE = (
    r"\d+(?:[.,]\d+)?\s*(?:mil+ilitros?|litros?|kilogramos?|gramos?|ml|kg|g|l)(?!\w)"
)
_MULTIPLICATION = re.compile(
    r"(?<![\w.,])(?P<count>\d+)\s*(?:[x×]|compartimentos?\s+de|"
    r"(?:sticks?|sobres?|latas?|botellas?)\s*[x×])\s*(?P<part>"
    + _MEASURE
    + ")",
    re.I,
)


def explicit_totals(text):
    """Return (total, unit, part, count, literal), without inferring a count."""
    result = []
    for m in _MULTIPLICATION.finditer(str(text or "")):
        count = int(m["count"])
        parts = measurements(m["part"])
        if 1 <= count <= 10000 and len(parts) == 1:
            part, unit, _ = parts[0]
            result.append((part * count, unit, part, count, m.group()))
    return result


_PACK_ITEMS = re.compile(
    r"\b(?:pack|paquete|lote)\s+(?:de\s+)?(?P<count>\d+)\s+"
    r"(?:latas?|botellas?|bolsas?|botes?|sobres?|tubos?|envases?|unidades?)\s+de\s+"
    r"(?P<part>" + _MEASURE + ")", re.I,
)
_SELECTED_PACK = re.compile(
    r"(?P<part>" + _MEASURE + r")\s*\(\s*paquete\s+de\s+(?P<count>\d+)\s*\)", re.I,
)
_PACK_COUNT = re.compile(r"\b(?:pack|paquete|lote|juego|set)\s+de\s+(\d+)\b", re.I)
_ITEM_MULTIPLIER = re.compile(r"(?<![\w.,])(?P<count>\d+)\s*[x×]\s*(?P<item>[a-z]{3,})\b", re.I)
_NAMED_PACK_COUNT = re.compile(
    r"(?:(?:pack|paquete|lote|juego|set|caja)\s+(?:de\s+)?(?P<prefix>\d+)\s+"
    r"(?:unidad(?:es)?|piezas?(?:\s+de\s+repuesto)?)|(?P<suffix>\d+)\s*pack"
    r"(?:\s*-\s*(?:small|medium|large))?)", re.I,
)
_NUMBER_PACK = re.compile(r"(?<![\w.,])(\d+)\s*pack\b", re.I)
_COUNTED_CONTENT = re.compile(
    r"(?<![\w.,])(\d+)\s+(?:rollos?|rotuladores?|hojas?|cuchillas?|"
    r"recambios?|bolsas?|tarros?|botes?|macetas?|capsules?|capsulas?)\b", re.I,
)
_INNER_OUTER_COUNT = re.compile(
    r"(?<![\w.,])(\d+)\s*(?:unidad(?:es)?|piezas?)\s*"
    r"\(\s*(?:pack|paquete)\s+de\s+(\d+)\s*\)", re.I,
)


def _package_context_counts(raw):
    """Keep inner item count and outer pack count distinct until explicit proof."""
    normalized = semantic(raw)
    counts = count_signatures(raw)
    packs = {m.group(1) + "pcs" for m in _PACK_COUNT.finditer(normalized)}
    packs |= {m.group(1) + "pcs" for m in _NUMBER_PACK.finditer(normalized)}
    if counts and packs:
        # "1 unidad (Paquete de 12)" and "16 unidad (Paquete de 1)"
        # explicitly give a total. Multiple alternative counts remain open.
        nested = list(_INNER_OUTER_COUNT.finditer(normalized))
        if len(counts) == len(packs) == len(nested) == 1:
            count, pack = map(int, nested[0].groups())
            if counts == {str(count) + "pcs"} and packs == {str(pack) + "pcs"}:
                return {str(count * pack) + "pcs"}
        return counts | packs
    if counts or packs:
        return counts or packs
    totals = explicit_totals(raw)
    if totals:
        return {str(count) + "pcs" for _, _, _, count, _ in totals}
    counts = {
        m.group(1) + "pcs" for m in _COUNTED_CONTENT.finditer(normalized)
        if not re.search(r"\b(?:para|hasta|compatible\s+con|capacidad\s+de)\s*$", normalized[:m.start()])
    }
    for match in _ITEM_MULTIPLIER.finditer(str(raw).casefold()):
        if match["item"] not in {"milimetros", "centimetros", "metros", "gramos", "kilogramos", "litros", "mm", "cm", "ml", "kg"}:
            counts.add(match["count"] + "pcs")
    return counts


def package_quantity_proof(row, value):
    """Prove an exact count/pack total, never infer units or replace source facts.

    Conflicting selected/title packs, alternative totals and unrelated numbers
    fail closed. In particular a bare kg capacity is not a package quantity.
    """
    contexts = [(f, str(row.get(f) or "")) for f in ("title_es_raw", "selected_variation_raw")]
    target_count = COUNT.fullmatch(semantic(value).strip())
    named_count = _NAMED_PACK_COUNT.fullmatch(semantic(value).strip())
    if target_count or named_count:
        target = (format(amount_number(target_count).normalize(), "f") if target_count
                  else str(int(named_count["prefix"] or named_count["suffix"]))) + "pcs"
        proofs = []
        for field, raw in contexts:
            counts = _package_context_counts(raw)
            if counts:
                if counts != {target}:
                    return None
                proofs.append({"source_field": field, "literal": raw, "signature": target, "kind": "explicit_pack_count"})
        return proofs[0] if proofs else None
    target_measures = {(n, u) for n, u, _ in measurements(value)}
    if len(target_measures) != 1 or not any(u in {"g", "ml"} for _, u in target_measures):
        return None
    target = next(iter(target_measures))
    proofs = []
    for field, raw in contexts:
        totals = explicit_totals(raw)
        for pattern in (_PACK_ITEMS, _SELECTED_PACK):
            for match in pattern.finditer(raw):
                parts = measurements(match["part"])
                count = int(match["count"])
                if len(parts) == 1 and 1 <= count <= 10000:
                    part, unit, _ = parts[0]
                    totals.append((part * count, unit, part, count, match.group()))
        if not totals:
            continue
        if {(n, u) for n, u, *_ in totals} != {target}:
            return None
        total, unit, part, count, literal = totals[0]
        proofs.append({"source_field": field, "literal": literal, "part": str(part),
                       "count": count, "total": str(total), "unit": unit,
                       "signature": format(total.normalize(), "f") + unit, "kind": "explicit_pack_total"})
    return proofs[0] if proofs else None


def corroborate_comparison(row, amount, unit, reference_field, reference):
    """Explain a comparison's scope using literal same-record evidence only."""
    for total, family, part, count, literal in explicit_totals(reference):
        if family == unit and total == amount:
            return {
                "kind": "explicit_pack_total",
                "literal": literal,
                "part": str(part),
                "count": count,
                "total": str(total),
                "unit": unit,
            }
    ref_text = semantic(reference)
    if reference_field.startswith("feature_bullets_raw"):
        if re.search(r"\bcada\s+(?:cubo|bolsa|recipiente|compartimento)\b", ref_text):
            ref_values = {n for n, u, _ in measurements(reference) if u == unit}
            for field in ("selected_variation_raw", "title_es_raw"):
                for total, family, part, count, literal in explicit_totals(
                    row.get(field)
                ):
                    if family == unit and amount == total and ref_values == {part}:
                        return {
                            "kind": "explicit_each_vs_total",
                            "source_field": field,
                            "literal": literal,
                            "part": str(part),
                            "count": count,
                            "total": str(total),
                            "unit": unit,
                        }
        # A compatibility list is not one tank's capacity. Require the actual
        # source quantity to be explicitly among the listed options.
        if "compatible" in ref_text and re.search(
            r"\b(?:garrafas?|botellas?)\b", ref_text
        ):
            options = {n for n, u, _ in measurements(reference) if u == unit}
            if len(options) > 1 and amount in options:
                return {
                    "kind": "explicit_compatibility_option",
                    "unit": unit,
                    "options": sorted(str(n) for n in options),
                }
        # Do not globally reinterpret 1.200 as 1200. Require an ungrouped
        # title/selected quantity corroborating that interpretation exactly.
        for m in re.finditer(
            r"(?<![\w.,])\d{1,3}(?:\.\d{3})+\s*(?:litros?|ml|l)(?!\w)", reference, re.I
        ):
            grouped = m.group()
            plain = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", grouped)
            values = {(n, u) for n, u, _ in measurements(plain)}
            corroborated = [
                field
                for field in ("title_es_raw", "selected_variation_raw")
                if (amount, unit)
                in {(n, u) for n, u, _ in measurements(row.get(field))}
            ]
            if (amount, unit) in values and corroborated:
                return {
                    "kind": "corroborated_spanish_thousands",
                    "literal": grouped,
                    "source_field": corroborated[0],
                    "amount": str(amount),
                    "unit": unit,
                }
    return None
