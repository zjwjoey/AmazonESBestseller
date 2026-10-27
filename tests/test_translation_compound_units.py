import pytest

from amazon_es_bestseller.translation.protection import protect, restore
from amazon_es_bestseller.translation.terminology import normalize_unit_display, postprocess


@pytest.mark.parametrize('source, expected', [
    ('1500 Centímetros cúbicos', '1500立方厘米'),
    ('1500 centímetros   cúbicos', '1500立方厘米'),
    ('10.25 pulgadas cúbicas', '10.25立方英寸'),
    ('10.25 cubic inches', '10.25立方英寸'),
    ('10-15m²/90g', '10-15平方米/90g'),
    ('9 cm³', '9立方厘米'),
    ('9 m³', '9立方米'),
    ('9 mm³', '9立方毫米'),
    ('9 cm', '9cm'),
    ('260 Grados Celsius', '260摄氏度'),
])
def test_complete_unit_display(source, expected):
    assert normalize_unit_display(source) == expected


def test_compound_unit_is_normalized_before_word_terms():
    assert postprocess('product_details', '1500 Centímetros cúbicos',
                       '1500 Centímetros cúbicos') == '1500立方厘米'


def test_saved_watercolour_volume_survives_provider_token_roundtrip():
    source = '1500 Centímetros cúbicos'
    protected = protect(source)
    assert list(protected.tokens.values()) == [source]
    restored, issues = restore(protected, protected.text)
    assert not issues
    assert normalize_unit_display(restored) == '1500立方厘米'
