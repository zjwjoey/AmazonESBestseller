import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.protection import protect
from amazon_es_bestseller.translation.qa import qa_field


@pytest.mark.parametrize('pair', json.loads(
    (Path(__file__).parent / 'fixtures/translation_increment110_numeric_boundary.json')
    .read_text(encoding='utf-8')))
def test_saved_actual_response_pair(pair):
    source = pair['source_es']
    assert qa_field(protect(source), pair['translation_zh'], source)['qa_status'] == 'pass'


@pytest.mark.parametrize('target', ['25 x 125ml', '25 × 125毫升'])
def test_saved_multipack_volume_spacing_preserves_complete_number(target):
    source = '25 x 125 ml'
    assert qa_field(protect(source), target, source)['qa_status'] == 'pass'


@pytest.mark.parametrize('source,target', [
    ('25 x 125 ml', '25 x 12ml'),
    ('25 x 125 ml', '25 x 125厘米'),
    ('modelo KH-7', '型号KH-8'),
    ('Libre de BPA', '含BPA'),
    ('Marco de PVC, Color Madera', '材质：木头'),
])
def test_fact_changes_remain_blocked(source, target):
    assert qa_field(protect(source), target, source)['qa_status'] == 'qa_failed'
