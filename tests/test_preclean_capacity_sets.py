import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.capacity_sets import compatible_explicit_sets
from amazon_es_bestseller.translation.preclean import NUMERIC_UNIT_RE, UNIT_CANONICAL


def capacities(text):
    factors = {'ml': Decimal(1), 'cl': Decimal(10), 'L': Decimal(1000)}
    return [(m, Decimal(m.group('number').replace(',', '.')) * factors[UNIT_CANONICAL[m.group('unit').casefold()]])
            for m in NUMERIC_UNIT_RE.finditer(text)
            if UNIT_CANONICAL.get(m.group('unit').casefold()) in factors]


CASES = json.loads((Path(__file__).parent / 'fixtures/preclean_capacity_sets_saved_sources.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=lambda r: str(r['index']) + ':' + r['asin'])
def test_saved_source_sets_are_fully_bound_or_remain_held(case):
    before = copy.deepcopy(case)
    assert len(case['source_evidence']['sha256']) == 64
    assert case['evidence_dom']
    assert compatible_explicit_sets(case['source_text'], capacities(case['source_text'])) is case['expected_supported']
    assert case == before


@pytest.mark.parametrize('text', [
    'Juego de recipientes. Capacidad del recipiente: 350 ml. Capacidad del recipiente: 750 ml.',
    'Incluye 7 recipientes de 320 ml, 800 ml y 240 ml.',
    'El set consta de tres cuencos, con capacidad de 350 ml, 750 ml y 1250 ml. Otra capacidad: 900 ml.',
    'El set consta de tres cuencos, con capacidad de 350 ml, 750 ml y 1250 ml. Capacidad del cuenco: 350 ml.',
    'El set consta de tres cuencos, con capacidad de 350 ml, 750 ml y 1250 ml. El grande tiene 750 ml y el grande tiene 1250 ml.',
    'Conjunto completo de 19 vasos: formado por seis vasos de sidra de 53 cl, seis vasos de pinta de 36 cl y seis vasos de chiquito de 23 cl',
    'Este juego incluye 12 recipientes de vidrio (3x320 ml, 3x520 ml, 3x800 ml, 2x1200 ml).',
    'Este juego incluye 12 recipientes de vidrio (3x320 ml, 3x520 ml, 3x800 ml, 3x1200 ml). Capacidad: 500 ml.',
    'Recipiente grande de 3,8 l y pequeño de 1,4 l. El grande tiene capacidad de 4,2 l.',
    'Recipiente grande de 3,8 l y recipiente pequeño de 1,4 l. Incluye 2 recipientes (1,4 l y 3,8 l). Recipiente grande de 1,4 l.',
    'Capacidad 44l + bolsa de refrigeración aislada de 5 litros. Capacidad: 40 l.',
    'Capacidad 44l + capacidad de 5 litros.',
    'Incluye un frasco 1 de 200 ml y un frasco 1 de 250 ml.',
    'Los envases de 200 ml y 250 ml. Incluye frasco 1 de 200 ml y frasco 2 de 300 ml.',
    'CONTENIDO DE LA CAJA: 500 ml de solución de limpieza, 250 ml de solución de limpieza',
    'CONTENIDO DE LA CAJA: 500 ml de solución antibacteriana, 250 ml de eliminador de olores. Capacidad del depósito: 200 ml.',
    'Juego de 8 tarros de 400 ml/750 ml.',
    'Juego de 7 recipientes. Recipiente grande de 3,8 l y recipiente pequeño de 1,4 l. Incluye 2 recipientes (1,4 l y 3,8 l).',
    'Incluye 3 botellas. Frasco 1 de 200 ml y frasco 2 de 250 ml.',
    'CONTENIDO DE LA CAJA: incluye 3 botellas, 500 ml de solución antibacteriana, 250 ml de eliminador de olores',
    'CONTENIDO DE LA CAJA: 500 ml de solución antibacteriana de limpieza, 250 ml de solución antibacteriana para limpieza',
    'Recipiente pequeño de 3,8 l y recipiente pequeña de 1,4 l. Incluye 2 recipientes (1,4 l y 3,8 l).',
    'Capacidad: recipiente grande de 3800 ml y recipiente pequeño de 1400 ml. NO INCLUYE 2 recipientes de vidrio (1400 ml y 3800 ml)',
    'Capacidad: recipiente grande de 3800 ml y recipiente pequeño de 1400 ml. No contiene 2 recipientes de vidrio (1400 ml y 3800 ml)',
    'Capacidad: recipiente grande de 3800 ml y recipiente pequeño de 1400 ml. Sin incluir un juego de 2 recipientes (1400 ml y 3800 ml)',
    'Por ejemplo, un juego de 3 recipientes de 350 ml, 750 ml y 1250 ml.',
    'Ejemplo de otro producto: "incluye 3 recipientes de 350 ml, 750 ml y 1250 ml".',
    'En una proporción de 5 botellas de salsa de 60 ml, 5 botellas de condimento de 100 ml.',
    'No incluye frasco 1 de 200 ml ni frasco 2 de 250 ml.',
    'CONTENIDO DE LA CAJA: no incluye 500 ml de solución antibacteriana, 250 ml de eliminador de olores',
])
def test_unbound_quantities_count_shortfalls_and_same_object_conflicts_do_not_pass(text):
    assert not compatible_explicit_sets(text, capacities(text))


def test_match_amounts_are_not_mutated():
    text = 'El set consta de tres cuencos, con capacidad de 350 ml, 750 ml y 1250 ml.'
    values = capacities(text)
    before = [(m.start(), m.end(), v) for m, v in values]
    assert compatible_explicit_sets(text, values)
    assert [(m.start(), m.end(), v) for m, v in values] == before


FIRSTAID = ('Contenido incluido: 1 unidad de agua oxigenada 250 cc '
            '1 unidad de alcohol 96º 250 ml 1 unidad de povidona yodada 50 ml '
            '3 unidades de suero fisiológico monodosis 5 ml')
TABLEWARE = ('24 piezas para 6 personas incluye 6 platos llanos, 6 platos de postre, '
             '6 platos hondos (cada uno con capacidad de 1260 ml hasta el borde) y '
             '6 cuencos multiusos (cada uno con capacidad de 840 ml hasta el borde).')


@pytest.mark.parametrize('text', [FIRSTAID, TABLEWARE, FIRSTAID.replace('250 cc', '250 ml'),
                                FIRSTAID.replace('Contenido incluido:', 'Contenido incluido: 6 botellas:')])
def test_direct_named_contents_and_piece_closed_tableware_do_not_invent_other_volumes(text):
    before = str(text)
    assert compatible_explicit_sets(text, capacities(text))
    assert text == before


@pytest.mark.parametrize('text', [
    FIRSTAID + ' Capacidad: 500 ml.',
    FIRSTAID + ' 1 unidad de agua oxigenada 500 cc',
    FIRSTAID + ' 1 unidad de alcohol 96º 500 ml',
    FIRSTAID + ' 1 unidad de jabón líquido 20 ml',
    FIRSTAID.replace('Contenido incluido:', 'Contenido no incluido:'),
    FIRSTAID.replace('1 unidad de alcohol', '0 unidades de alcohol'),
    FIRSTAID.replace('3 unidades de suero fisiológico monodosis 5 ml', ''),
    FIRSTAID.replace('Contenido incluido:', 'Contenido incluido: 7 botellas:'),
    TABLEWARE.replace('24 piezas', '23 piezas'),
    TABLEWARE.replace('6 platos llanos', '5 platos llanos'),
    TABLEWARE.replace('para 6 personas', 'para 8 personas'),
    TABLEWARE + ' Capacidad de los platos hondos: 900 ml.',
    TABLEWARE + ' 500 ml.',
    TABLEWARE.replace('incluye', 'no incluye'),
    TABLEWARE.replace('incluye', 'es un ejemplo que incluye'),
])
def test_named_contents_and_tableware_unknowns_counts_and_same_role_conflicts_stay_held(text):
    assert not compatible_explicit_sets(text, capacities(text))
