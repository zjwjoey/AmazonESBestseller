"""Prove a few explicit included-vessel lists without rewriting source facts.

Unknown quantities fail closed. Lists remain lists: no shape/height assignment,
set total volume, or canonical product capacity is inferred here.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any


_WORDS = {'un': 1, 'una': 1, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4,
          'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9,
          'diez': 10, 'doce': 12, 'dieciocho': 18, 'veinticuatro': 24}
_COUNT = r'(?:[1-9]\d*|' + '|'.join(_WORDS) + r')'
_VESSEL = r'(?:recipientes?|cuencos?|molinillos?|vasos?|botellas?|envases?)'
_INTRO = re.compile(r'\b(?:(?:consta de|incluye|contiene|contiende|conjunto completo de|juego de)\s+'
                    r'|incluye\s*:[^\n]{0,120}?)'
                    r'(?P<count>' + _COUNT + r')\s+(?P<vessel>' + _VESSEL + r')\b', re.I)
_HEADING = re.compile(r'(?m)^\s*(?P<count>' + _COUNT + r')\s+(?P<vessel>' + _VESSEL + r')\b', re.I)
_INCLUDED = re.compile(r'\b(?:incluye|contiene|contiende|consta de|recibir[aá]s|'
                       r'contenido (?:incluido|del producto|de la caja))\b', re.I)


def _count(text: str) -> int:
    return int(text) if text.isdigit() else _WORDS[text.casefold()]


def _key(text: str) -> str:
    return re.sub(r'\s+', ' ', text.casefold()).strip()


def _declared_counts(value: str, capacities: list[tuple[Any, Decimal]]) -> set[int]:
    result = set()
    for match in [*_INTRO.finditer(value), *_HEADING.finditer(value)]:
        following = next((m for m, _ in capacities if m.start() >= match.end()), None)
        # "Incluye 1 recipiente de 1,8 L" is a component, not the whole set.
        if following and re.fullmatch(r'\s+de\s*', value[match.end():following.start()], re.I):
            continue
        result.add(_count(match.group('count')))
    return result


def _lists(value: str, capacities: list[tuple[Any, Decimal]]) -> list[tuple[int, list]]:
    proofs = []
    for intro in _INTRO.finditer(value):
        following = [(m, amount) for m, amount in capacities if m.start() >= intro.end()]
        if not following:
            continue
        # A list must be part of this actual included-vessel statement, not
        # another bullet/paragraph. Optional dimensions are kept verbatim.
        lead = value[intro.end():following[0][0].start()]
        if len(lead) > 180 or re.search(r'[\n.!?]', lead):
            continue
        series = [following[0]]
        for item in following[1:]:
            gap = value[series[-1][0].end():item[0].start()]
            if not re.fullmatch(r'\s*(?:,\s*)?(?:y\s+|&\s*)?', gap, re.I):
                break
            series.append(item)
        count = _count(intro.group('count'))
        if count == len(series) and count > 1:
            proofs.append((count, series))
    return proofs


def _named_container_prefix(value: str, match: Any) -> str | None:
    prefix = value[max(0, match.start() - 100):match.start()]
    found = re.search(r'\brecipiente\s+(grande|pequeñ[oa])\s+de\s*$', prefix, re.I)
    return ('container:large' if found.group(1).casefold() == 'grande' else 'container:small') if found else None


def _named_repeat_list(value: str, capacities: list[tuple[Any, Decimal]], proofs: list) -> bool:
    for count, series in proofs:
        inside = {m.start() for m, _ in series}
        roles: dict[str, set[Decimal]] = defaultdict(set)
        unknown = False
        for match, amount in capacities:
            if match.start() in inside:
                continue
            role = _named_container_prefix(value, match)
            if role is None:
                unknown = True
                break
            roles[role].add(amount)
        if (not unknown and len(roles) == count and all(len(v) == 1 for v in roles.values())
                and {next(iter(v)) for v in roles.values()} == {v for _, v in series}):
            return True
    return False


def _counted_groups(value: str, capacities: list[tuple[Any, Decimal]], declared: set[int]) -> bool:
    entries = []
    roles: dict[str, set[Decimal]] = defaultdict(set)
    anonymous = False
    for match, amount in capacities:
        prefix = value[max(0, match.start() - 100):match.start()]
        counted = re.search(r'(?<!\w)(?P<count>' + _COUNT + r')\s*[x×]\s*$', prefix, re.I)
        if counted:
            anonymous = True
        else:
            candidates = []
            for start in re.finditer(r'(?<!\w)' + _COUNT + r'\b', prefix, re.I):
                found = re.fullmatch(r'(?P<count>' + _COUNT + r')\s+(?P<label>' + _VESSEL +
                                     r'[^\d\n.;,:()]{0,55}?)\s+de\s*', prefix[start.start():], re.I)
                if found:
                    candidates.append(found)
            counted = candidates[-1] if candidates else None
            if counted:
                roles[_key(counted.group('label'))].add(amount)
            else:
                # "frasco 1" is an identifier, not a quantity. Ellipsis is
                # accepted only at an explicit list-item boundary.
                counted = re.search(r'(?:^|[;,]|\by)\s*(?P<count>' + _COUNT + r')\s+de\s*$', prefix, re.I)
                anonymous = True
        if counted is None:
            return False
        entries.append(_count(counted.group('count')))
    if any(len(v) > 1 for v in roles.values()):
        return False
    if declared:
        return declared == {sum(entries)}
    # In the absence of an overall package count, every group must still have
    # its own distinct literal name, e.g. salsa vs condimento bottles.
    return (not anonymous and len(roles) == len(entries) and len(roles) > 1
            and bool(_INCLUDED.search(value[:capacities[0][0].start()])))


def _numbered_bottles(value: str, capacities: list[tuple[Any, Decimal]]) -> bool:
    roles: dict[str, set[Decimal]] = defaultdict(set)
    unbound = []
    for match, amount in capacities:
        prefix = value[max(0, match.start() - 50):match.start()]
        role = re.search(r'\b(?:frasco|botella)\s+([1-9]\d*)\s+de\s*$', prefix, re.I)
        if role:
            roles[role.group(1)].add(amount)
        else:
            unbound.append((match, amount))
    if len(roles) != 2 or any(len(v) != 1 for v in roles.values()):
        return False
    if not unbound:
        return True
    if len(unbound) != 2:
        return False
    first, second = unbound
    prefix = value[max(0, first[0].start() - 35):first[0].start()]
    return (bool(re.search(r'\blos envases de\s*$', prefix, re.I))
            and bool(re.fullmatch(r'\s+y\s+', value[first[0].end():second[0].start()], re.I))
            and sorted([first[1], second[1]]) == sorted(next(iter(v)) for v in roles.values()))


def _named_box_liquids(value: str, capacities: list[tuple[Any, Decimal]]) -> bool:
    roles: dict[str, set[Decimal]] = defaultdict(set)
    kinds = set()
    for match, amount in capacities:
        start = value.rfind('\n', 0, match.start()) + 1
        if not re.match(r'CONTENIDO DE LA CAJA\s*:', value[start:match.start()], re.I):
            return False
        suffix = re.split(r'[,\n.]', value[match.end():], maxsplit=1)[0]
        label = re.fullmatch(r'\s+de\s+((?:soluci[oó]n|eliminador)\s+[^\d]+?)\s*', suffix, re.I)
        if label is None:
            return False
        roles[_key(label.group(1))].add(amount)
        kinds.add('solution' if re.match(r'soluci[oó]n\b', label.group(1), re.I) else 'remover')
    # Different adjectives alone do not prove that two phrases are different
    # liquids. Require the two explicitly different product kinds as well.
    return len(roles) == len(kinds) == 2 and all(len(amounts) == 1 for amounts in roles.values())


def _named_firstaid_contents(value: str, capacities: list[tuple[Any, Decimal]]) -> bool:
    roles: dict[str, set[tuple[int, Decimal]]] = defaultdict(set)
    for volume, amount in capacities:
        line_start = value.rfind('\n', 0, volume.start()) + 1
        if not re.match(r'Contenido incluido\s*:', value[line_start:volume.start()], re.I):
            return False
        prefix = value[max(line_start, volume.start() - 100):volume.start()]
        found = re.search(r'(?<!\w)(?P<count>' + _COUNT + r')\s+unidad(?:es)?\s+de\s+'
                          r'(?P<kind>agua oxigenada|alcohol(?:\s+\d+(?:[.,]\d+)?\s*[º°o])?|'
                          r'povidona yodada|suero fisiol[oó]gico(?:\s+monodosis)?)\s*$', prefix, re.I)
        if found is None:
            return False
        kind = _key(found.group('kind'))
        if kind.startswith('alcohol'):
            kind = 'alcohol'
        elif kind.startswith('suero'):
            kind = 'suero'
        roles[kind].add((_count(found.group('count')), amount))
    if (set(roles) != {'agua oxigenada', 'alcohol', 'povidona yodada', 'suero'}
            or any(len(items) != 1 or next(iter(items))[1] <= 0 for items in roles.values())):
        return False
    # A literal total bottle count, if given, refers to the bottle inventory;
    # other non-liquid first-aid pieces are deliberately not assigned volumes.
    declared = re.findall(r'\b(?:Contenido incluido\s*:|incluye|contiene)\s*(' + _COUNT +
                          r')\s+(?:botellas|frascos|envases)\b', value, re.I)
    count = sum(next(iter(items))[0] for items in roles.values())
    return all(_count(n) == count for n in declared)


def _counted_tableware(value: str, capacities: list[tuple[Any, Decimal]]) -> bool:
    # Non-volume flat/dessert plates are actual included pieces, not missing
    # liquid capacities. Read exactly the declared four groups and two labeled
    # per-piece capacities; no volume is assigned to the other two groups.
    volume = r'\d+(?:[.,]\d+)?\s*(?:mililitros?|centilitros?|litros?|ml|cl|l|cc)\b'
    count = _COUNT
    pattern = (r'(?P<total>' + count + r')\s+piezas(?P<context>[^\n.]{0,120})\bincluye\s+'
               r'(?P<flat>' + count + r')\s+platos llanos\s*,\s*'
               r'(?P<dessert>' + count + r')\s+platos de postre\s*,\s*'
               r'(?P<deep>' + count + r')\s+platos hondos\s*\(\s*cada uno con capacidad de\s*'
               r'(?P<deep_volume>' + volume + r')\s+hasta el borde\s*\)\s+y\s+'
               r'(?P<bowl>' + count + r')\s+cuencos multiusos\s*\(\s*cada uno con capacidad de\s*'
               r'(?P<bowl_volume>' + volume + r')\s+hasta el borde\s*\)')
    for statement in re.finditer(pattern, value, re.I):
        counts = [_count(statement.group(role)) for role in ('flat', 'dessert', 'deep', 'bowl')]
        if sum(counts) != _count(statement.group('total')):
            continue
        persons = re.search(r'\bpara\s+(' + count + r')\s+personas\b', statement.group('context'), re.I)
        if persons and any(n != _count(persons.group(1)) for n in counts):
            continue
        expected = [statement.span(role) for role in ('deep_volume', 'bowl_volume')]
        if (len(capacities) == 2 and all(amount > 0 for _, amount in capacities)
                and sorted((m.start(), m.end()) for m, _ in capacities) == sorted(expected)):
            return True
    return False


def compatible_explicit_sets(value: str, capacities: list[tuple[Any, Decimal]]) -> bool:
    """True only when every supplied scalar volume has a complete literal role.

    Call with the entire field's capacity matches, never a subset/group. A
    declared included list does not authorize any unknown extra assertion.
    """
    # A negative inclusion or an example/proportion is not an actual included
    # list. Fail closed for this field rather than borrowing its count/list as
    # proof for a different assertion elsewhere in the same source.
    if re.search(r'\b(?:no|nunca|jam[aá]s)\s+(?:se\s+)?(?:incluye|contiene|incluyen|contienen)\b|'
                 r'\bsin\s+(?:incluir|contener)\b|\bproporci[oó]n\b', value, re.I):
        return False
    if re.search(r'\b(?:ejemplo|hipot[eé]tic[oa]|supongamos|otro producto|otro modelo|otra ficha)\b'
                 r'[^\n.]{0,100}\b(?:juego|set|incluye|contiene|recipientes|vasos|frasco)\b', value, re.I):
        return False
    # The shared numeric lexer has a word boundary before a number, so literal
    # "3x320 ml" can omit 320 from its matches. Read only this explicit count-x
    # syntax locally; otherwise the set's declared count could never be proved.
    capacities = list(capacities)
    positions = {m.start() for m, _ in capacities}
    # A cubic centimetre is exactly one millilitre. Keep this local reader
    # compatible with callers whose old scalar lexer did not yet expose cc.
    for match in re.finditer(r'(?<!\w)(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>cc)\b', value, re.I):
        if match.start() not in positions:
            capacities.append((match, Decimal(match.group('number').replace(',', '.'))))
            positions.add(match.start())
    for match in re.finditer(r'(?<!\d)(?P<number>\d+(?:[.,]\d+)?)\s*'
                             r'(?P<unit>ml|cl|litros?|l)\b', value, re.I):
        prefix = value[max(0, match.start() - 15):match.start()]
        if match.start() not in positions and re.search(r'(?<!\w)[1-9]\d*\s*[x×]\s*$', prefix, re.I):
            factor = Decimal(1) if match.group('unit').lower() == 'ml' else (
                Decimal(10) if match.group('unit').lower() == 'cl' else Decimal(1000))
            capacities.append((match, Decimal(match.group('number').replace(',', '.')) * factor))
    capacities.sort(key=lambda item: item[0].start())
    if len(capacities) < 2:
        return False
    if _named_firstaid_contents(value, capacities) or _counted_tableware(value, capacities):
        return True
    declared = _declared_counts(value, capacities)
    proofs = [(count, series) for count, series in _lists(value, capacities)
              if not declared or declared == {count}]
    for count, series in proofs:
        if len(series) == len(capacities) and (not declared or declared == {count}):
            return True
    if proofs and _named_repeat_list(value, capacities, proofs):
        return True
    if _counted_groups(value, capacities, declared):
        return True
    # Explicitly named main capacity plus an insulated accessory. No total
    # capacity is calculated, and any third assertion prevents this proof.
    if len(capacities) == 2 and not declared:
        (main, _), (accessory, _) = capacities
        if (re.search(r'\bcapacidad\s*$', value[:main.start()], re.I)
                and re.fullmatch(r'\s*\+\s*bolsa de refrigeraci[oó]n aislada de\s*',
                                 value[main.end():accessory.start()], re.I)):
            return True
    return (bool(_INCLUDED.search(value)) and (not declared or declared == {2})
            and _numbered_bottles(value, capacities)) or (
        not declared and _named_box_liquids(value, capacities))
