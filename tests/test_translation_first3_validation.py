import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.protection import protect, restore
from amazon_es_bestseller.translation.qa import qa_field


FIXTURES = json.loads((Path(__file__).parent / 'fixtures/translation_first3_validation.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', FIXTURES, ids=lambda c: c['case'])
def test_saved_qwen_candidates_keep_real_errors_and_remove_only_proven_false_alarms(case):
    assert len(case['paid_result_sha256']) == 64
    result = qa_field(protect(case['source'], protected_values=[case['brand']]),
                      case['translated'], case['source'], field=case['field'], brand=case['brand'])
    assert (result['qa_status'] == 'pass') == case['expected_pass'], result['issues']
    if case.get('expected_issue'):
        assert any(x['code'] == case['expected_issue'] for x in result['issues'])


@pytest.mark.parametrize('source,target', [
    ('1set', '1套'),
    ('2 sets', '2套'),
    ('90 g (Paquete de 1)', '90克（1包）'),
    ('Paquete de 1 / Paquete de 1', '1包 / 1包'),
    ('Pack de 2 Unidades / Pack de 2 Unidades', '2件 / 2件'),
    ('10-15 m²/90 g', '10-15平方米/90克'),
    ('1500 Centímetros cúbicos', '1500立方厘米'),
    ('10,25 cubic inches', '10.25立方英寸'),
    ('25 centímetros cuadrados', '25平方厘米'),
    ('10 ° C', '10°C'),
])
def test_typed_package_area_volume_and_temperature_equivalence(source, target):
    assert qa_field(protect(source), target, source)['qa_status'] == 'pass'


@pytest.mark.parametrize('source,target', [
    ('1set', '2套'),
    ('Paquete de 1 / Paquete de 1', '1包'),
    ('Paquete de 1', '1包 / 1包'),
    ('1500 Centímetros cúbicos', '1500厘米 立方米'),
    ('1500 Centímetros cúbicos', '1500立方米'),
    ('1500 Centímetros cúbicos', '1500立方厘米 立方米'),
    ('25 centímetros cuadrados', '25厘米'),
    ('10-15 m²/90 g', '10-15米/90克'),
    ('10 ° C', '20°C'),
])
def test_real_count_dimension_and_temperature_changes_stay_blocked(source, target):
    assert qa_field(protect(source), target, source)['qa_status'] == 'qa_failed'


@pytest.mark.parametrize('source,token', [
    ('INLODGE.L8SK3', 'INLODGE.L8SK3'),
    ('Capacidad: 1500 Centímetros cúbicos', '1500 Centímetros cúbicos'),
    ('Cobertura: 15 m²', '15 m²'),
    ('Área: 25 centímetros cuadrados', '25 centímetros cuadrados'),
    ('Volumen: 12 cm³', '12 cm³'),
    ('Temperatura: 10 ° C', '10 ° C'),
])
def test_complete_technical_identity_or_power_unit_is_one_protected_token(source, token):
    protected = protect(source)
    assert token in protected.tokens.values()
    text, issues = restore(protected, protected.text)
    assert text == source and issues == []


def test_dotted_identity_cannot_be_replaced_with_transliterated_fragments():
    source = 'INLODGE.L8SK3'
    result = qa_field(protect(source), '因洛德吉.8斯克3', source)
    assert any(x['code'] == 'PROTECTED_TOKEN_MISSING' for x in result['issues'])


@pytest.mark.parametrize('source,target', [
    ('sin comprometer la calidad', '不牺牲品质'),
    ('Se pueden mezclar entre sí sin límites', '可任意相互混合'),
    ('nunca almacenar por debajo de 10 ° C', '切勿在10°C以下存放'),
    ('nunca almacenar por encima de 10 ° C', '切勿在10°C以上存放'),
    ('No', '否'),
])
def test_known_negation_preserves_its_specific_meaning(source, target):
    assert qa_field(protect(source), target, source)['qa_status'] == 'pass'


@pytest.mark.parametrize('source,target', [
    ('sin comprometer la calidad', '会牺牲品质，但不含糖'),
    ('Se pueden mezclar entre sí sin límites', '不含酒精，不能相互混合'),
    ('nunca almacenar por debajo de 10 ° C', '切勿在10°C以上存放'),
    ('nunca almacenar por debajo de 10 ° C', '不含糖，可以在10°C以下存放'),
    ('nunca almacenar por encima de 10 ° C', '切勿在10°C以下存放'),
    ('almacenar por debajo de 10 ° C', '切勿在10°C以下存放'),
    ('No', '是'),
    ('No', '不含糖'),
])
def test_an_unrelated_negative_word_cannot_mask_a_reversed_or_missing_constraint(source, target):
    result = qa_field(protect(source), target, source)
    assert any(x['code'] == 'NEGATION_MISMATCH' for x in result['issues'])


@pytest.mark.parametrize('source,target,field,code', [
    ('Tierra de Siena tostada, Tierra de sombra tostada', '熟褐土、熟褐土', 'feature_bullets', 'COLOR_DISTINCTION_COLLAPSED'),
    ('Verde vejiga', '膀胱绿', 'feature_bullets', 'DOMAIN_TERM_REVIEW_REQUIRED'),
    ('90 g / Sketcher', '90克 / 草图绘制器', 'selected_variation_raw', 'MODEL_TERM_REVIEW_REQUIRED'),
    ('Con tratamiento antiadherente', '带防粘涂层', 'feature_bullets', 'UNSUPPORTED_COATING_CLAIM'),
])
def test_reviewed_true_errors_are_explicit_holds_without_guessing_a_replacement(source, target, field, code):
    assert any(x['code'] == code for x in qa_field(protect(source), target, source, field=field)['issues'])


@pytest.mark.parametrize('source,target,field,code', [
    ('Tierra de Siena tostada, Tierra de sombra tostada', '熟赭土、熟褐土', 'feature_bullets', 'COLOR_DISTINCTION_COLLAPSED'),
    ('Tierra de sombra tostada', '熟褐土', 'feature_bullets', 'COLOR_DISTINCTION_COLLAPSED'),
    ('Verde', '绿色', 'feature_bullets', 'DOMAIN_TERM_REVIEW_REQUIRED'),
    ('Sketcher', 'Sketcher', 'selected_variation_raw', 'MODEL_TERM_REVIEW_REQUIRED'),
    ('Sketcher', '草图绘制器', 'description_es', 'MODEL_TERM_REVIEW_REQUIRED'),
    ('Con tratamiento antiadherente', '经不粘处理', 'feature_bullets', 'UNSUPPORTED_COATING_CLAIM'),
    ('Con tratamiento antiadherente y revestimiento cerámico', '带不粘陶瓷涂层', 'feature_bullets', 'UNSUPPORTED_COATING_CLAIM'),
])
def test_known_source_candidate_holds_do_not_expand_to_unrelated_words_or_supported_claims(source, target, field, code):
    assert all(x['code'] != code for x in qa_field(protect(source), target, source, field=field)['issues'])
