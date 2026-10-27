"""Literal quantity contexts, without rewriting or filling any source fact.

Every exclusion binds an individual volume occurrence to an explicit use/test
statement. An unbound quantity remains in the conservative capacity comparison.
"""
from decimal import Decimal
import hashlib
import re


_V = r"\d+(?:[.,]\d+)?\s*(?:mililitros?|centilitros?|litros?|ml|cl|l)(?![\w/])"
_N = r"(?:\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
_COUNTS = {s: i for i, s in enumerate(('uno','dos','tres','cuatro','cinco','seis',
                                     'siete','ocho','nueve','diez','once','doce'), 1)}
_COUNTS.update(un=1, una=1)


def scenario_exclusions(value, capacities):
    marked = {}
    bounded_doses = []

    def add(match, role, groups):
        positions = []
        for group in groups:
            start, end = match.span(group)
            for volume, amount in capacities:
                if start <= volume.start() and volume.end() <= end:
                    positions.append((volume.start(), amount))
                    marked[volume.start()] = {'start': volume.start(), 'end': volume.end(),
                        'literal': volume.group(0), 'role': role,
                        'evidence_text': match.group(0)}
        return positions

    def rule(pattern, role, groups):
        for match in re.finditer(pattern, value, re.I):
            positions = add(match, role, groups)
            if role in {'boiling_energy_comparison', 'battery_serving_test'}:
                bounded_doses.extend((pos, amount, False) for pos, amount in positions)

    # Energy comparisons refer to the explicit comparison operation, not to
    # the product's reservoir. Restrict both ends rather than an entire line.
    rule(r"(?:al\s+)?hervir\s+un\s+vaso\s+de\s+agua\s*\(\s*(?P<a>"+_V+
         r")\s*\)\s+en\s+comparaci[oó]n\s+a\s*(?P<b>"+_V+r")",
         'boiling_energy_comparison', ('a','b'))
    rule(r"1\s+taza\s*=\s*(?P<a>"+_V+r")\s+frente\s+a\s*(?P<b>"+_V+r")",
         'boiling_energy_comparison', ('a','b'))
    for match in re.finditer(
        r"\b(?:hierve|hervir|calentar)\s+(?:un\s+vaso\s+de\s+agua\s*\(\s*)?"
        r"(?P<dose>"+_V+r")\s*\)?(?:\s+de\s+agua)?[^\n;]{0,170}?\ben\s+"
        r"(?:tan\s+s[oó]lo\s+)?\d+(?:[.,]\d+)?\s*(?:segundos|minutos)\b", value, re.I):
        positions = add(match, 'timed_heating_dose', ('dose',))
        bounded_doses.extend((pos, amount, False) for pos, amount in positions)

    rule(r"(?:vs\.?|frente\s+a)\s+(?:un\s+)?hornos?\s+de\s*(?P<a>"+_V+
         r")\s+(?:con\s+clasificaci[oó]n|de\s+clase)\s*[\"“]?A\+?[\"”]?\s+y\s*"
         r"(?P<b>"+_V+r")\s+(?:con\s+clasificaci[oó]n|de\s+clase)\s*[\"“]?A\+?",
         'other_oven_comparison', ('a','b'))
    rule(r"\bfiltra\s+hasta\s*(?P<v>"+_V+r")\s+en\s+\d+\s+semanas\s+con\s+cada\s+cartucho\b",
         'cartridge_throughput_over_time', ('v',))
    rule(r"\bcompatibles\s+con\s+los\s+tamaños\s+de\s*(?P<a>"+_V+
         r")\s+y\s*(?P<b>"+_V+r")\s+de\s+la\s+familia\b",
         'other_family_sizes', ('a','b'))
    rule(r"\belige\s+los\s+vasos\s+[^\n;\d]{1,35}\s+de\s*(?P<a>"+_V+
         r")\s*\([^)]{0,15}\)\s+y\s+para\s+bebidas\s+grandes\s+los\s+de\s*(?P<b>"+_V+r")",
         'other_cup_size_advice', ('a','b'))
    rule(r"\bincluye\s+soluci[oó]n\s+de\s+limpieza\s+de\s+muestra\s+de\s*(?P<v>"+_V+r")",
         'included_cleaner_sample', ('v',))
    rule(r"\btambi[eé]n\s+disponible\s+para\s+la\s+venta\s+el\s+formato\s+de\s*(?P<v>"+_V+r")",
         'separately_sold_refill', ('v',))

    # Dilution table: retain the whole literal table, and demand exact same
    # water-per-roll arithmetic before treating its rows as usage scenarios.
    for line in value.splitlines():
        if not re.search(r"\bdiluir\b[^\n]{0,70}\bagua\b", line, re.I):
            continue
        entries = list(re.finditer(r"(?P<n>\d+)\s+rollos?\s*[-–]\s*(?P<v>"+_V+r")", line, re.I))
        base = value.find(line)
        rates = []
        spans = []
        for entry in entries:
            count = int(entry.group('n'))
            for volume, amount in capacities:
                if base+entry.start('v') == volume.start() and count:
                    rates.append(amount/count)
                    spans.append((volume,entry))
        if len(entries) >= 2 and len(rates)==len(entries) and len(set(rates))==1:
            for volume,entry in spans:
                marked[volume.start()]={'start':volume.start(),'end':volume.end(),'literal':volume.group(0),
                    'role':'dilution_per_roll','evidence_text':entry.group(0)}
    rule(r"m[aá]s\s+l[ií]quida,?\s+mezclando\s*\(\s*(?P<a>"+_V+
         r")\s+de\s+agua\s+aproximadamente\)[^\n;]{0,160}\bo\s+m[aá]s\s+densa\s*\(\s*"
         r"(?P<b>"+_V+r")\s+de\s+agua\s+aproximadamente\)",
         'separate_wallpaper_mixing_scenarios', ('a','b'))
    # General size-selection advice must be introduced as such. A random
    # volume range is not an external suggestion.
    for intro in re.finditer(r"Elige\s+el\s+tamaño\s+correcto\s+para\s+la\s+taza\s+que\s+buscas:\s*([^\n]+)", value, re.I):
        for entry in re.finditer(r"(?:las\s+tazas|las)\s+de\s+\d+\s+a\s*(?P<v>"+_V+r")\s+para\s+caf[eé]", intro.group(1), re.I):
            start=intro.start(1)+entry.start('v');end=intro.start(1)+entry.end('v')
            for volume,amount in capacities:
                if start <= volume.start() and volume.end() <= end:
                    marked[volume.start()]={'start':volume.start(),'end':volume.end(),'literal':volume.group(0),
                        'role':'general_coffee_size_advice','evidence_text':entry.group(0)}

    # Battery endurance is an explicitly repeated-dose test, not a reservoir.
    rule(r"una\s+sola\s+carga\s+completa\s+permite\s+preparar\s+(?:unas\s+)?\d+\s+tazas\s*"
         r"\(\s*(?P<a>"+_V+r")\s*\)\s+o\s+\d+\s+tazas\s*\(\s*(?P<b>"+_V+r")\s*\)",
         'battery_serving_test', ('a','b'))
    # Ordinary serving/can counts require an actual remaining nominal volume.
    for match in re.finditer(r"(?P<n>"+_N+r")\s+(?:tazas?(?:\s+de\s+t[eé])?|latas?(?:\s+de\s+bebidas)?)"
                             r"(?:\s+grandes?)?\s*(?:de\s*)?\(?\s*(?P<v>"+_V+r")", value, re.I):
        if match.start('v') in marked:
            continue
        count=_COUNTS.get(match.group('n').lower())
        if count is None:count=int(match.group('n'))
        if count <= 0:
            continue
        positions=add(match,'serving_or_can_volume',('v',))
        bounded_doses.extend((pos,amount*count,True) for pos,amount in positions)
    for match in re.finditer(r"\bo\s+(?P<n>"+_N+r")\s+pequeñas\s*\(\s*(?P<v>"+_V+r")\s*\)",value,re.I):
        if match.start('v') in marked:
            continue
        count=_COUNTS.get(match.group('n').lower())
        if count is None:count=int(match.group('n'))
        if count <= 0:
            continue
        positions=add(match,'small_cup_serving_volume',('v',))
        bounded_doses.extend((pos,amount*count,True) for pos,amount in positions)

    # A recipe amount can be bounded only by a literally named main container.
    mains=[]
    for match in re.finditer(r"\brecipiente\s+principal\s+de\s*(?P<v>"+_V+r")",value,re.I):
        mains.extend(amount for volume,amount in capacities if volume.start()==match.start('v'))
    if len(set(mains))==1:
        for match in re.finditer(r"\bpara\s+\d+\s*g\s+de\s+copos\s+de\s+avena,\s*(?P<v>"+_V+r")\s+de\s+leche\s+y\b",value,re.I):
            doses=[amount for volume,amount in capacities if volume.start()==match.start('v')]
            if doses and doses[0]<=mains[0]:add(match,'explicit_oats_recipe_dose',('v',))

    remaining={amount for volume,amount in capacities if volume.start() not in marked}
    nominal=next(iter(remaining)) if len(remaining)==1 else None
    for pos,dose,needs_nominal in bounded_doses:
        if pos in marked and (nominal is None and needs_nominal or nominal is not None and dose>nominal):
            marked.pop(pos)
    return [marked[pos] for pos in sorted(marked)]


def compatible_reservoir_modes(value, capacities):
    for pattern in (
        r"\bcapacidad\s*:\s*(?P<upper>"+_V+r")\s+de\s+agua\s+y\s*(?P<use>"+_V+r")\s+de\s+t[eé]\b",
        r"\bcapacidad\s+total\s+de\s*(?P<upper>"+_V+r")\s+para\s*(?P<use>"+_V+r")\s+de\s+agua\s+filtrada\b",
    ):
        for match in re.finditer(pattern,value,re.I):
            bound={}
            for role in ('upper','use'):
                bound[role]=[(volume,amount) for volume,amount in capacities
                             if match.start(role)<=volume.start() and volume.end()<=match.end(role)]
            if len(capacities)==2 and all(len(bound[r])==1 for r in bound):
                if bound['use'][0][1]<=bound['upper'][0][1]:return True
    return False


def title_bound_components(value, capacities, source_title):
    """Bind a complete two-part restatement to explicit source-title roles."""
    if len(capacities) != 2 or not re.search(r'\bdoble\s+(?:compartimento|capa)\b', value, re.I):
        return None
    title = str(source_title or '')
    negative_component = r'\b(?:no\s+(?:incluye|tiene|contiene|es)|sin)\b[^\n.;]{0,60}\b(?:compartimento|doble\s+capa|vaso|tarro|taza)\b'
    if re.search(negative_component, title, re.I) or re.search(negative_component, value, re.I):
        return None
    if re.search(r'\b(?:ambos|los\s+dos)\s+(?:valores|vol[uú]menes|capacidades)\b[^\n.;]{0,100}'
                 r'\b(?:mismo|[uú]nico)\s+(?:recipiente|vaso|tarro|compartimento)\b', value, re.I):
        return None
    if len(list(re.finditer(_V, title, re.I))) != 2:
        return None
    declaration = re.search(r'\b(?:vaso|tarro|taza)\b[^\d\n]{0,55}(?P<main>'+_V+
                            r')\s+pack\s+de\s+[1-9]\d*\s+con\s+compartimento\s+(?P<part>'+_V+r')', title, re.I)
    if declaration is None:
        return None
    pair = re.search(r'\bcapacidad\s*(?:de|:)?\s*(?P<main>'+_V+r')\s*\+\s*(?P<part>'+_V+r')', value, re.I)
    if pair is None:
        return None
    def amount(text):
        number, unit = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*([A-Za-z]+)', text).groups()
        # A three-digit decimal/grouping spelling is not locale-resolved here.
        if re.search(r'[1-9]\d*[.,]\d{3}(?!\d)', number):
            return None
        factor = Decimal(1) if unit.lower().startswith('m') else (
            Decimal(10) if unit.lower()=='cl' or unit.lower().startswith('centi') else Decimal(1000))
        return Decimal(number.replace(',','.'))*factor
    roles = []
    for i, role in enumerate(('main','part')):
        volume, number = capacities[i]
        declared = amount(declaration.group(role))
        if declared is None or number != declared or not (pair.start(role)<=volume.start() and volume.end()<=pair.end(role)):
            return None
        roles.append({'start':volume.start(),'literal':volume.group(0),'role':role})
    return {'title_source_text':title,'title_source_hash':hashlib.sha256(title.encode('utf-8')).hexdigest(),
            'body_literal':pair.group(0),'roles':roles,'inferred_total':False}
