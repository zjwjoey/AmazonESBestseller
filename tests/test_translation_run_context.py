import json
from copy import deepcopy

import pytest

from amazon_es_bestseller.cli import main
from amazon_es_bestseller.orchestration.translation_batch import file_hash, load_source_candidate
from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.legacy_reference import (
    admit_legacy_run_reference_cache, build_legacy_reference_candidates, prepare_legacy_review_input,
    LEGACY_REVIEWED_POLICY_VERSION,
)
from amazon_es_bestseller.translation.preclean import audit_records
from amazon_es_bestseller.translation.production import build_production_input, records_for_preclean
from amazon_es_bestseller.translation.providers.base import ProviderResponse
from amazon_es_bestseller.translation.service import TranslationService
from test_translation_parent_authority import _source


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return {'path': str(path), 'sha256': file_hash(path)}


class FakeProvider:
    name = 'qwen-mt'
    model = 'qwen-mt-flash'
    rate = 0
    calls = []

    def __init__(self, **kwargs):
        pass

    def translate(self, text, **kwargs):
        self.calls.append((kwargs['asin'], kwargs['field'], text))
        return ProviderResponse(text='细腻质感', provider=self.name, model=self.model, status='success', attempts=1)


def setup_run(tmp_path):
    source_path, _ = _source(tmp_path, scalar_fields={
        'title_es_raw': 'Producto de prueba', 'product_description': 'Acabado suave',
        'feature_bullets': ['Textura fina']})
    source = json.loads(source_path.read_text(encoding='utf-8'))
    record = next(row for row in source['records'] if row['asin'] == 'B000000020')
    asin = record['asin']
    loaded = load_source_candidate(source_path.parent / 'manifest.json',
        manifest_hash=file_hash(source_path.parent / 'manifest.json'),
        available_asins=[row['asin'] for row in source['records']])
    canonical = [row for row in loaded['records'] if row['asin'] == asin]
    rows = audit_records(records_for_preclean(build_production_input(canonical)))['translation_input_records']
    rows[0]['fields']['title_es_raw']['translate_allowed'] = False
    rows[0]['fields']['product_details']['translate_allowed'] = False
    products = tmp_path / 'products.json'
    product_ref = save(products, {'records': rows})
    candidates = build_legacy_reference_candidates(source['records'], {asin: {
        'spanish': {'title_es_raw': record['title_es_raw']}, 'chinese': {'title_es_raw': '测试商品'},
        'provenance': {'source_kind': 'legacy_excel_reference', 'provider': 'unknown'}}}, source['source_gate'])
    authority = {'source_candidate_manifest_path': source_path.parent / 'manifest.json',
        'source_candidate_manifest_hash': file_hash(source_path.parent / 'manifest.json'),
        'available_asins': [row['asin'] for row in source['records']], 'selected_asins': [asin]}
    review = prepare_legacy_review_input(candidates, **authority)
    row = review['candidates'][0]
    policy = {'policy_version': LEGACY_REVIEWED_POLICY_VERSION, 'semantic_decisions': [{
        'candidate_hash': row['candidate_hash'], 'context_hash': row['context_hash'], 'source_hash': row['source_hash'],
        'reviewed_value': row['candidate_value'], 'decision': 'KEEP', 'semantic_review_status': 'PASS',
        'review_model': 'CODEX', 'review_note': 'Exact fixture source reviewed.'}]}
    admitted = admit_legacy_run_reference_cache(candidates, source['source_gate'], policy,
        output_path=tmp_path / 'reference.json', **authority)
    overlay_ref = save(tmp_path / 'overlay.json', {'authority': admitted['authority'],
        'reference_envelopes': admitted['reference_envelopes']})
    snapshot = TranslationCache(tmp_path / 'snapshot.json')
    service = TranslationService(FakeProvider(), snapshot)
    key = service._memory_key('Prueba', 'leaf_category')
    snapshot.put_memory(key, {'translated_text': '柔和饰面', 'candidate_text': '柔和饰面',
        'translation_status': 'success', 'qa_status': 'pass', 'qa_issues': [],
        'provider': 'qwen-mt', 'model': 'qwen-mt-flash'})
    snapshot.save()
    historical = {'path': str(snapshot.path), 'sha256': file_hash(snapshot.path)}
    manifest = {'schema_version': 'translation-run-context-v1', 'products': product_ref,
        'snapshot': {'path': str(snapshot.path), 'sha256': file_hash(snapshot.path)},
        'run_cache_path': str(tmp_path / 'run_cache.json'), 'selected_asins': [asin],
        'fields': ['title_es_raw', 'leaf_category', 'feature_bullets', 'product_details'],
        'service_contract': {'schema_version': service.schema_version, 'prompt_version': 'v1',
                             'structured_schema_version': ''},
        'provider_identity': {'name': 'qwen-mt', 'model': 'qwen-mt-flash'},
        'source_manifest': {'path': str(authority['source_candidate_manifest_path']),
                            'sha256': authority['source_candidate_manifest_hash']},
        'available_asins': authority['available_asins'],
        'legacy_candidates': save(tmp_path / 'candidates.json', candidates),
        'legacy_policy': save(tmp_path / 'policy.json', policy), 'reference_overlay': overlay_ref,
        'historical_caches': [historical]}
    config = {'provider': 'qwen-mt', 'fields': manifest['fields'],
        'run_context': save(tmp_path / 'run.json', manifest)}
    config_path = tmp_path / 'config.json'
    save(config_path, config)
    return products, config_path, manifest, rows


def invoke(products, config, manifest, out, *, dry=False):
    args = ['translate', '--products', str(products), '--config', str(config),
            '--cache', manifest['run_cache_path'], '--out', str(out), '--yes']
    if dry:
        args = ['--offline'] + args + ['--dry-run']
    return main(args)


@pytest.mark.parametrize('parallel', [False, True])
def test_actual_cli_uses_bound_snapshot_references_holds_and_resume(tmp_path, monkeypatch, parallel):
    products, config, manifest, rows = setup_run(tmp_path)
    from amazon_es_bestseller.translation.providers import qwen_mt
    monkeypatch.setattr(qwen_mt, 'QwenMTProvider', FakeProvider)
    if parallel:
        from amazon_es_bestseller.translation import pool
        configuration = json.loads(config.read_text(encoding='utf-8'))
        aliases = ['TEST_A', 'TEST_B', 'TEST_C']
        configuration.update({'strict_provider_mapping': True, 'max_workers': 3, 'providers': [
            {'name': alias, 'api_key_env': alias + '_KEY',
             'endpoint': 'https://fake.invalid/v1/chat/completions', 'model': 'qwen-mt-flash'}
            for alias in aliases]})
        for alias in aliases:
            monkeypatch.setenv(alias + '_KEY', 'FAKE_ONLY')
        save(config, configuration)
        monkeypatch.setattr(pool, 'build_qwen_provider_pool', lambda configuration:
            pool.ProviderPool({alias: FakeProvider() for alias in aliases}, max_workers=3))
    FakeProvider.calls = []
    claims = []
    original_claim = TranslationCache.claim_memory

    def capture_claim(cache, key, pending, **kwargs):
        claimed, result = original_claim(cache, key, pending, **kwargs)
        if claimed:
            claims.append(key)
        return claimed, result

    monkeypatch.setattr(TranslationCache, 'claim_memory', capture_claim)
    before = {spec['path']: file_hash(spec['path']) for spec in
              (manifest['snapshot'], manifest['reference_overlay'], manifest['source_manifest'])}
    plan_path = tmp_path / 'plan.json'
    assert invoke(products, config, manifest, plan_path, dry=True) == 0
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    assert plan['estimated_api_requests'] == 1
    assert plan['translation_memory_hits'] == 1
    assert plan['resolved_reference_fields'] == 1 and plan['review_blocked'] == 1
    assert not FakeProvider.calls and not (tmp_path / 'run_cache.json').exists()
    output_path = tmp_path / 'out.json'
    assert invoke(products, config, manifest, output_path) == 0
    assert [(asin, field) for asin, field, _ in FakeProvider.calls] == [('B000000020', 'feature_bullets')]
    output = json.loads(output_path.read_text(encoding='utf-8'))['B000000020']
    assert output['title_zh'] == '测试商品'
    assert output['fields']['title_zh']['provider'] == 'unknown'
    assert output['fields']['title_zh']['resolution_source'] == 'legacy-reviewed-reference'
    assert output['fields']['leaf_category_zh']['translated_text'] == '柔和饰面'
    assert output['fields']['product_details_zh']['translation_status'] in {'pending', 'preclean_blocked'}
    assert invoke(products, config, manifest, tmp_path / 'resume.json') == 0
    assert len(FakeProvider.calls) == plan['estimated_api_requests']
    assert sorted(claims) == plan['dispatch_keys']
    assert all(file_hash(path) == digest for path, digest in before.items())
    current = TranslationCache(manifest['run_cache_path'])
    assert all(item.get('provider') != 'unknown' for item in current.memory.values())
    assert rows[0]['fields']['title_es_raw']['translate_allowed'] is False


@pytest.mark.parametrize('mutation', ['products', 'snapshot', 'source_manifest', 'reference_overlay'])
def test_actual_cli_binding_error_blocks_before_provider(tmp_path, monkeypatch, mutation):
    products, config_path, manifest, _ = setup_run(tmp_path)
    from amazon_es_bestseller.translation.providers import qwen_mt
    monkeypatch.setattr(qwen_mt, 'QwenMTProvider', lambda **kwargs: pytest.fail('no provider construction'))
    manifest[mutation]['sha256'] = 'wrong'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config['run_context'] = save(tmp_path / 'run.json', manifest)
    save(config_path, config)
    with pytest.raises(ValueError, match='RUN_CONTEXT'):
        invoke(products, config_path, manifest, tmp_path / 'plan.json', dry=True)


def rebind(tmp_path, config_path, manifest):
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config['run_context'] = save(tmp_path / 'run.json', manifest)
    save(config_path, config)


@pytest.mark.parametrize('uncertain', [False, True])
def test_three_readonly_caches_conflict_or_unknown_attempt_is_never_dispatched(tmp_path, monkeypatch, uncertain):
    from amazon_es_bestseller.translation.run_context import build_run_cache_snapshot
    products, config, manifest, rows = setup_run(tmp_path)
    payload = json.loads((tmp_path / 'snapshot.json').read_text(encoding='utf-8'))
    origins = []
    for index in range(3):
        changed = deepcopy(payload)
        if index == 2:
            for namespace in ('memory', 'memory_results'):
                for value in changed[namespace].values():
                    if uncertain:
                        value['translation_status'] = 'pending'
                        value['last_error'] = 'TRANSPORT_OUTCOME_UNKNOWN'
                    else:
                        value['candidate_text'] = value['translated_text'] = '另一译文'
        origins.append(save(tmp_path / f'history_{index}.json', changed))
    before = {item['path']: item['sha256'] for item in origins}
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / 'NEVER_WRITTEN.json'))
    snapshot = build_run_cache_snapshot(origins, rows, service, tmp_path / 'bound_snapshot.json')
    assert snapshot['conflict_or_pending_identities'] == 1
    manifest['snapshot'], manifest['historical_caches'] = snapshot, origins
    rebind(tmp_path, config, manifest)
    from amazon_es_bestseller.translation.providers import qwen_mt
    monkeypatch.setattr(qwen_mt, 'QwenMTProvider', FakeProvider)
    FakeProvider.calls = []
    assert invoke(products, config, manifest, tmp_path / 'plan.json', dry=True) == 0
    assert invoke(products, config, manifest, tmp_path / 'out.json') == 0
    assert [field for _, field, _ in FakeProvider.calls] == ['feature_bullets']
    result = json.loads((tmp_path / 'out.json').read_text(encoding='utf-8'))['B000000020']
    assert result['fields']['leaf_category_zh']['translation_status'] == 'pending'
    assert all(file_hash(path) == digest for path, digest in before.items())


@pytest.mark.parametrize('mutation', ['scope', 'schema', 'context', 'raw_field', 'qwen_reference', 'snapshot_origin'])
def test_rebound_manifest_cannot_self_certify_changed_authority_or_cache(tmp_path, monkeypatch, mutation):
    products, config, manifest, rows = setup_run(tmp_path)
    if mutation == 'scope':
        manifest['selected_asins'] = ['B000000099']
    elif mutation == 'schema':
        manifest['service_contract']['schema_version'] = 'old'
    elif mutation in {'context', 'qwen_reference'}:
        overlay = json.loads((tmp_path / 'overlay.json').read_text(encoding='utf-8'))
        overlay['reference_envelopes'][0]['context_hash' if mutation == 'context' else 'provider'] = 'wrong'
        manifest['reference_overlay'] = save(tmp_path / 'overlay.json', overlay)
    elif mutation == 'raw_field':
        rows[0]['raw_fields']['leaf_category']['source_hash'] = 'wrong'
        manifest['products'] = save(products, {'records': rows})
    else:
        payload = json.loads((tmp_path / 'snapshot.json').read_text(encoding='utf-8'))
        manifest['historical_caches'] = [save(tmp_path / 'origin.json', payload)]
        next(iter(payload['memory'].values()))['translated_text'] = '伪造'
        manifest['snapshot'] = save(tmp_path / 'snapshot.json', payload)
    rebind(tmp_path, config, manifest)
    from amazon_es_bestseller.translation.providers import qwen_mt
    monkeypatch.setattr(qwen_mt, 'QwenMTProvider', lambda **kwargs: pytest.fail('no provider construction'))
    with pytest.raises(ValueError, match='RUN_CONTEXT'):
        invoke(products, config, manifest, tmp_path / 'plan.json', dry=True)


def test_corrupt_historical_cache_is_not_recovered_or_renamed(tmp_path):
    from amazon_es_bestseller.translation.run_context import build_run_cache_snapshot
    _, _, _, rows = setup_run(tmp_path)
    origin = tmp_path / 'corrupt.json'
    origin.write_bytes(b'{broken')
    reference = {'path': str(origin), 'sha256': file_hash(origin)}
    service = TranslationService(FakeProvider(), TranslationCache(tmp_path / 'NEVER_WRITTEN.json'))
    with pytest.raises(ValueError):
        build_run_cache_snapshot([reference], rows, service, tmp_path / 'not_written.json')
    assert origin.read_bytes() == b'{broken' and not list(tmp_path.glob('corrupt.json.corrupt-*'))
    assert not (tmp_path / 'not_written.json').exists()
