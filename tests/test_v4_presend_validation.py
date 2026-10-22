"""Offline request-boundary checks using real V4 approvals and saved fixtures."""
from datetime import timedelta
from pathlib import Path

import pytest

from test_highspeed_v4_execution import setup_scope, NOW, FakeTransport
from amazon_es_bestseller.highspeed import runner as engine
from amazon_es_bestseller.highspeed.store import RunStore


def exercise(tmp_path, monkeypatch, *, mutation=None, paced=True):
    plan, approval = setup_scope(tmp_path)
    source = plan['sources'][0]
    # Copy the approval's evidence so mutation never touches a repository fixture.
    evidence = approval['sources'][source['source_id']]['evidence']
    copy = tmp_path / 'approval-evidence.html'
    copy.write_bytes(Path(evidence['path']).read_bytes())
    evidence['path'] = str(copy)
    store = RunStore(approval['store_root'])
    store.begin_run(plan, started_at=NOW.isoformat())
    current = [NOW]
    gate = {'state': 'NORMAL'}
    checks, sleeps = [], []
    original = engine.validate_authorization

    def validate(*args, **kwargs):
        checks.append(current[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, 'validate_authorization', validate)

    def sleep(seconds):
        sleeps.append(seconds)
        current[0] += timedelta(seconds=seconds)
        if mutation == 'gate': gate['state'] = 'RATE_LIMITED'
        if mutation == 'local_gate': store.set_gate('BLOCKED')
        if mutation == 'evidence': copy.write_text('changed bytes', encoding='utf8')
        if mutation == 'expired': approval['expires_at'] = NOW.isoformat()
        if mutation == 'scope': approval['sources'][source['source_id']]['page_urls'] = []

    transport = FakeTransport(plan, approval)
    runner = engine.HighSpeedRunner(store, clock=lambda: current[0], sleep=sleep,
                                    gate_reader=lambda: gate)
    try:
        if mutation in {'initial_gate', 'initial_unknown'}:
            gate['state'] = 'BLOCKED' if mutation == 'initial_gate' else 'UNKNOWN'
        action = lambda: runner._request(plan, approval, transport, source,
            source['actual_source_url'], 'PAGE', NOW,
            NOW if paced is True else NOW-timedelta(seconds=10) if paced == 'elapsed' else None)
        if mutation:
            with pytest.raises(ValueError): action()
            assert transport.calls == []
            assert store.budget_counters(plan['run_id'])['requests'] == 0
            if mutation.startswith('initial_'):
                assert sleeps == []
        else:
            action()
            assert len(transport.calls) == 1
            assert store.budget_counters(plan['run_id'])['requests'] == 1
            assert checks == ([NOW, NOW+timedelta(seconds=3)] if paced is True else [NOW])
            assert sleeps == ([3] if paced is True else [])
    finally:
        store.close()


@pytest.mark.parametrize('paced', [True, False, 'elapsed'])
def test_one_complete_v4_validation_at_presend_boundary(tmp_path, monkeypatch, paced):
    exercise(tmp_path, monkeypatch, paced=paced)


@pytest.mark.parametrize('mutation', [
    'gate', 'local_gate', 'evidence', 'expired', 'scope', 'initial_gate', 'initial_unknown',
])
def test_changes_while_pacing_block_before_charge_or_fetch(tmp_path, monkeypatch, mutation):
    exercise(tmp_path, monkeypatch, mutation=mutation)
