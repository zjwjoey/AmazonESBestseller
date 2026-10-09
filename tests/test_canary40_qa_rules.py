import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.protection import protect
from amazon_es_bestseller.translation.qa import qa_field


PAIRS=json.loads((Path(__file__).parent/'fixtures/translation_canary40_qa_pairs.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('pair',PAIRS,ids=lambda pair:pair['canonical_key'][:12])
def test_saved_canary_false_positive(pair):
    qa=qa_field(protect(pair['source'],protected_values=[pair['asin'],pair['brand']]),
        pair['rendered'],pair['source'],field=pair['field'],brand=pair['brand'],allowed_residual=pair['allowed_residual'])
    assert qa['qa_status']=='pass',qa['issues']


@pytest.mark.parametrize('source,target',[
    ('9L','25.4升'),('30L','20升'),('10×15cm','10×10毫米'),
    ('Libre de BPA','含BPA'),('Libre de BPA','含BPA，无需安装'),
    ('Marco de PVC, Color Madera','相框，木色'),
    ('Marco de PVC, Color Madera','PVC相框，材质：木材'),
    ('Marco de PVC, Color Madera','PVC相框，材质：木色'),
    ('24,5l. x 58,5an. x 90,5al. centímetros','24.5厘米（长）×90.5厘米（宽）×58.5厘米（高）'),
    ('100f. x 21an. x 70al. milímetros','100厘米 ×21厘米×70厘米 毫米'),
    ('30,4l. x 21an. centímetros','30.4升 ×21厘米'),
    ('60 000 horas','60小时'),
    ('modelo KH-7','型号KH-8'),
])
def test_fact_errors_remain_blocked(source,target):
    assert qa_field(protect(source),target,source)['qa_status']=='qa_failed'


def test_explicit_bpa_absence_and_wood_color_are_preserved():
    source='Marco de PVC, Color Madera, libre de BPA'
    assert qa_field(protect(source),'PVC相框，木色，不含BPA',source)['qa_status']=='pass'


@pytest.mark.parametrize('source,target',[
    ('USB cargador 5 V/2 A','USB充电器5V/2 A'),
    ('30 colores diferentes (9 neón, 14 colores pastel, 6 colores naturales y 1 marcador negro)',
     '30种不同颜色（9种霓虹色、14种柔和色、6种自然色和1支黑色记号笔）'),
])
def test_saved_count_and_electrical_not_model_false_positives(source,target):
    assert qa_field(protect(source),target,source)['qa_status']=='pass'


@pytest.mark.parametrize('source,target',[
    ('9,9an. x 24al. centímetros','9.9英寸 × 24厘米'),
    ('30,5 cm (L) x 44,5 cm (H) x 20,3 cm (P)',
     '30.5厘米（长）×44.5厘米（高）×20.3厘米（宽）'),
])
def test_newly_discovered_real_dimension_errors_remain_blocked(source,target):
    assert qa_field(protect(source),target,source)['qa_status']=='qa_failed'
