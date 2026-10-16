"""Conservative offline checks for derived numbers and explicit source conflicts."""
import math
import re

from ..normalization.locale_numbers import parse_price_es, parse_rating_es, parse_review_count_es
from ..normalization.monthly_bought import parse_monthly_bought


def check_numbers(record, actual, add):
    parsers = {
        'current_price': ('current_price_raw', parse_price_es),
        'reference_price': ('reference_price_raw', parse_price_es),
        'unit_price': ('unit_price_raw', parse_price_es),
        'rating_value': ('rating_raw', parse_rating_es),
        'rating_count': ('review_count_raw', parse_review_count_es),
        'monthly_bought_min': ('monthly_bought_raw', parse_monthly_bought),
    }
    for field, (source, parser) in parsers.items():
        value = record.get(field)
        if value is None:
            continue
        try:
            expected = parser(actual.get(source))
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        except (TypeError, ValueError, OverflowError):
            expected, valid = None, False
        valid = valid and record.get(source) == actual.get(source)
        valid = valid and expected is not None and abs(value - expected) < 0.000001 and value >= 0
        if field == 'rating_value':
            valid = valid and value <= 5
        if field in {'rating_count', 'monthly_bought_min'}:
            valid = valid and float(value).is_integer()
        if not valid:
            add(field, 'NORMALIZED_NUMBER_UNVERIFIED', f'规范化数值与{source}不一致或缺少源证据；保留原值待审。',
                severity='P1', state='SOURCE_CONFLICT')


def explicit_spec_conflicts(contents):
    """Compare only unambiguous measurements of the same dimension."""
    measures = re.compile(r'(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(ml|kg|mg|l|g|w|v)\b', re.I)
    units = {'ml': ('capacity', 1), 'l': ('capacity', 1000), 'mg': ('weight', .001),
             'g': ('weight', 1), 'kg': ('weight', 1000), 'w': ('power', 1), 'v': ('voltage', 1)}
    rows = [(f, None, contents[f]) for f in ('selected_variation_raw', 'title_es_raw') if isinstance(contents.get(f), str)]
    attrs = contents.get('attributes')
    for index, attr in enumerate(attrs if isinstance(attrs, list) else []):
        if isinstance(attr, dict) and re.search(r'capacidad|peso|potencia|voltaje|tensi[oó]n', str(attr.get('label_raw')), re.I):
            rows.append(('attributes', index, attr.get('value_raw')))
    by_kind = {}
    for field, index, text in rows:
        found = {}
        for number, unit in measures.findall(str(text or '')):
            kind, scale = units[unit.lower()]
            found.setdefault(kind, set()).add(round(float(number.replace(',', '.')) * scale, 6))
        for kind, values in found.items():
            if len(values) == 1:
                by_kind.setdefault(kind, []).append({'field': field, 'attribute_source_index': index,
                    'value': next(iter(values)), 'source_text': text})
    return {kind: rows for kind, rows in by_kind.items() if len({r['value'] for r in rows}) > 1}
