"""Offline regression for restored Spanish axes and their faithful Chinese form."""
import json
from pathlib import Path

from amazon_es_bestseller.translation.terminology import postprocess, normalize_unit_display
from amazon_es_bestseller.translation.protection import protect
from amazon_es_bestseller.translation.validators import _units, _fact_numbers


def test_saved_dimension_axis_markers_are_translated_without_losing_roles():
    case=json.loads((Path(__file__).parent/'fixtures/translation/axis_display_B0H8CM59SB.json').read_text(encoding='utf8'))
    assert postprocess('specification_es',case['before'],case['source'])==case['after']


def test_dimension_axis_is_not_an_opaque_technical_unit():
    p=protect('Dimensiones del producto: 6f. x 10an. x 2al. centímetros')
    assert '6f.' not in p.tokens.values()
    assert protect('Número modelo: 6f.').tokens


def test_chinese_axis_names_do_not_create_false_unit_or_number_mismatches():
    source='6f. x 10an. x 2al. centímetros'
    for target in ['深6×宽10×高2厘米','6深×10宽×2高厘米','6深×10宽×2高 cm','6×10×2厘米（深×宽×高）']:
        assert _units(source)==_units(target)
        assert sorted(_fact_numbers(source))==sorted(_fact_numbers(target))


def test_axis_translation_does_not_repair_wrong_numbers_or_touch_identity():
    s='Dimensiones del producto: 6f. x 10an. x 2al. centímetros'
    wrong='产品尺寸：7f. x 10an. x 2al. 厘米'
    assert postprocess('specification_es',wrong,s)==wrong
    model='型号：6f. x 10an. x 2al. 厘米'
    assert postprocess('product_details_es',model,'Número modelo: 6f. x 10an. x 2al. centímetros')==model
    assert postprocess('title_es_raw','6f. x 10an. x 2al. 厘米',s)=='6f. x 10an. x 2al. 厘米'


def test_axis_words_without_a_length_unit_cannot_invent_centimetres():
    assert normalize_unit_display('厚度：3 Grosor / 5f.')=='厚度：3 Grosor / 5f.'
    source='Dimensiones: 6f. x 10an. x 2al.'
    assert postprocess('specification_es','6f. x 10an. x 2al.',source)=='6f. x 10an. x 2al.'
    for raw in ['3Grosor','5f.','8an.','4al.','2l.']:
        assert _units(raw)==[]


def test_shared_millimetres_and_axis_order_are_preserved():
    source='Dimensiones: 2al. x 6f. x 10an. milímetros'
    assert postprocess('product_details_es','尺寸：2al. x 6f. x 10an. mm',source)=='尺寸：2×6×10毫米（高×深×宽）'
