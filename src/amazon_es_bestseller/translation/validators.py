"""Small, deterministic field QA validators."""
from __future__ import annotations

import re
import json
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List

from .protection import ProtectedText, restore
from .zh import dedupe_technical_units

VALIDATOR_RULE_VERSION = "canary40-offline-qa-20261009-v1"

# Do not count digits embedded in model/technical identifiers (``V16``,
# ``BAL-V16-GEO-1``). Those are protected tokens and are checked separately.
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?"
    r"(?![A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ])(?!(?:[.,]\d))"
)
# Keep long/full Spanish unit names before short abbreviations and require a
# word boundary after the unit.  Without this, ``5200 mAh`` matched the
# one-letter ``m`` alternative, and ``4,25 Kilogramos`` was not recognized at
# all.  The latter made a valid decimal-comma conversion fail QA.
_UNIT_NAME_RE = (
    r"millilitros?|mililitros?|centilitros?|decilitros?|litros?|liters?|gramos?|grs?|gm|mili?gramos?|"
    r"kilogramos?|centímetros?|centimetros?|milímetros?|milimetros?|"
    r"metros?|kilómetros?|kilometros?|kilovatios?|vatios?|watios?|"
    r"voltios?|amperios?|hercios?|hertzios?|megahercios?|gigahercios?|"
    r"grados?\s+celsius|grados?\s+cent[ií]grados?|libras?\s+por\s+pulgada\s+cuadrada|"
    r"pulgadas?|inches?|inch|libras?|onzas?|pies?|"
    r"miliamperios?\s*hora|miliamperios?hora|an(?:cho)?\.?|al(?:to)?\.?|mAh|Ah|(?i:db)|decibelios?|(?i:lm|lumens?|lúmenes?|lumenes?|pcs|piezas?|unidades?|conteo|grosor|f\.)|件|包装|ml|cl|dl|l\.|l(?!\.)|mg|g|kg|"
    # ``a`` is a Spanish preposition; only uppercase ``A`` is the
    # unambiguous ampere abbreviation in a mixed-language product field.
    r"mm|cm|km|m|w|kw|v|(?-i:A)|hz|mhz|ghz|bar|psi|°c|%|"
    r"毫升|毫克|厘?米|毫米|公里|千米|英寸|千克|公斤|克|瓦特|瓦|千瓦|伏特|伏|安(?![\u4e00-\u9fff])|赫兹|升|流明|分贝|件|毫安时|摄氏度|磅|盎司|英尺"
)
_UNIT_NAME_RE = (r"pies\s+cuadrados|onzas?\s+de\s+l[ií]quido|平方英尺|液体盎司|"
                 + _UNIT_NAME_RE.replace("unidades?", "unidad(?:es)?") + r"|个单位|只|支")
UNIT_RE = re.compile(
    # ``\w`` treats adjacent Chinese characters as word characters and would
    # miss ``9V已包含``. Only letters/digits should block a unit.
    r"(\d+(?:[.,]\d+)?)\s*(" + _UNIT_NAME_RE + r")(?![A-Za-z0-9_ÁÉÍÓÚÜÑáéíóúüñ])", re.I)
_DIMENSION_AXIS_RE = re.compile(
    r"(?<=\d)\s*(?:l\.|f\.|an\.|al\.|grosor|ancho|alto|largo|longitud|"
    r"anchura|altura|profundidad|espesor)(?=\s|[xX×])", re.I)
_DIMENSION_COMPACT_RE = re.compile(
    r"(?P<sequence>(?<![A-Za-z0-9])\d+(?:[.,]\d+)?"
    r"(?:\s*[x×*]\s*\d+(?:[.,]\d+)?)+"
    r"\s*(?:centímetros?|centimetros?|cm|milímetros?|milimetros?|mm|"
    r"metros?|m|pulgadas?|inches?|inch|英寸|厘米|毫米|米)?(?![A-Za-z]))", re.I)
_DIMENSION_PER_AXIS_RE = re.compile(
    r"(?P<sequence>(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s*(?:cm|centímetros?|"
    r"centimetros?|mm|milímetros?|milimetros?|m|metros?|英寸|厘米|毫米|米)"
    r"(?:\s*[×x*]\s*\d+(?:[.,]\d+)?\s*(?:cm|centímetros?|centimetros?|"
    r"mm|milímetros?|milimetros?|m|metros?|英寸|厘米|毫米|米))+(?![A-Za-z]))", re.I)
_DIMENSION_UNIT_ALIASES = {
    "centimetro": "cm", "centimetros": "cm", "centímetro": "cm",
    "centímetros": "cm", "cm": "cm", "厘米": "cm",
    "milimetro": "mm", "milimetros": "mm", "milímetro": "mm",
    "milímetros": "mm", "mm": "mm", "毫米": "mm",
    "metro": "m", "metros": "m", "m": "m", "米": "m",
    "pulgada": "in", "pulgadas": "in", "inch": "in",
    "inches": "in", "英寸": "in",
}
_DIMENSION_SENTINEL = "__dimension__"
UNIT_ALIASES = {
    "毫升": "ml", "millilitro": "ml", "millilitros": "ml",
    "mililitro": "ml", "mililitros": "ml",
    "厘米": "cm", "centimetro": "cm", "centimetros": "cm",
    "centímetro": "cm", "centímetros": "cm", "毫米": "mm",
    "milimetro": "mm", "milimetros": "mm", "milímetro": "mm",
    "milímetros": "mm", "米": "m", "metro": "m", "metros": "m",
    "公里": "km", "千米": "km", "km": "km", "kilómetro": "km",
    "kilómetros": "km", "kilometro": "km", "kilometros": "km",
    "千克": "kg", "kilogramo": "kg", "kilogramos": "kg", "公斤": "kg",
    "克": "g", "gramo": "g", "gramos": "g", "gr": "g", "grs": "g", "gm": "g", "毫克": "mg",
    "miligramo": "mg", "miligramos": "mg", "瓦特": "w", "瓦": "w", "vatio": "w",
    "vatios": "w", "watios": "w", "千瓦": "kw", "kilovatio": "kw",
    "kilovatios": "kw", "伏特": "v", "伏": "v", "voltio": "v", "voltios": "v",
    "安": "a", "amperio": "a", "amperios": "a", "赫兹": "hz",
    "hercio": "hz", "hercios": "hz", "hertzio": "hz", "hertzios": "hz",
    "升": "l", "litro": "l", "litros": "l", "毫安时": "mah",
    "miliamperio hora": "mah", "miliamperios hora": "mah",
    "miliamperiohora": "mah", "miliamperioshora": "mah", "摄氏度": "°c",
    "grados celsius": "°c", "grado celsius": "°c",
    "grados centígrados": "°c", "grado centígrado": "°c",
    "db": "db", "decibelio": "db", "decibelios": "db", "分贝": "db",
    "lm": "lm", "lumen": "lm", "lumens": "lm", "lumenes": "lm", "lúmenes": "lm", "流明": "lm",
    "pcs": "pcs", "pieza": "pcs", "piezas": "pcs", "unidad": "pcs", "unidades": "pcs", "conteo": "pcs", "件": "pcs", "包装": "pcs",
    "grosor": "cm", "f": "cm", "f.": "cm", "l.": "cm",
    "liters": "l", "liter": "l", "libras por pulgada cuadrada": "psi",
    "pulgada": "in", "pulgadas": "in", "inch": "in", "inches": "in",
    "英寸": "in",
    "an": "cm", "an.": "cm", "ancho": "cm", "ancho.": "cm",
    "al": "cm", "al.": "cm", "alto": "cm", "alto.": "cm",
    "libras": "lb", "libra": "lb", "onzas": "oz", "onza": "oz",
    "pies": "ft", "pie": "ft", "磅": "lb", "盎司": "oz", "英尺": "ft",
    "mhz": "mhz", "megahercio": "mhz", "megahercios": "mhz",
    "ghz": "ghz", "gigahercio": "ghz", "gigahercios": "ghz",
}

UNIT_ALIASES.update({'pies cuadrados':'ft2', '平方英尺':'ft2',
                     'onza de líquido':'fl_oz', 'onzas de líquido':'fl_oz',
                     '液体盎司':'fl_oz', '个单位':'pcs', '只':'pcs', '支':'pcs'})

_SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
    "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

_COUNT_NOUN_RE = re.compile(
    r"(?i)\b\d+(?:[.,]\d+)?\s+"
    r"(?:juguetes?|chupetes?|filtros?|accesorios?|art[ií]culos?|"
    r"piezas?|unidades?|paquetes?|packs?|rollos?|vasos?|bombillas?)\b"
)
_COMPACT_SLASH_PACK_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<sizes>\d+(?:/\d+){1,})\s*(?P<unit>mm|cm)\s*(?P<count>\d+)\s*pcs\b")
_HYPHEN_PACK_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*-\s*pack\b")
_SOURCE_NEGATION_RE = re.compile(
    r"(?i)(?<!\w)(?:sin\b|no\s+(?:requiere|contiene|incluye)|libre\s+de\b|"
    r"sin\s+fragancia\b|sin\s+perfume\b)"
)
_TARGET_NEGATION_RE = re.compile(
    r"(?:不含(?:有)?|不包含|没有|无需|不需要|免(?:于|除)?|"
    r"无(?:糖|麸质|香料|酒精|乳胶|硅|塑料|添加|异味|系列))"
)


_QA_DIMENSION_UNIT = r"cent[ií]metros?|cm|mil[ií]metros?|mm|metros?|m|pulgadas?|inches?|inch|英寸|厘米|毫米|米"
_QA_DIMENSION_RE = re.compile(
    r"(?P<sequence>(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s*(?:" + _QA_DIMENSION_UNIT + r")?"
    r"(?:\s*[x×*]\s*\d+(?:[.,]\d+)?\s*(?:" + _QA_DIMENSION_UNIT + r")?)+)(?![A-Za-z])", re.I)
_QA_MODEL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")


def _zh_count(value: str) -> int:
    digits = dict(zip('零一二三四五六七八九', range(10)))
    digits['两'] = 2
    total = current = 0
    for char in value:
        if char in '十百':
            total += (current or 1) * {'十':10, '百':100}[char]
            current = 0
        else:
            current = digits[char]
    return total + current


def _qa_fact_text(text: str, count_values=None) -> str:
    """QA-only equivalents, never mutate source/candidate/display evidence."""
    # Keep nº17 intact: blanket NFKC turns it into no17 and hides the rank.
    value = str(text or '')
    value = re.sub(r'(\d+(?:[.,]\d+)?)\s+millones\b',
                   lambda m: str(_number_key(m[1]) * 1000000), value, flags=re.I)
    value = re.sub(r'(\d+(?:[.,]\d+)?)万', lambda m: str(_number_key(m[1]) * 10000), value)
    value = re.sub(r'(?<![\d.,])(\d{1,3})[ \u00a0](\d{3})(?=\s+horas?\b)',
                   lambda m: m[1] + m[2], value, flags=re.I)
    value = re.sub(r'([一二两三四五六七八九十百]+)合([一二两三四五六七八九十百]+)',
                   lambda m: str(_zh_count(m[1])) + '合' + str(_zh_count(m[2])), value)
    return re.sub(r'[一二两三四五六七八九十百]+(?=种|个|条|层|重|年|强|台|只|支)',
                  lambda m: m[0] if value[max(0,m.start()-1):m.start()] == '每'
                  or (count_values is not None and _number_key(str(_zh_count(m[0]))) not in count_values)
                  else ' ' + str(_zh_count(m[0])), value)


def _qa_dimension_text(text: str, count_values=None) -> str:
    value = _DIMENSION_AXIS_RE.sub('', _qa_fact_text(text, count_values))
    value = re.sub(r'[（(](?:长|宽|高|深|L|H|P)[）)]', '', value)
    return re.sub(r'(?<=\d)(?:长|宽|高|深)(?=[×x*，,\s]|厘米|毫米|英寸|米|$)', '', value)


def _dimension_axis_mismatch(source: str, target: str) -> bool:
    """Compare explicit axes only; absent labels never invent geometry."""
    axes = {'l.':'长', 'f.':'深', 'an.':'宽', 'al.':'高'}
    expected = {}
    for number, axis in re.findall(r'(\d+(?:[.,]\d+)?)\s*(l\.|f\.|an\.|al\.)', source, re.I):
        expected.setdefault(axes[axis.casefold()], set()).add(_number_key(number))
    for number, axis in re.findall(r'(\d+(?:[.,]\d+)?)\s*(?:' + _QA_DIMENSION_UNIT + r')\s*\(([LHP])\)', source):
        expected.setdefault({'L':'长','H':'高','P':'深'}[axis], set()).add(_number_key(number))
    if len(expected) < 2:
        return False
    labelled = [(axis, number) for number, axis in re.findall(
        r'(\d+(?:[.,]\d+)?)\s*(?:' + _QA_DIMENSION_UNIT + r')?\s*[（(](长|宽|高|深)[）)]', target, re.I)]
    labelled += re.findall(r'(长|宽|高|深)\s*(\d+(?:[.,]\d+)?)', target)
    labelled += [(axis, number) for number, axis in re.findall(r'(\d+(?:[.,]\d+)?)(长|宽|高|深)', target)]
    return any(axis not in expected or _number_key(number) not in expected[axis] for axis, number in labelled)


def _negation_mismatch(source: str, target: str) -> bool:
    source = unicodedata.normalize('NFKC', source)
    source_negative = bool(_SOURCE_NEGATION_RE.search(source) or re.search(
        r'(?i)\bno\s+(?:necesita|incluid[oa])\b', source))
    if (re.search(r'(?i)\b(?:evita(?:ndo)?|avoid)\b', source)
            and re.search(r'免去|避免', target)):
        source_negative = True
    target_negative = bool(_TARGET_NEGATION_RE.search(target) or (source_negative and re.search(
        r'不(?:包含|附带|带|使|会|显|支持|占|重叠|缠绕|晕染|模糊|易)|免去|免遭|避免|毫不|'
        r'无(?:线|刷|BPA|铅|粉尘|遥控|运动检测|弹性|饰面|气泡|裂纹|滴答|干扰|眩光|忧|烦恼|痕|堵塞)', target, re.I)))
    if re.search(r'(?i)\bno\s+autorizad[oa]s?\b', source):
        source_negative = True
    if re.search(r'(?i)\bproteger\b', source) and re.search(r'免受|免遭', target):
        source_negative = True
    equivalents = [(r'sin\s+esfuerzo', r'轻松|简便'), (r'sin\s+riesgo', r'放心'),
                   (r'sin\s+preocupaciones', r'安心|无忧'), (r'sin\s+complicaciones', r'轻松|无烦恼'),
                   (r'sin\s+fatiga', r'毫不疲倦'), (r'sin\s+efecto\s+m[aá]scara', r'不显假面'),
                   (r'no\s+consiguen', r'难以'), (r'no\s+te\s+preocupes', r'即使')]
    for pattern, equivalent in equivalents:
        if re.search(pattern, source, re.I) and re.search(equivalent, target):
            target_negative = True
    # A different negative phrase must never conceal a lost material absence.
    if re.search(r'(?i)(?:libre\s+de|sin|no\s+(?:contiene|incluye))\s+BPA\b', source):
        return not bool(re.search(r'(?:不含|无|不包含|没有|未添加)\s*(?:BPA|双酚\s*A)', target, re.I))
    return source_negative != target_negative


def _identity_key(value: str) -> str:
    """Normalize an identity token for the title no-brand policy only."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _units(text: str, count_values=None) -> List[tuple[str, str]]:
    value = _qa_dimension_text(text, count_values)
    units: List[tuple[str, str]] = []
    dimension_spans = []
    matches = list(_QA_DIMENSION_RE.finditer(value))
    for match in matches:
        sequence = match.group("sequence")
        unit_matches = re.findall(
            r"(?i)(centímetros?|centimetros?|cm|milímetros?|milimetros?|mm|"
            r"metros?|m|pulgadas?|inches?|inch|英寸|厘米|毫米|米)",
            sequence,
        )
        if not unit_matches:
            continue
        names = {_DIMENSION_UNIT_ALIASES.get(unit.casefold(), unit.casefold()) for unit in unit_matches}
        if len(names) != 1:
            # Keep incompatible cm/in/mm facts separate so unit corruption
            # cannot disappear behind one final dimension sentinel.
            continue
        dimension_spans.append(match.span())
        canonical = _DIMENSION_UNIT_ALIASES.get(unit_matches[-1].casefold(),
                                                 unit_matches[-1].casefold())
        units.append((_DIMENSION_SENTINEL, canonical))

    # Remove complete dimension tuples before ordinary number+unit matching;
    # otherwise ``48l. x 32an. x 25al. centímetros`` is incorrectly treated
    # as three independent centimeter facts while the Chinese display may
    # legitimately carry one trailing ``厘米``.
    ordinary_text = value
    for start, end in reversed(dimension_spans):
        ordinary_text = ordinary_text[:start] + " " * (end - start) + ordinary_text[end:]
    units.extend((number.replace(",", "."), UNIT_ALIASES.get(unit.casefold(), unit.casefold()))
                 for number, unit in UNIT_RE.findall(ordinary_text))
    units.extend((number.replace(',', '.'), '%') for number in
                 re.findall(r'(\d+(?:[.,]\d+)?)%(?=[A-Za-z])', ordinary_text))
    units.extend((number.replace(',', '.'), 'in') for number in
                 re.findall(r'(?<!["\w])(\d+(?:[.,]\d+)?)"', ordinary_text))
    for number in re.findall(r'(?i)\b(?:deskset|set)\s+of\s+(\d+)\s+x\b|\b(\d+)\s+marcador(?:es)?\b', ordinary_text):
        fact = (next(part for part in number if part), 'pcs')
        if fact not in units:
            units.append(fact)
    # Packaging quantities are written as a noun-first phrase, unlike the
    # normal number+unit form.  Keep this in QA only; normalization/business
    # package-count rules still decide whether a generic package of one is a
    # derived quantity.
    for number in re.findall(
            r"(?i)\b(?:paquete|pack|set|conjunto)\s+de\s+(\d+(?:[.,]\d+)?)\b",
            value):
        fact = (number, "pcs")
        if fact not in units or not re.search(r"(?i)\b" + re.escape(number) + r"\s+marcador(?:es)?\b", value):
            units.append(fact)
    # Amazon titles sometimes glue a size series and item count together,
    # e.g. ``6/8/10/12mm8pcs``.  It is intentionally a narrow full-pattern
    # rule, not a generic relaxation of adjacent number/unit validation.
    for match in _COMPACT_SLASH_PACK_RE.finditer(value):
        sizes = match.group("sizes").split("/")
        for fact in ((sizes[-1], match.group("unit").casefold()), (match.group("count"), "pcs")):
            if fact not in units:
                units.append(fact)
    for number in _HYPHEN_PACK_RE.findall(value):
        units.append((number, "pcs"))
    return units


def _unit_keys(values: Iterable[tuple[str, str]]) -> List[tuple[Any, str]]:
    """Compare unit facts by numeric meaning, not decimal punctuation."""
    return [(_number_key(number), unit) for number, unit in values]


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


def _fact_numbers(text: str, count_values=None) -> List[str]:
    """Extract standalone and unit-bound numbers without double counting."""
    normalized_text = _qa_dimension_text(text, count_values)
    compact_dimensions = re.compile(
        r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?:\s*[×x*]\s*\d+(?:[.,]\d+)?)+"
        r"(?:\s*(?:mm|cm|m))?(?![A-Za-z])", re.I)
    dimension_matches = list(_QA_DIMENSION_RE.finditer(normalized_text))
    model_matches = list(_QA_MODEL_RE.finditer(normalized_text))
    standalone = Counter()
    # Dimension tuples are counted as complete occurrences below. Exclude
    # their individual axes from the generic number pass so repeated tuples
    # such as ``40×25`` appearing in both dimensions and size are preserved.
    number_matches = list(NUMBER_RE.finditer(normalized_text))
    for number_match in number_matches:
        if any(match.start() <= number_match.start() < match.end()
               for match in dimension_matches + model_matches):
            continue
        standalone[_number_key(number_match.group(0))] += 1
    observed = standalone
    # Count each unit-bound occurrence omitted by NUMBER_RE, rather than
    # taking max(counter): max loses a repeated final17 in23+11+17+17L.
    for match in UNIT_RE.finditer(normalized_text):
        if any(span.start() <= match.start() < span.end() for span in dimension_matches + model_matches):
            continue
        if not any(span.start() == match.start() for span in number_matches):
            observed[_number_key(match.group(1))] += 1
    # NUMBER_RE intentionally ignores digits adjacent to letters.  A compact
    # dimension such as ``10x15cm`` is nevertheless a user-visible numeric
    # fact, so add its axes explicitly without double-counting standalone
    # numbers already observed above.
    dimension_additions = Counter()
    for match in dimension_matches:
        dimensions = Counter(_normalized_numbers(
            re.findall(r"\d+(?:[.,]\d+)?", match.group(0))))
        dimension_additions += dimensions
    observed += dimension_additions
    # Amazon titles often encode multipacks as ``Pack x2``.  The generic
    # number guard intentionally ignores digits glued to letters, so account
    # for this explicit package-count form without treating dimensions as a
    # second copy of the same numbers.
    for number in re.findall(
            r"(?i)\b(?:pack|paquete|set|conjunto)\s*[x×]\s*(\d+)\b",
            str(text or "")):
        key = _number_key(number)
        observed[key] += 1
    # Compact Spanish multipacks such as ``3en1`` keep both numeric facts but
    # the generic standalone-number regex intentionally ignores digits glued
    # to letters. Their Chinese rendering is commonly ``3合1``.
    for left, right in re.findall(
            r"(?i)(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)(?:en|-en-)(\d+(?:[.,]\d+)?)(?![A-Za-z0-9])",
            str(text or "")):
        observed[_number_key(left)] += 1
        observed[_number_key(right)] += 1
    for match in _COMPACT_SLASH_PACK_RE.finditer(str(text or "")):
        required = Counter(_number_key(number)
                           for number in (*match.group("sizes").split("/"), match.group("count")))
        for number, count in required.items():
            if observed[number] < count:
                observed[number] += count - observed[number]
    return [str(value) for value in observed.elements()]


def _date_month_allowance(source: str) -> Counter:
    """Allow the one numeric month introduced by a Chinese date rendering.

    ``24 octubre 2025`` → ``2025年10月24日`` is fact-preserving even though
    the Spanish source spells the month.  Only one month number per explicit
    source month is allowed; unrelated hallucinated numbers remain failures.
    """
    allowance: Counter = Counter()
    for word in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", str(source or "").casefold()):
        month = _SPANISH_MONTHS.get(word)
        if month is not None:
            allowance[_number_key(str(month))] += 1
    return allowance


def _protected_token_is_equivalent(token: str, source: str, translated: str) -> bool:
    """Accept a semantic unit/number rendering in place of a lost marker.

    Qwen may remove ``__T0000__`` while still returning ``9伏特`` for source
    ``9V``. Technical units are allowed to translate; identity/model tokens
    still require literal preservation.
    """
    if str(token).casefold() == 'led':
        return bool(re.search(r'(?<![A-Za-z])LED(?![A-Za-z])', translated, re.I))
    # Compact Amazon dimension markers such as ``3,5Grosor`` are protected as
    # unit-like tokens.  Their faithful Chinese form is a dimension axis, so
    # compare the numeric fact rather than requiring the same isolated unit
    # tuple after the whole dimension has been normalized.
    if re.search(r"(?i)(?:l\.|f\.|an\.|al\.|grosor|ancho|alto|largo|"
                 r"longitud|anchura|altura|profundidad|espesor)", str(token or "")):
        token_numbers = Counter(_normalized_numbers(
            re.findall(r"\d+(?:[.,]\d+)?", str(token or ""))))
        result_numbers = Counter(_normalized_numbers(_fact_numbers(translated)))
        return all(result_numbers[number] >= count
                   for number, count in token_numbers.items())
    token_units = _unit_keys(_units(token))
    if not token_units:
        pack = _HYPHEN_PACK_RE.fullmatch(str(token or "").strip())
        if pack:
            return Counter(_unit_keys(_units(translated))).get((_number_key(pack.group(1)), "pcs"), 0) > 0
    if token_units:
        result_units = _unit_keys(_units(translated))
        remaining = Counter(result_units)
        result_numbers = Counter(_normalized_numbers(_fact_numbers(translated)))
        for item in token_units:
            if (item[1] in {"cm", "mm", "m", "in"}
                    and (_DIMENSION_SENTINEL, item[1]) in remaining
                    and result_numbers.get(item[0], 0) > 0):
                continue
            if remaining[item] <= 0:
                return False
            remaining[item] -= 1
        return True
    # ``UV`` is a standard abbreviation whose faithful Chinese display is
    # ``紫外线``. Accept this one explicit technical equivalence while keeping
    # model/interface tokens such as USB-C and UPF50 literal.
    if str(token or "").strip().casefold() in {"uv", "u.v."}:
        return bool(re.search(r"紫外线|\buv\b", str(translated or ""), re.I))
    # A bare numeric marker may be rendered with surrounding Chinese prose,
    # but a composite identity such as ``DGT 3.0`` must not degrade to
    # ``DGT 3`` merely because one numeric fragment survived.
    if re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", str(token or "")):
        token_numbers = _normalized_numbers(NUMBER_RE.findall(token))
        result_numbers = Counter(_normalized_numbers(NUMBER_RE.findall(translated)))
        return all(result_numbers[number] >= count for number, count in Counter(token_numbers).items())
    return False


def validate_translation(protected: ProtectedText, translated: str,
                         source: str, *, field: str = "",
                         brand: str = "",
                         allowed_residual: Iterable[str] = ()) -> List[Dict[str, Any]]:
    restored, restore_issues = restore(protected, translated)
    # A translated unit such as ``9伏特`` is semantically equivalent to the
    # protected source token ``9V`` even when the provider dropped the marker.
    # Keep literal identity/model markers strict; only numeric/unit tokens may
    # use this equivalence.
    issues = []
    for issue in restore_issues:
        # The displayed Chinese name intentionally removes the separately
        # stored brand. The source brand remains immutable in brand_zh/raw
        # evidence, so its protected placeholder must not fail name QA.
        if (issue.get("code") == "PROTECTED_TOKEN_MISSING"
                and field in {"title", "title_es", "title_es_raw"}
                and brand
                and _identity_key(str(issue.get("token") or ""))
                    == _identity_key(brand)):
            continue
        if (issue.get("code") == "PROTECTED_TOKEN_MISSING"
                and _protected_token_is_equivalent(str(issue.get("token") or ""), source, restored)):
            continue
        issues.append(issue)
    # Validate the same narrow deterministic cleanup that the display layer
    # applies.  A protected ``5200 mAh`` may be echoed as ``__T0000__mAh``;
    # restoration then temporarily yields ``5200 mAhmAh`` even though the
    # final display value is normalized back to ``5200 mAh``.
    restored = dedupe_technical_units(restored)
    source_numbers = _fact_numbers(str(source or ""))
    result_numbers = _fact_numbers(restored, count_values=set(_normalized_numbers(source_numbers)))
    source_number_keys = Counter(_normalized_numbers(source_numbers))
    result_number_keys = Counter(_normalized_numbers(result_numbers))
    allowed_numbers = source_number_keys + _date_month_allowance(source)
    if result_number_keys != allowed_numbers:
        source_counter = allowed_numbers
        result_counter = result_number_keys
        added = []
        for number, count in result_counter.items():
            if count > source_counter.get(number, 0):
                added.extend([number] * (count - source_counter.get(number, 0)))
        if added:
            issues.append({"code": "ADDED_NUMBER", "numbers": [str(x) for x in added]})
        issues.append({"code": "NUMERIC_MISMATCH", "source": source_numbers, "result": result_numbers})
    source_units_raw = _units(source)
    result_units_raw = _units(restored, count_values=set(_normalized_numbers(source_numbers)))
    unit_mismatch = Counter(_unit_keys(source_units_raw)) != Counter(_unit_keys(result_units_raw))
    # Spanish often expresses a count through the product noun (``5 juguetes``)
    # while Chinese uses the explicit classifier ``5件``. This is a faithful
    # classifier rendering, not an invented source unit.
    if (unit_mismatch and not source_units_raw and result_units_raw
            and all(unit == "pcs" for _, unit in result_units_raw)
            and _COUNT_NOUN_RE.search(str(source or ""))):
        unit_mismatch = False
    if unit_mismatch:
        issues.append({"code": "UNIT_MISMATCH", "source": source_units_raw,
                       "result": result_units_raw})
    source_is_negative = bool(_SOURCE_NEGATION_RE.search(str(source or "")))
    target_is_negative = bool(_TARGET_NEGATION_RE.search(restored))
    if _negation_mismatch(str(source or ''), restored):
        issues.append({"code": "NEGATION_MISMATCH",
                       "source_negative": source_is_negative,
                       "result_negative": target_is_negative})
    if _dimension_axis_mismatch(str(source or ''), restored):
        issues.append({'code':'DIMENSION_AXIS_MISMATCH'})
    if (re.search(r'(?i)\bPVC\b', source) and re.search(r'(?i)\bcolor\s+madera\b', source)
            and re.search(r'材质\s*[:：]?\s*木|木材|实木|木制', restored)):
        issues.append({'code':'COLOR_MATERIAL_MISMATCH'})
    if field in {"feature_bullets", "feature_bullets_es", "feature_bullets_raw", "features_es"}:
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
    words = re.findall(r"\b[a-záéíóúñü]{2,}\b", restored.lower())
    # Keep this a small high-signal lexicon rather than treating every source
    # word as residual: identity brands and deliberate Spanish product names
    # are valid evidence.  The common connectivity phrase below catches the
    # previously missed ``EN TIEMPO REAL / SIM INTEGRADA HASTA`` output.
    spanish = {"en", "para", "con", "sin", "producto", "negro", "blanco", "rojo", "azul",
               "tamaño", "incluye", "material", "tiempo", "real", "sim", "integrada",
               "hasta", "compatible", "conexión", "conexion", "lubrica"}
    allowed = {str(x).lower() for x in allowed_residual}
    protected_words = {
        word.casefold()
        # Only explicit identity/brand values are exempt.  Generic uppercase
        # words are protected for token transport, but may still be Spanish
        # prose that the provider accidentally left untranslated.
        for value in protected.protected_values
        for word in re.findall(r"\b[a-záéíóúñü]{2,}\b", str(value).lower())
    }
    residual = sorted({w for w in words
                       if w not in allowed
                       and w not in protected_words
                       and w in spanish})
    if residual:
        issues.append({"code": "SPANISH_RESIDUAL", "tokens": residual})
    return issues
