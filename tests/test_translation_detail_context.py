import pytest

from amazon_es_bestseller.translation.dictionary_service import DictionaryService
from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.service import TranslationService
from amazon_es_bestseller.translation.providers.base import ProviderResponse, TranslationProvider


@pytest.mark.parametrize('field,source,expected', [
    ('Tipo Acabado', 'Uniforme', '均匀'),
    ('Tipo de acabado', 'Con tratamiento antiadherente', '经防粘处理'),
    ('Descripción Revestimiento', 'Con tratamiento antiadherente', '经防粘处理'),
    ('¿Es impermeable?', 'Falso', '否'),
    ('¿Es seguro para el horno?', 'Verdadero', '是'),
    ('Uso en interiores o exteriores', 'Interior', '室内'),
    ('Uso en interiores o exteriores', 'Exterior', '室外'),
    ('Tipo de ropa', 'Uniforme', None),
    ('Descripción', 'Falso', None),
    ('Material del interior', 'Interior', None),
    ('Tipo Acabado', 'Uniforme brillante', None),
    ('¿Es impermeable?', 'Falso, según el vendedor', None),
])
def test_exact_context_values_preserve_roles(field, source, expected):
    assert DictionaryService().lookup_value(source, field=field) == expected


class EchoProvider(TranslationProvider):
    name = 'test'
    model = 'test'
    def translate(self, text, **kwargs):
        return ProviderResponse(text='蓝色', provider=self.name, model=self.model,
                                status='success', attempts=1)


def test_unknown_label_is_preserved_and_blocked(tmp_path):
    service = TranslationService(EchoProvider(), TranslationCache(tmp_path/'cache.json'))
    result = service.translate_records([{'asin':'B00004OCLC', 'product_details':[
        {'label_raw':'Etiqueta completamente nueva', 'value_raw':'Azul'},
    ]}])['records']['B00004OCLC']['fields']['product_details_zh']
    assert result['translation_status'] != 'success'
    assert result['qa_status'] == 'qa_failed'
    assert result['translated_text'] == 'Etiqueta completamente nueva：蓝色'
    assert any(i['code'] == 'UNTRANSLATED_DETAIL_LABEL' for i in result['qa_issues'])


@pytest.mark.parametrize('label', ['ASIN', '型号', 'Tipo de pintura', 'Código de color'])
def test_known_or_chinese_labels_do_not_raise_unknown_label(tmp_path, label):
    service = TranslationService(EchoProvider(), TranslationCache(tmp_path/'cache.json'))
    result = service.translate_records([{'asin':'B00004OCLC', 'product_details':[
        {'label_raw':label, 'value_raw':'Azul'},
    ]}])['records']['B00004OCLC']['fields']['product_details_zh']
    assert not any(i['code']=='UNTRANSLATED_DETAIL_LABEL' for i in result['qa_issues'])
