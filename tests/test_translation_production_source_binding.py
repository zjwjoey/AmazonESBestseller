import copy
import hashlib
import json

import pytest

from amazon_es_bestseller.translation.preclean import audit_records
from amazon_es_bestseller.translation.production import (
    build_production_input, build_production_state, merge_translation_shards, records_for_preclean,
)
from amazon_es_bestseller.translation.production_contract import target_field_for


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def prepare(record):
    production = build_production_input([record], run_id='source-binding-offline')
    prepared = audit_records(records_for_preclean(production))['translation_input_records']
    return production, prepared


def translated(prepared, field, *, text='商品 1'):
    value = prepared[0]['fields'][field]['clean_text']
    return {prepared[0]['asin']: {'asin': prepared[0]['asin'], 'fields': {
        target_field_for(field): {
            'source_text': value, 'source_hash': digest(value),
            'translated_text': text, 'candidate_text': text,
            'translation_status': 'success', 'qa_status': 'pass', 'qa_issues': [],
        }}}}


def field_state(production, prepared, results, field):
    state = build_production_state(production, prepared, results)
    return next(row for row in state['records'][0]['fields'] if row['field'] == field)


# Public saved-page excerpt: B00004OCLC; no notes, prices or local task state.
SAVED_DETAILS = {
    'asin': 'B00004OCLC',
    'product_details': 'Descatalogado por el fabricante: No\n'
                       'Dimensiones del producto: 17,15 x 3,51 x 2,54 cm; 90 g',
    'attributes': [
        {'label_raw': 'Descatalogado por el fabricante ‏', 'value_raw': '‎ No',
         'section': 'detail_bullets', 'position': 0},
        {'label_raw': 'Dimensiones del producto ‏',
         'value_raw': '‎ 17,15 x 3,51 x 2,54 cm; 90 g',
         'section': 'detail_bullets', 'position': 1},
    ],
}


def test_saved_flat_details_are_not_replaced_by_lower_priority_structured_evidence():
    record = copy.deepcopy(SAVED_DETAILS)
    before = copy.deepcopy(record)
    production, prepared = prepare(record)
    manifest_field = production['records'][0]['fields']['product_details']
    assert prepared[0]['fields']['product_details']['source_text'] == manifest_field['source_text']
    assert prepared[0]['raw_fields']['product_details']['source_hash'] == manifest_field['source_hash']
    assert record == before


def test_selected_raw_list_beats_differing_derived_alias_and_keeps_structure():
    record = {'asin': 'B000000001',
              'detail_attributes_raw': [{'label_raw': 'Color', 'value_raw': 'Rojo'}],
              'product_details': 'Color: Azul',
              'attributes': [{'label_raw': 'Color', 'value_raw': 'Verde'}]}
    production, prepared = prepare(record)
    assert prepared[0]['raw_fields']['product_details']['source_hash'] == production['records'][0]['fields']['product_details']['source_hash']
    assert [(row['label'], row['value']) for row in prepared[0]['details_structured']] == [('Color', 'Rojo')]
    state = field_state(production, prepared, translated(prepared, 'product_details', text='颜色: 红色'), 'product_details')
    assert state['promotion_status'] == 'PROMOTED'


@pytest.mark.parametrize('field,value', [
    ('title_es_raw', '  <b>Producto 1</b> Ver más  '),
    ('title_es_raw', 'Producto \u200b1'),
    ('feature_bullets', ['Sin alcohol: No', 'Sin alcohol: No']),
    ('product_details', 'Color: Rojo\nColor: Rojo'),
    ('product_details', [{'label_raw': 'Color', 'value_raw': 'Rojo'}]),
    ('product_details', {'Color': 'Rojo', 'Modelo': 'ABC'}),
])
def test_verified_clean_input_hash_promotes_without_overwriting_raw_hash(field, value):
    production, prepared = prepare({'asin': 'B000000001', field: value})
    before = copy.deepcopy((production, prepared))
    results = translated(prepared, field)
    state = field_state(production, prepared, results, field)
    assert state['promotion_status'] == 'PROMOTED'
    assert state['source_hash'] == production['records'][0]['fields'][field]['source_hash']
    assert state['source_text'] == production['records'][0]['fields'][field]['source_text']
    assert state['translation_input_hash'] == results['B000000001']['fields'][target_field_for(field)]['source_hash']
    assert (production, prepared) == before


def test_structured_colon_hint_preserves_selected_source_without_becoming_replacement():
    record = {'asin': 'B000000001', 'product_details': 'Potencia: salida: 20 W',
              'attributes': [{'label_raw': 'Potencia: salida', 'value_raw': '20 W'}]}
    production, prepared = prepare(record)
    assert prepared[0]['fields']['product_details']['source_text'] == 'Potencia: salida: 20 W'
    assert [(r['label'], r['value']) for r in prepared[0]['details_structured']] == [('Potencia: salida', '20 W')]


@pytest.mark.parametrize('mutation', ['clean', 'source', 'raw_hash', 'allow', 'structured_rows'])
def test_tampered_preclean_and_rehashed_result_cannot_self_certify_source_binding(mutation):
    field = 'product_details' if mutation == 'structured_rows' else 'title_es_raw'
    record = {'asin': 'B000000001', field: 'Color: Rojo' if field == 'product_details' else '<b>Producto 1</b>'}
    production, prepared = prepare(record)
    clean = prepared[0]['fields'][field]
    if mutation == 'clean':
        # Reusing the raw digest used to let a forged, uncleaned envelope pass.
        clean['clean_text'] = production['records'][0]['fields'][field]['source_text']
    elif mutation == 'source':
        clean['source_text'] = 'Producto 2'
    elif mutation == 'raw_hash':
        prepared[0]['raw_fields'][field]['source_hash'] = digest('Producto 2')
    elif mutation == 'allow':
        clean['clean_status'] = 'CLEAN'
        clean['actions'] = []
    else:
        prepared[0]['details_structured'][0]['value'] = 'Azul'
    results = translated(prepared, field)
    state = field_state(production, prepared, results, field)
    assert state['promotion_status'] == 'SOURCE_CHANGED'
    assert not state['final_zh']


@pytest.mark.parametrize('mutation', ['manifest_hash', 'canonical_alias', 'field_text'])
def test_source_field_and_original_alias_binding_are_rechecked(mutation):
    production, prepared = prepare({'asin': 'B000000001', 'title_es_raw': 'Producto 1'})
    results = translated(prepared, 'title_es_raw')
    item = production['records'][0]
    if mutation == 'manifest_hash':
        item['fields']['title_es_raw']['source_hash'] = 'changed'
        results['B000000001']['fields']['title_zh']['source_hash'] = 'changed'
    elif mutation == 'canonical_alias':
        item['source_record']['title_es_raw'] = 'Producto 2'
    else:
        item['fields']['title_es_raw']['source_text'] = 'Producto 2'
        item['fields']['title_es_raw']['source_hash'] = digest('Producto 2')
        results['B000000001']['fields']['title_zh']['source_hash'] = digest('Producto 2')
    state = field_state(production, prepared, results, 'title_es_raw')
    assert state['promotion_status'] == 'SOURCE_CHANGED'
    assert state['final_zh'] is None


def test_result_hash_and_claimed_source_text_must_both_match_verified_clean_input():
    production, prepared = prepare({'asin': 'B000000001', 'title_es_raw': 'Producto 1'})
    results = translated(prepared, 'title_es_raw')
    results['B000000001']['fields']['title_zh']['source_text'] = 'Producto 2'
    assert field_state(production, prepared, results, 'title_es_raw')['promotion_status'] == 'SOURCE_CHANGED'


def test_expected_preclean_replay_cannot_be_replaced_by_a_forged_clean_status():
    production, prepared = prepare({'asin': 'B000000001', 'specification_es': 'Capacidad 9 L / 25,4 L'})
    field = prepared[0]['fields']['specification_es']
    field.update(clean_status='CLEAN', translate_allowed=True, issues=[], numeric={'values': [], 'issues': []})
    results = translated(prepared, 'specification_es')
    assert field_state(production, prepared, results, 'specification_es')['promotion_status'] == 'SOURCE_CHANGED'


def test_qa_issue_is_not_overridden_by_correct_input_binding():
    production, prepared = prepare({'asin': 'B000000001', 'title_es_raw': 'Producto 1'})
    results = translated(prepared, 'title_es_raw')
    results['B000000001']['fields']['title_zh']['qa_issues'] = [{'code': 'NUMERIC_MISMATCH'}]
    assert field_state(production, prepared, results, 'title_es_raw')['promotion_status'] != 'PROMOTED'


@pytest.mark.parametrize('key,value', [
    ('asin', 'B000000002'), ('field', 'brand'), ('target_field', 'brand_zh'),
])
def test_equal_text_hash_does_not_authorize_another_product_or_field(key, value):
    production, prepared = prepare({'asin': 'B000000001', 'title_es_raw': 'Producto 1'})
    results = translated(prepared, 'title_es_raw')
    results['B000000001']['fields']['title_zh'][key] = value
    assert field_state(production, prepared, results, 'title_es_raw')['promotion_status'] == 'SOURCE_CHANGED'


def test_real_service_offline_fake_receipt_uses_clean_hash_then_state_keeps_raw_hash(tmp_path):
    from amazon_es_bestseller.commands.translation_production import FakeTranslationProvider
    from amazon_es_bestseller.translation.cache import TranslationCache
    from amazon_es_bestseller.translation.service import TranslationService

    production, prepared = prepare({'asin': 'B000000001', 'title_es_raw': '<b>Producto</b> Ver más'})
    service = TranslationService(FakeTranslationProvider(), TranslationCache(tmp_path / 'offline-cache.json'))
    results = service.translate_records(prepared)['records']
    row = field_state(production, prepared, results, 'title_es_raw')
    assert row['promotion_status'] == 'PROMOTED'
    assert row['translation_input_hash'] == digest('Producto')
    assert row['source_hash'] == digest('<b>Producto</b> Ver más')
    # The existing CLI's fake/export gate remains responsible for rejecting
    # this explicitly fake execution as a formal production release.
    assert row['provider'] == 'fake'


def test_production_promotion_replays_preclean_once_for_the_whole_input(monkeypatch):
    from amazon_es_bestseller.translation import preclean as module

    records = [{'asin': f'B{i:09d}', 'title_es_raw': 'Producto'} for i in range(3)]
    production = build_production_input(records)
    prepared = audit_records(records_for_preclean(production))['translation_input_records']
    calls = []
    original = module.audit_records

    def counted(rows):
        calls.append(len(rows))
        return original(rows)

    monkeypatch.setattr(module, 'audit_records', counted)
    build_production_state(production, prepared, {})
    assert calls == [3]


def token_list(prepared, location):
    if location == 'field':
        return prepared[0]['fields']['product_details']['protected_tokens']
    return prepared[0]['details_structured'][0][location]['protected_tokens']


@pytest.mark.parametrize('location', ['field', 'label_clean', 'value_clean'])
def test_json_roundtrip_token_metadata_order_does_not_change_source_binding(location):
    production, prepared = prepare({'asin': 'B000000001',
                                    'product_details': 'IP44 Ip44: 15 Bar y 15 bar'})
    prepared = json.loads(json.dumps(prepared, ensure_ascii=False))
    tokens = token_list(prepared, location)
    assert len(tokens) >= 2
    tokens.reverse()
    row = field_state(production, prepared, {}, 'product_details')
    assert row['promotion_status'] == 'PENDING'
    assert row['source_hash'] == production['records'][0]['fields']['product_details']['source_hash']


@pytest.mark.parametrize('location', ['field', 'label_clean', 'value_clean'])
@pytest.mark.parametrize('mutation', ['remove', 'duplicate', 'change_case'])
def test_token_metadata_comparison_keeps_literal_tokens_and_duplicate_counts(location, mutation):
    production, prepared = prepare({'asin': 'B000000001',
                                    'product_details': 'IP44 Ip44: 15 Bar y 15 bar'})
    tokens = token_list(prepared, location)
    if mutation == 'remove':
        tokens.pop()
    elif mutation == 'duplicate':
        tokens.append(tokens[0])
    else:
        tokens[-1] = tokens[-1].swapcase()
    assert field_state(production, prepared, {}, 'product_details')['promotion_status'] == 'SOURCE_CHANGED'


def test_token_metadata_comparison_does_not_ignore_detail_row_order():
    production, prepared = prepare({'asin': 'B000000001', 'product_details': 'Color: Rojo\nVoltaje: 9 V'})
    prepared[0]['details_structured'].reverse()
    assert field_state(production, prepared, {}, 'product_details')['promotion_status'] == 'SOURCE_CHANGED'


def test_protected_tokens_have_a_total_order_even_when_casefold_keys_tie():
    from amazon_es_bestseller.translation.preclean import clean_text

    tokens = clean_text('15 Bar 15 bar IP44 Ip44 M2 m2')['protected_tokens']
    assert tokens == sorted(tokens, key=lambda item: (item.casefold(), item))
    assert set(tokens) == {'15 Bar', '15 bar', 'IP44', 'Ip44', 'M2', 'm2'}


def description_shard(envelope, batch='batch-1'):
    return {'batch_id': batch, 'records': {'B000000001': {'asin': 'B000000001',
            'fields': {'description_zh': envelope}}}}


def empty_description():
    return {'source_text': '', 'source_hash': '', 'translated_text': '',
            'translation_status': 'source_missing', 'qa_status': 'source_missing',
            'qa_issues': [], 'provider': 'qwen_mt', 'last_error': None}


def test_missing_description_without_a_hash_remains_source_missing_across_shard_merge():
    shards = [description_shard(empty_description()), description_shard(empty_description(), 'batch-2')]
    before = copy.deepcopy(shards)
    field = merge_translation_shards(shards)['B000000001']['fields']['description_zh']
    assert field['translation_status'] == 'source_missing'
    assert field['qa_status'] == 'source_missing'
    assert field['source_hash'] == '' and field['last_error'] is None
    assert field.get('promotion_status') != 'SOURCE_CHANGED'
    assert shards == before


@pytest.mark.parametrize('key,value', [
    ('source_text', 'Texto sin hash'), ('translated_text', '译文'), ('candidate_text', '候选'),
    ('source_text', ' '), ('raw_value', 'Texto original'), ('raw', {'source_text': 'Texto original'}),
    ('translation_status', 'success'), ('translation_status', 'pending'),
])
def test_a_missing_hash_is_exempt_only_for_an_explicitly_empty_source(key, value):
    envelope = empty_description()
    envelope[key] = value
    field = merge_translation_shards([description_shard(envelope)])['B000000001']['fields']['description_zh']
    assert field['promotion_status'] == 'SOURCE_CHANGED'
    assert field['last_error']['code'] == 'MISSING_SOURCE_HASH'


@pytest.mark.parametrize('old', [
    {'source_text': 'Texto original', 'source_hash': digest('Texto original'),
     'translated_text': '原译文', 'translation_status': 'success'},
    {'source_hash': 'old-source-hash', 'translation_status': 'failed'},
    {'source_text': 'Texto original', 'source_hash': '', 'translation_status': 'failed'},
])
def test_nonempty_source_becoming_missing_still_blocks_and_retains_history(old):
    shards = [description_shard(old), description_shard(empty_description(), 'batch-2')]
    before = copy.deepcopy(shards)
    field = merge_translation_shards(shards)['B000000001']['fields']['description_zh']
    assert field['translation_status'] == 'source_changed'
    assert field['promotion_status'] == 'SOURCE_CHANGED'
    assert field['last_error']['code'] == 'SOURCE_CHANGED'
    assert field['history'][-1]['source_hash'] == old['source_hash']
    assert shards == before


def test_two_empty_sources_with_equivalent_empty_hash_representations_do_not_imply_source_change():
    old = empty_description()
    old['source_hash'] = digest('')
    field = merge_translation_shards([description_shard(old), description_shard(empty_description(), 'batch-2')])['B000000001']['fields']['description_zh']
    assert field['translation_status'] == 'source_missing'
