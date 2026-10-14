from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from test_highspeed_v4_adapters import FIXTURES
from amazon_es_bestseller.highspeed.v4_workflows import prepare_v4
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.authorization import STOP_CONDITIONS, validate_authorization
from amazon_es_bestseller.multisource.storage import immutable_json, sealed, digest


NOW = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)


def setup_scope(tmp_path):
    shared = tmp_path / 'shared' / 'access_gate.json'
    immutable_json(shared, sealed({'state': 'NORMAL'}))
    result = prepare_v4(FIXTURES / 'manifest.json', tmp_path, tmp_path / 'runtime', shared)
    d = Path(result['directory'])
    plan = json.loads((d / 'plan.pending.json').read_text(encoding='utf8'))
    approval = json.loads((d / 'authorization.pending.json').read_text(encoding='utf8'))
    plan['live_enabled'] = True
    approval.update(approved=True, approval_id='offline-test-approval', approved_by='TEST_ONLY',
                    plan_sha256=digest(plan), reviewed_at='2026-10-14T14:00:00Z',
                    expires_at='2026-10-14T16:00:00Z', access_gate_state='NORMAL',
                    shared_access_gate_path=str(shared.resolve()), stop_conditions=sorted(STOP_CONDITIONS))
    for k in ('max_requests_per_run', 'max_pages_per_run', 'max_requests_per_day', 'max_raw_observations_per_run',
              'history_observation_limit', 'target_unique_links', 'max_duration_hours', 'request_pacing_seconds',
              'parallelism', 'category_cooldown_seconds'):
        approval[k] = plan[k]
    for scope in approval['sources'].values():
        scope['allow_native_acp'] = False
    return plan, approval


class FakeTransport:
    def __init__(self, plan, approval, code=200):
        self.calls = []
        self.approval = approval; self.plan = plan; self.code = code

    def fetch_page(self, url):
        self.calls.append(url)
        source = next(s for s in self.plan['sources'] if s['actual_source_url'] == url)
        path = self.approval['sources'][source['source_id']]['evidence']['path']
        return {'status_code': self.code, 'url': url, 'text': Path(path).read_text(encoding='utf8')}


def test_same_store_request_engine_eight_roots_resume_no_repeat(tmp_path):
    plan, approval = setup_scope(tmp_path)
    store = RunStore(approval['store_root'])
    try:
        transport = FakeTransport(plan, approval)
        runner = V4T1Runner(store, clock=lambda: NOW, sleep=lambda n: None)
        result = runner.run(plan, authorization=approval, transport=transport)
        assert len(transport.calls) == 8
        assert result['summary']['requests'] == 8
        assert result['summary']['raw_count'] == 336
        assert result['quality']['central_full_dom_review_passed'], result['quality']['errors'][:2]
        assert result['run']['quality_gate_passed'] is False
        again = runner.run(plan, authorization=approval, transport=transport, resume=True)
        assert len(transport.calls) == 8
        assert again['summary']['requests'] == 8
    finally:
        store.close()


@pytest.mark.parametrize('code', [403, 429, 503])
def test_stop_preserves_receipt_and_both_gates(tmp_path, code):
    plan, approval = setup_scope(tmp_path)
    store = RunStore(approval['store_root'])
    try:
        transport = FakeTransport(plan, approval, code)
        runner = V4T1Runner(store, clock=lambda: NOW, sleep=lambda n: None)
        result = runner.run(plan, authorization=approval, transport=transport)
        assert len(transport.calls) == 1
        assert result['run']['status'] == 'STOPPED'
        assert store.get_gate()['state'] != 'NORMAL'
        receipts = list(store.iter_responses(plan['run_id']))
        assert len(receipts) == 1 and Path(receipts[0]['html_path']).exists()
        with pytest.raises(ValueError, match='GLOBAL_ACCESS_GATE_BLOCKED'):
            runner.run(plan, authorization=approval, transport=transport, resume=True)
        assert len(transport.calls) == 1
    finally:
        store.close()


@pytest.mark.parametrize('mutation', ['unapproved', 'expired', 'budget', 'plan', 'stage'])
def test_no_request_when_approval_invalid(tmp_path, mutation):
    plan, a = setup_scope(tmp_path)
    if mutation == 'unapproved': a['approved'] = False
    if mutation == 'expired': a['expires_at'] = '2026-10-14T14:59:59Z'
    if mutation == 'budget': a['max_requests_per_run'] += 1
    if mutation == 'plan': plan['max_pages_per_run'] += 1
    if mutation == 'stage': a['stage'] = 'T3'
    store = RunStore(a['store_root'])
    try:
        transport = FakeTransport(plan, a)
        with pytest.raises(ValueError):
            V4T1Runner(store, clock=lambda: NOW).run(plan, authorization=a, transport=transport)
        assert transport.calls == []
    finally:
        store.close()


def test_charged_budget_and_resume_never_reset_start_time(tmp_path):
    plan, a = setup_scope(tmp_path)
    plan['max_requests_per_run'] = a['max_requests_per_run'] = 2
    a['plan_sha256'] = digest(plan)
    store = RunStore(a['store_root'])
    try:
        t = FakeTransport(plan, a)
        runner = V4T1Runner(store, clock=lambda: NOW, sleep=lambda n: None)
        result = runner.run(plan, authorization=a, transport=t)
        started = result['run']['started_at']
        assert len(t.calls) == 2
        result = runner.run(plan, authorization=a, transport=t, resume=True)
        assert len(t.calls) == 2
        assert result['run']['started_at'] == started
    finally:
        store.close()


def test_v4_plan_cannot_enter_legacy_unbounded_workflow(tmp_path):
    from amazon_es_bestseller.highspeed.runner import HighSpeedRunner
    from amazon_es_bestseller.highspeed.cli import run_live
    plan, a = setup_scope(tmp_path)
    store = RunStore(a['store_root'])
    try:
        with pytest.raises(ValueError, match='EXPLICIT_V4'):
            HighSpeedRunner(store).run(plan, authorization=a)
        with pytest.raises(ValueError, match='EXPLICIT_V4'):
            run_live(None, store, plan, a, entry_time=NOW.isoformat())
    finally:
        store.close()


def test_v4_cannot_adopt_a_historical_store(tmp_path):
    from amazon_es_bestseller.highspeed.contracts import new_plan
    plan, a = setup_scope(tmp_path)
    store = RunStore(a['store_root'])
    try:
        store.begin_run(new_plan('historical'))
        before = list(store.db.execute('SELECT run_id,plan_hash FROM runs'))
        t = FakeTransport(plan, a)
        with pytest.raises(ValueError, match='NO_HISTORICAL_RUNS'):
            V4T1Runner(store, clock=lambda: NOW).run(plan, authorization=a, transport=t)
        assert list(store.db.execute('SELECT run_id,plan_hash FROM runs')) == before
        assert not t.calls
    finally:
        store.close()
