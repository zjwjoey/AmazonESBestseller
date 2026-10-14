from pathlib import Path
import json
import pytest
from bs4 import BeautifulSoup
from amazon_es_bestseller.highspeed.ratings import extract_rating_fields, validate_rating_fields

FIX = Path(__file__).parent / 'fixtures' / 'highspeed'

def extract(card, raw=None, **kw):
    if isinstance(card,str): card=BeautifulSoup(card,'lxml').select_one('.ranking-card')
    raw=raw or card.select_one('a[href]')['href']
    return extract_rating_fields(card,raw_product_url=raw,evidence_path='saved.html',evidence_sha256='a'*64,observed_at='2026-10-14T01:00:00Z',representation='SERVER',representation_slot=0,**kw)

@pytest.mark.parametrize('kind,value,count',[('best_sellers',4.6,48704),('new_releases',5.0,11)])
def test_real_saved_card(kind,value,count):
    card=BeautifulSoup((FIX/f'real_v3_rating_{kind}.html').read_text(encoding='utf8'),'lxml').body.find(recursive=False)
    p=json.loads((FIX/f'real_v3_rating_{kind}.provenance.json').read_text(encoding='utf8'))
    fields=extract(card,p['raw_product_url'])
    assert fields['rating_value']==value
    assert fields['review_count']==count
    assert fields['rating_status']==fields['review_count_status']=='VALID'
    assert validate_rating_fields(fields,card)

def test_no_rating_not_zero():
    fields=extract('<div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="/dp/B077T9VSNZ">Product</a></div>')
    assert fields['rating_status']=='NOT_DISPLAYED' and fields['rating_value'] is None

def test_wrong_card_href_unverified():
    fields=extract('<div class="ranking-card"><a href="/dp/B077T9VSNZ">Product</a><span class="a-icon-alt">4,5 de 5 estrellas</span></div>',raw='/dp/B012345678')
    assert fields['rating_status']=='UNVERIFIED'

def synthetic(content,href='/dp/B077T9VSNZ'):
    return '<div class="ranking-card"><span class="zg-bdg-text">#1</span><a href="'+href+'">Product</a>'+content+'</div>'

@pytest.mark.parametrize('raw,expected',[('1.234',1234),('12 345',12345),('12\u202f345',12345),('0',0),('1,234',None),('12.34',None),('1.234.567',1234567)])
def test_count_formats_are_not_guessed(raw,expected):
    row=extract(synthetic('<span class="review-count">'+raw+'</span>'))
    assert row['review_count']==expected
    assert row['review_count_status']==('VALID' if expected is not None else 'PARSE_FAILED')

@pytest.mark.parametrize('raw,status,value',[('4,7 de 5 estrellas','VALID',4.7),('4.7 out of 5 stars','VALID',4.7),('6,0 de 5 estrellas','PARSE_FAILED',None),('5 estrellas 80%','PARSE_FAILED',None),('cuatro de 5 estrellas','PARSE_FAILED',None)])
def test_explicit_rating_labels(raw,status,value):
    row=extract(synthetic('<span class="a-icon-alt">'+raw+'</span>'))
    # Percentage distributions are excluded, not consumer average ratings.
    if '%' in raw:status='NOT_DISPLAYED'
    assert row['rating_status']==status and row['rating_value']==value

def test_rating_conflict_keeps_all_raw_evidence():
    row=extract(synthetic('<span class="a-icon-alt">4,2 de 5 estrellas</span><span class="a-icon-alt">4,8 de 5 estrellas</span>'))
    assert row['rating_status']=='SOURCE_CONFLICT' and row['rating_value'] is None
    assert len(row['rating_evidence'])==2

def test_foreign_review_target_is_unverified():
    row=extract(synthetic('<a href="/product-reviews/B012345678" aria-label="4,7 de 5 estrellas, 20 calificaciones"><span class="a-icon-alt">4,7 de 5 estrellas</span><span class="a-size-small">20</span></a>'))
    assert row['rating_status']==row['review_count_status']=='UNVERIFIED'
    assert row['rating_value'] is None

def test_nested_card_and_ad_cannot_pollute_rating():
    row=extract(synthetic('<div class="ranking-card"><a href="/dp/B012345678"><span class="a-icon-alt">4,9 de 5 estrellas</span></a></div><div class="sponsored"><span class="a-icon-alt">5,0 de 5 estrellas</span></div>'))
    assert row['rating_status']=='NOT_DISPLAYED'

def test_no_rank_is_unverified_even_with_label():
    row=extract('<div class="ranking-card"><a href="/dp/B077T9VSNZ">Product</a><span class="a-icon-alt">4,5 de 5 estrellas</span></div>')
    assert row['rating_status']=='UNVERIFIED'

def test_rating_without_reviews_and_acp():
    card=BeautifulSoup(synthetic('<span class="a-icon-alt">4,7 de 5 estrellas</span>'),'lxml').select_one('.ranking-card')
    row=extract_rating_fields(card,raw_product_url='/dp/B077T9VSNZ',evidence_path='acp.html',evidence_sha256='b'*64,observed_at='2026-10-14T01:00:00Z',representation='ACP_0',representation_slot=0)
    assert row['rating_value']==4.7 and row['review_count_status']=='NOT_DISPLAYED'
    assert row['rating_evidence'][0]['representation']=='ACP_0'
    assert validate_rating_fields(row,card)
    row['rating_value']=5.0
    assert not validate_rating_fields(row,card)

def test_title_aria_label_is_not_rating():
    row=extract(synthetic('<a href="/dp/B077T9VSNZ" aria-label="Libro 5 estrellas">Book</a>'))
    assert row['rating_status']=='NOT_DISPLAYED'

def test_parent_container_cannot_be_card():
    doc=BeautifulSoup('<div id="zg">'+synthetic('<span class="a-icon-alt">4,5 de 5 estrellas</span>')+'</div>','lxml')
    row=extract(doc.select_one('#zg'),'/dp/B077T9VSNZ')
    assert row['rating_status']=='UNVERIFIED'

def test_negative_rating_not_positive():
    row=extract(synthetic('<span class="a-icon-alt">-4,5 de 5 estrellas</span>'))
    assert row['rating_status']=='PARSE_FAILED' and row['rating_value'] is None

def test_malformed_visible_count_is_parse_failed():
    row=extract(synthetic('<a href="/product-reviews/B077T9VSNZ" aria-label="4,5 de 5 estrellas, muchas calificaciones"><span class="a-icon-alt">4,5 de 5 estrellas</span></a>'))
    assert row['rating_value']==4.5 and row['review_count_status']=='PARSE_FAILED'

def test_real_acp_capture():
    p=json.loads((FIX/'real_v3_rating_acp.provenance.json').read_text(encoding='utf8'))
    card=BeautifulSoup((FIX/'real_v3_rating_acp.html').read_text(encoding='utf8'),'lxml').body.find(recursive=False)
    row=extract_rating_fields(card,raw_product_url=p['raw_product_url'],evidence_path=p['evidence_path'],evidence_sha256=p['evidence_sha256'],observed_at=p['observed_at'],representation=p['representation'],representation_slot=p['representation_slot'])
    assert row['rating_status']=='VALID' and row['review_count_status']=='VALID'
    assert validate_rating_fields(row,card)

def test_ranked_ad_card_unverified():
    row=extract(synthetic('<span class="a-icon-alt">5,0 de 5 estrellas</span>').replace('class="ranking-card"','class="ranking-card sponsored"'))
    assert row['rating_status']=='UNVERIFIED'

def test_review_count_conflict_preserved():
    row=extract(synthetic('<span class="review-count">1.234</span><span class="review-count">1.345</span>'))
    assert row['review_count_status']=='SOURCE_CONFLICT' and row['review_count'] is None
    assert len(row['review_count_evidence'])==2

def test_review_count_cannot_become_rating():
    row=extract(synthetic('<span class="review-count">5</span>'))
    assert row['review_count']==5 and row['rating_value'] is None and row['rating_status']=='NOT_DISPLAYED'

@pytest.mark.parametrize('time',['2026-10-14T01:00:00',None,'invalid'])
def test_unbound_time_unverified(time):
    card=BeautifulSoup(synthetic('<span class="a-icon-alt">4,7 de 5 estrellas</span>'),'lxml').select_one('.ranking-card')
    row=extract_rating_fields(card,raw_product_url='/dp/B077T9VSNZ',evidence_path='saved.html',evidence_sha256='a'*64,observed_at=time,representation='SERVER',representation_slot=0)
    assert row['rating_status']=='UNVERIFIED'

@pytest.mark.parametrize('stem',['best_sellers','new_releases','acp'])
def test_real_fixture_has_immutable_provenance(stem):
    import hashlib
    path=FIX/f'real_v3_rating_{stem}.html'
    provenance=json.loads(path.with_suffix('.provenance.json').read_text(encoding='utf8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest()==provenance['fixture_sha256']
    assert len(provenance.get('original_evidence_sha256',provenance.get('evidence_sha256')))==64


def test_negative_review_text_not_positive():
    row=extract(synthetic('<a href="/product-reviews/B077T9VSNZ" aria-label="4,5 de 5 estrellas, -20 calificaciones"><span class="a-icon-alt">4,5 de 5 estrellas</span></a>'))
    assert row['review_count_status']=='PARSE_FAILED' and row['review_count'] is None
