import threading
from concurrent.futures import ThreadPoolExecutor

from amazon_es_bestseller.translation.pool import build_qwen_provider_pool, TranslationTask


def config(tmp_path):
    return {'shared_rate': 0.5, 'max_workers': 3, 'failover': False,
            'stop_on_rate_limit': True, 'rate_limit_halt_path': str(tmp_path / 'halt.json'),
            'providers': [{'name': a, 'api_key_env': 'UNSET_TEST_KEY', 'model': 'qwen-mt-flash',
                           'rate': 0, 'max_retries': 0} for a in ('A', 'B', 'C')]}


def test_three_routes_share_one_fake_clock_and_dedupe(tmp_path, monkeypatch):
    from amazon_es_bestseller.translation import pool as module
    clock = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda n: clock.__setitem__(0, clock[0] + n))
    sent = []
    def factory(alias):
        def transport(*args):
            sent.append((alias, clock[0]))
            return {'status_code': 200, 'body': {'text': '中文'}}
        return transport
    pool = build_qwen_provider_pool(config(tmp_path), transport_factory=factory)
    for p in pool.providers.values():
        p.api_key = 'fixture'
    tasks = [TranslationTask.from_values(str(i), asin='TEST', field='title') for i in range(6)]
    results = pool.submit(tasks + tasks)
    assert len(sent) == 6
    assert all(b[1] - a[1] >= 2 for a, b in zip(sent, sent[1:]))
    assert set(a for a, _ in sent) == {'A', 'B', 'C'}
    assert len(results) == 12


def test_waiting_route_rechecks_persistent_halt_before_transport(tmp_path, monkeypatch):
    from amazon_es_bestseller.translation import pool as module
    clock = [0.0]
    entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    def sleep(n):
        waiting.set()
        assert release.wait(3)
        clock[0] += n
    monkeypatch.setattr(module.time, 'sleep', sleep)
    sent = []
    def factory(alias):
        def transport(*args):
            sent.append(alias)
            if alias == 'A':
                entered.set()
                assert waiting.wait(3)
                return {'status_code': 429, 'body': {}}
            return {'status_code': 200, 'body': {'text': '中文'}}
        return transport
    pool = build_qwen_provider_pool(config(tmp_path), transport_factory=factory)
    for p in pool.providers.values():
        p.api_key = 'fixture'
    with ThreadPoolExecutor() as executor:
        first = executor.submit(pool._call_one, TranslationTask.from_values('1', asin='TEST', field='title'), 'A')
        assert entered.wait(3)
        future = executor.submit(pool._call_one, TranslationTask.from_values('2', asin='TEST', field='title'), 'B')
        assert waiting.wait(3)
        # Let A finish and persist the halt while B is still waiting for its slot.
        try:
            result = [first.result(3)]
            assert pool.halt_path.exists()
        finally:
            release.set()
        result.append(future.result(3))
    assert sent == ['A']
    assert sum(r.response.attempts for r in result) == 1


def test_two_already_inflight_429_responses_preserve_first_halt(tmp_path, monkeypatch):
    from amazon_es_bestseller.translation import pool as module
    clock = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda n: clock.__setitem__(0, clock[0] + n))
    barrier = threading.Barrier(2)
    def factory(alias):
        def transport(*args):
            barrier.wait(3)
            return {'status_code': 429, 'body': {}}
        return transport
    cfg = config(tmp_path)
    cfg['max_workers'] = 2
    pool = build_qwen_provider_pool(cfg, transport_factory=factory)
    for p in pool.providers.values():
        p.api_key = 'fixture'
    result = pool.submit([TranslationTask.from_values(str(i), asin='TEST', field='title') for i in range(2)])
    assert all(r.response.status == 'failed' for r in result)
    assert pool.halt_path.exists()
