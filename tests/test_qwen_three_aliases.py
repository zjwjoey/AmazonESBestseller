import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation import pool as module
from amazon_es_bestseller.translation.pool import PoolProviderAdapter, TranslationTask, build_qwen_provider_pool
from amazon_es_bestseller.translation.providers.qwen_mt import QwenMTProvider


CONFIG = Path('configs/translation_v2_three_existing.json')


def configured(monkeypatch):
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    for name in ('QWEN_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_THIRD_API_KEY'):
        monkeypatch.setenv(name, 'fake-' + name)
    for name in ('QWEN_API_ENDPOINT', 'DASHSCOPE_API_ENDPOINT', 'QWEN_THIRD_API_ENDPOINT'):
        monkeypatch.setenv(name, 'https://' + name.lower().replace('_', '-') + '.invalid/compatible-mode/v1/chat/completions')
    monkeypatch.setenv('QWEN_MT_BASE_URL', 'https://base.invalid/compatible-mode/v1')
    monkeypatch.setenv('QWEN_MT_MODEL', 'env-model')
    return config


def test_three_explicit_aliases_and_masked_preflight(monkeypatch):
    config = configured(monkeypatch)
    report = module.preflight_qwen_provider_pool(config)
    assert report['status'] == 'READY' and report['max_workers'] == 3
    assert [row['alias'] for row in report['providers']] == ['QWEN_A', 'QWEN_B', 'QWEN_C']
    assert 'fake-' not in json.dumps(report)
    calls = []

    def factory(alias):
        def transport(url, headers, payload, timeout):
            calls.append((alias, url, headers['Authorization'], payload['model']))
            return {'status_code': 200, 'body': {'text': '测试商品'}}
        return transport

    pool = build_qwen_provider_pool(config, transport_factory=factory)
    tasks = [TranslationTask.from_values(str(n), asin=str(n), field='title_es_raw') for n in range(6)]
    pool.submit(tasks)
    pool.submit(tasks)
    assert len(calls) == 6 and {row[0] for row in calls} == {'QWEN_A', 'QWEN_B', 'QWEN_C'}
    for alias, url, authorization, model in calls:
        spec = next(row for row in config['providers'] if row['name'] == alias)
        assert authorization == 'Bearer fake-' + spec['api_key_env']
        assert spec['endpoint_env'].lower().replace('_', '-') in url
        assert model == 'env-model'
    assert 'fake-' not in json.dumps(pool.snapshot())
    assert sum(row['requests'] for row in pool.stats().values()) == 6
    assert sum(row['http_attempts'] for row in pool.stats().values()) == 6


def test_unmapped_endpoints_block_before_credential_reads_or_transport(monkeypatch):
    config = configured(monkeypatch)
    monkeypatch.delenv('DASHSCOPE_API_ENDPOINT')
    monkeypatch.delenv('QWEN_THIRD_API_ENDPOINT')
    report = module.preflight_qwen_provider_pool(config)
    assert report['status'] == 'BLOCKED'
    assert {item['alias'] for item in report['missing']} == {'QWEN_B', 'QWEN_C'}
    with pytest.raises(ValueError, match='PROVIDER_CONFIGURATION_BLOCKED'):
        build_qwen_provider_pool(config, transport_factory=lambda _: pytest.fail('must not construct transport'))


def test_endpoint_and_model_priority_and_base_path(monkeypatch):
    configured(monkeypatch)
    provider = QwenMTProvider(api_key='fake', endpoint='https://explicit.invalid/chat/completions', model='explicit')
    assert provider.endpoint == 'https://explicit.invalid/chat/completions' and provider.model == 'explicit'
    assert QwenMTProvider(api_key='fake').model == 'env-model'
    monkeypatch.delenv('QWEN_API_ENDPOINT')
    assert 'dashscope-api-endpoint.invalid' in QwenMTProvider(api_key='fake').endpoint
    monkeypatch.delenv('DASHSCOPE_API_ENDPOINT')
    assert QwenMTProvider(api_key='fake').endpoint == 'https://base.invalid/compatible-mode/v1/chat/completions'
    for path in ('https://base.invalid/compatible-mode/v1/', 'https://base.invalid/compatible-mode/v1/chat/completions'):
        monkeypatch.setenv('QWEN_MT_BASE_URL', path)
        assert QwenMTProvider(api_key='fake').endpoint == 'https://base.invalid/compatible-mode/v1/chat/completions'
    monkeypatch.setenv('QWEN_MT_BASE_URL', 'https://base.invalid/unknown')
    with pytest.raises(ValueError, match='QWEN_BASE_URL_PATH_INVALID'):
        QwenMTProvider(api_key='fake')


def test_unknown_transport_is_held_no_retry_no_failover_other_work_continues(monkeypatch):
    config = configured(monkeypatch)
    for spec in config['providers']:
        spec['max_retries'] = 2
    calls = []

    def factory(alias):
        def transport(*args):
            calls.append(alias)
            if alias == 'QWEN_A':
                raise TimeoutError('unknown after send')
            return {'status_code': 200, 'body': {'text': '测试商品'}}
        return transport

    pool = build_qwen_provider_pool(config, transport_factory=factory)
    tasks = [TranslationTask.from_values(str(n), asin=str(n), field='title_es_raw') for n in range(3)]
    results = pool.submit(tasks)
    assert results[0].response.status == 'pending'
    assert calls.count('QWEN_A') == 1 and len(calls) == 3
    assert pool.submit([tasks[0]])[0].response.status == 'pending'
    assert len(calls) == 3
    assert pool.snapshot()['held_pending'] == 1
    assert pool.stats()['QWEN_A']['http_attempts'] == 1
    assert pool.stats()['QWEN_A']['retries'] == 0
    assert all(result.response.status == 'success' for result in results[1:])
    assert PoolProviderAdapter(pool).translate('0', asin='another', field='title_es_raw').status == 'pending'
    assert len(calls) == 3


def test_qa_failure_does_not_failover(monkeypatch):
    config = configured(monkeypatch)
    calls = []

    def factory(alias):
        def transport(*args):
            calls.append(alias)
            return {'status_code': 200, 'body': {'choices': []}}
        return transport

    pool = build_qwen_provider_pool(config, transport_factory=factory)
    result = pool.submit([TranslationTask.from_values('x', asin='A', field='title_es_raw')])[0]
    assert result.response.error == 'EMPTY_TRANSLATION' and len(calls) == 1
    assert pool.submit([TranslationTask.from_values('x', asin='B', field='title_es_raw')])[0].response.error == 'EMPTY_TRANSLATION'
    assert len(calls) == 1


@pytest.mark.parametrize('blocked', [True, False])
def test_real_cli_strict_dryrun_never_reads_credentials(monkeypatch, tmp_path, blocked):
    from amazon_es_bestseller.cli import main
    configured(monkeypatch)
    if blocked:
        monkeypatch.delenv('DASHSCOPE_API_ENDPOINT')
    real_getenv = module.os.getenv

    def guarded(name, *args):
        if name in {'QWEN_API_KEY', 'DASHSCOPE_API_KEY', 'QWEN_THIRD_API_KEY'}:
            pytest.fail('offline dryrun cannot read key value')
        return real_getenv(name, *args)

    monkeypatch.setattr(module.os, 'getenv', guarded)
    products, out = tmp_path / 'input.json', tmp_path / 'plan.json'
    products.write_text(json.dumps([{'asin': 'B000000020', 'title_es_raw': 'Producto de prueba'}]), encoding='utf-8')
    assert main(['--offline', 'translate', '--products', str(products), '--config', str(CONFIG),
                 '--cache', str(tmp_path / 'cache.json'), '--out', str(out), '--dry-run']) == 0
    plan = json.loads(out.read_text(encoding='utf-8'))
    assert plan['provider_preflight']['status'] == ('BLOCKED' if blocked else 'READY')
    assert plan['provider_preflight']['dispatches'] == 0
    if blocked:
        assert plan['status'] == 'PROVIDER_CONFIGURATION_BLOCKED'
    else:
        assert plan['pool']['max_workers'] == 3


def test_service_pending_attempt_survives_cache_reload_without_resend(monkeypatch, tmp_path):
    from amazon_es_bestseller.translation.cache import TranslationCache
    from amazon_es_bestseller.translation.service import TranslationService
    config = configured(monkeypatch)
    calls = []

    def factory(alias):
        def transport(*args):
            calls.append(alias)
            raise TimeoutError('sent outcome unknown')
        return transport

    pool = build_qwen_provider_pool(config, transport_factory=factory)
    path = tmp_path / 'cache.json'
    adapter = PoolProviderAdapter(pool)
    records = [{'asin': 'B000000020', 'title_es_raw': 'Producto de prueba'}]
    first = TranslationService(adapter, TranslationCache(path)).translate_records(records)
    assert first['records']['B000000020']['fields']['title_zh']['translation_status'] == 'pending'
    TranslationService(adapter, TranslationCache(path)).translate_records(records, repair_failed=True)
    assert len(calls) == 1
