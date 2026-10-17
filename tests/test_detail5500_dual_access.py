import re
import pytest
from amazon_es_bestseller.access import detector as original
from amazon_es_bestseller.collection.detail_dual_access5500 import detect_access_status

MARKERS=['Captcha','validateCaptcha','errors_page/validateCaptcha','Type the characters',
'Haz clic en el botón de abajo para seguir comprando','resolver el captcha',
'robot check','access denied','unusual traffic','rate exceeded']

@pytest.mark.parametrize('status',[None,200,403,429,503])
@pytest.mark.parametrize('marker',MARKERS)
def test_full_document_detection_matches_original_including_deep_marker(status,marker):
    text='<html>'+('ordinary product facts '*20000)+marker.swapcase()+'</html>'
    assert detect_access_status(status,text)==original.detect_access_status(status,text)

@pytest.mark.parametrize('marker',['robot checK','acceſſ denied','acceſſ denİed','unuſual traffıc',
    'Haz clİc en el botón de abajo para seguir comprando','Type the characterſ'])
def test_unicode_regex_equivalence(marker):
    assert original.detect_access_status(200,marker).value=='CHALLENGE'
    assert detect_access_status(200,marker)==original.detect_access_status(200,marker)

@pytest.mark.parametrize('status',[None,200,403,429,503])
def test_ordinary_product_and_near_misses_preserve_status(status):
    for text in ['',None,'ordinary product '*30000,'access den but no challenge','robot cheque']:
        assert detect_access_status(status,text)==original.detect_access_status(status,text)


def test_future_marker_pattern_falls_back_to_original_full_scan(monkeypatch):
    monkeypatch.setattr(original,'CAPTCHA_RE',re.compile('new-deep-restriction',re.I))
    text='ordinary product '*30000+'new-deep-restriction'
    assert detect_access_status(200,text).value=='CHALLENGE'
