import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.pool import ProviderPool, TranslationTask
from amazon_es_bestseller.translation.providers.qwen_mt import QwenMTProvider
from amazon_es_bestseller.translation.raw_journal import RawResponseJournal


FIXTURE = Path(__file__).parent / 'fixtures/qwen_content_rejection_20261011.json'


def task(text):
    return TranslationTask.from_values(text, asin='B083VBXG2S', field='product_details')


def test_content_rejection_preserves_failure_and_keeps_lane_healthy_without_replay(tmp_path):
    seen = []

    def transport(url, headers, payload, timeout):
        seen.append(payload['messages'][0]['content'])
        if len(seen) == 1:
            return json.loads(FIXTURE.read_text(encoding='utf-8'))
        return {'status_code': 200, 'body': {'choices': [{'message': {'content': '中文'}}]}}

    provider = QwenMTProvider(api_key='offline-test', transport=transport,
        endpoint='https://example.invalid', max_retries=0, rate=0,
        response_journal=RawResponseJournal(tmp_path / 'raw'))
    pool = ProviderPool({'QWEN_C': provider}, max_workers=1, failover=True)
    rejected = pool.submit([task('rejected source')])[0]
    assert rejected.response.status == 'failed'
    assert 'data_inspection_failed' in rejected.response.error
    assert pool.stats()['QWEN_C']['state'] == 'HEALTHY'
    assert RawResponseJournal.verify(rejected.response.raw['raw_response_refs'][0])
    # A request-local rejection remains terminal for this semantic unit.
    replay = pool.submit([task('rejected source')])[0]
    assert replay.response.status == 'failed'
    assert seen == ['rejected source']
    next_result = pool.submit([task('unrelated ordinary source')])[0]
    assert next_result.response.status == 'success'
    assert len(seen) == 2
    assert pool.stats()['QWEN_C']['failed'] == 1
    assert pool.stats()['QWEN_C']['retries'] == 0


@pytest.mark.parametrize('status,code,expected', [
    (400, 'invalid_parameter', 'DISABLED'),
    (401, 'invalid_api_key', 'DISABLED'),
    (403, 'access_denied', 'DISABLED'),
    (429, 'limit_requests', 'RATE_LIMITED'),
    (500, 'internal_error', 'DEGRADED'),
])
def test_endpoint_failures_are_not_misclassified_as_content_rejection(status, code, expected):
    provider = QwenMTProvider(api_key='offline-test', endpoint='https://example.invalid',
        max_retries=0, rate=0, transport=lambda *args: {
            'status_code': status, 'body': {'error': {'code': code, 'type': code}}})
    pool = ProviderPool({'QWEN_C': provider}, max_workers=1, failover=False,
        stop_on_rate_limit=True)
    result = pool.submit([task('ordinary source')])[0]
    assert result.response.status == 'failed'
    assert pool.stats()['QWEN_C']['state'] == expected


def test_content_rejection_has_no_other_lane_failover(tmp_path):
    calls = []
    providers = {}
    for alias in ('A', 'B'):
        def transport(*args, alias=alias):
            calls.append(alias)
            return json.loads(FIXTURE.read_text(encoding='utf-8'))
        providers[alias] = QwenMTProvider(api_key='offline-test', transport=transport,
            endpoint='https://example.invalid', max_retries=0, rate=0,
            response_journal=RawResponseJournal(tmp_path / alias))
    pool = ProviderPool(providers, max_workers=2, failover=True)
    result = pool.submit([task('rejected source')])[0]
    assert result.response.status == 'failed'
    assert calls == ['A']
    assert pool.stats()['A']['state'] == pool.stats()['B']['state'] == 'HEALTHY'
