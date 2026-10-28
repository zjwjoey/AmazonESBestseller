"""Offline-only snapshot expansion regressions; no collection authorization."""
from copy import deepcopy
import json
from pathlib import Path

import pytest


def test_new_profiles_preserve_old_policy_and_reject_tampering():
    from amazon_es_bestseller.highspeed.global_target import (
        MAIN_PRIORITY_POLICY, MAIN_PRIORITY_10000_POLICY, MAIN_PRIORITY_50000_POLICY,
        is_global_policy,
    )
    assert MAIN_PRIORITY_POLICY['target'] == 20000
    for policy, target in ((MAIN_PRIORITY_10000_POLICY, 10000), (MAIN_PRIORITY_50000_POLICY, 50000)):
        assert is_global_policy(policy)
        assert policy['target'] == target
        assert policy['source_minima']['CUSTOMER_FAVORITES'] == 3000
        bad = deepcopy(policy); bad['target'] += 1
        with pytest.raises(ValueError):
            is_global_policy(bad)


def test_real_saved_card_images_are_opt_in_and_dom_audited():
    from test_snapshot_main_priority import real_source, FIXTURE
    from amazon_es_bestseller.highspeed.v4_adapters import parse_v4_page
    from amazon_es_bestseller.highspeed.v4_quality import audit_v4
    source = real_source()
    kwargs = dict(source=source, page_url=source['actual_source_url'], run_id='TEST_ONLY',
                  observed_at='2026-10-21T01:00:00Z', evidence_path=str(FIXTURE),
                  origin='OFFLINE_FIXTURE', snapshot_metadata_contract='SOURCE_CATEGORY_IDENTITY_V1')
    old = parse_v4_page(FIXTURE.read_text('utf8'), **kwargs)
    assert all('image_url' not in r for r in old['observations'])
    result = parse_v4_page(FIXTURE.read_text('utf8'), snapshot_field_contract='CARD_IMAGE_V1', **kwargs)
    rows = result['observations']
    assert any(r.get('image_url') for r in rows)
    result['page'].update(run_id='TEST_ONLY', snapshot_id='TEST_ONLY')
    assert audit_v4([result['page']], rows, [source])['central_full_dom_review_passed']
    changed = deepcopy(rows); changed[0]['image_url'] = 'https://example.invalid/other.jpg'
    assert not audit_v4([result['page']], changed, [source])['central_full_dom_review_passed']


def test_read_only_store_never_creates_missing_root(tmp_path):
    from amazon_es_bestseller.highspeed.store import ReadOnlyRunStore
    root = tmp_path / 'absent'
    with pytest.raises((OSError, ValueError)):
        ReadOnlyRunStore(root)
    assert not root.exists()


def test_main_cli_has_unified_snapshot_entry():
    from amazon_es_bestseller.cli import build_parser
    args = build_parser().parse_args(['snapshot', 'status', '--root', 'TEST_ONLY', '--run-id', 'TEST_ONLY'])
    assert args.snapshot_args[0] == 'status'


def test_profile_has_finite_budgets_but_no_live_authorization():
    from amazon_es_bestseller.highspeed.snapshot_cli import task_config, validate_config
    config = task_config()
    validate_config(config)
    assert config['target_policy']['target'] == 50000
    assert sum(v['PAGE'] for v in config['source_budgets'].values()) == 5000
    assert config['approved'] is False and config['live_enabled'] is False
    bad = deepcopy(config); bad['source_budgets']['CUSTOMER_FAVORITES']['PAGE'] = 0
    with pytest.raises(ValueError): validate_config(bad)
    bad = deepcopy(config); bad['parallelism'] = 3
    with pytest.raises(ValueError): validate_config(bad)


def test_pause_bound_to_exact_plan_preserves_control_evidence(tmp_path):
    from amazon_es_bestseller.highspeed.snapshot_control import request_pause, pause_pending, release_pause
    plan = {'run_id': 'TEST_ONLY', 'target': 50000}
    assert not pause_pending(tmp_path, plan)
    first = request_pause(tmp_path, plan)
    assert pause_pending(tmp_path, plan)
    with pytest.raises(ValueError): pause_pending(tmp_path, {**plan, 'target': 20000})
    release_pause(tmp_path, plan)
    assert not pause_pending(tmp_path, plan)
    assert (tmp_path/'snapshot_pause.json').exists()
    second = request_pause(tmp_path, plan)
    assert second['token'] != first['token'] and pause_pending(tmp_path, plan)


def test_offline_main_snapshot_cannot_launch_live():
    from amazon_es_bestseller.highspeed.snapshot_cli import dispatch
    from types import SimpleNamespace
    with pytest.raises(ValueError, match='OFFLINE'):
        dispatch(SimpleNamespace(snapshot_args=['run', '--live'], offline=True))


@pytest.mark.parametrize('spain,access,expected', [(True, 'NORMAL', True), (False, 'NORMAL', False),
    (None, 'NORMAL', False), (True, 'CHALLENGE', False)])
def test_delivery_preflight_verifies_diagnostics_not_method_return(tmp_path, spain, access, expected):
    from amazon_es_bestseller.highspeed.delivery_preflight import verify_session_delivery
    class Session:
        calls = 0
        def ensure_spain_delivery(self, code): self.calls += 1
        def delivery_diagnostics(self):
            return '<html>TEST_ONLY</html>', {'delivery_is_spain': spain, 'access_status': access}
    session = Session(); out = tmp_path/'preflight'
    if expected:
        result = verify_session_delivery(session, out)
        assert result['status'] == 'VERIFIED'
    else:
        with pytest.raises(ValueError): verify_session_delivery(session, out)
    assert session.calls == 1
    assert json.loads((out/'delivery_gate.json').read_text('utf8'))['business_http_receipt'] is False


def test_card_image_cannot_take_other_products_or_foreign_hosts():
    from bs4 import BeautifulSoup
    from amazon_es_bestseller.highspeed.snapshot_images import card_image
    html = '''<div><a href="/dp/B012345678"><img src="https://m.media-amazon.com/a.jpg"></a>
    <a href="/dp/B087654321"><img src="https://m.media-amazon.com/foreign.jpg"></a></div>'''
    card = BeautifulSoup(html, 'lxml').div
    row = card_image(card, '/dp/B012345678', 'https://www.amazon.es/')
    assert row['image_url'] == 'https://m.media-amazon.com/a.jpg'
    card.select_one('img')['src'] = 'https://unrelated.invalid/a.jpg'
    assert card_image(card, '/dp/B012345678', 'https://www.amazon.es/')['image_url'] is None


def test_receipt_lookup_keeps_order_and_excludes_restricted_receipts():
    from amazon_es_bestseller.highspeed.receipt_lookup import ReceiptLookup
    receipts = [dict(kind='PAGE', request_url='u', access_state='NORMAL', request_id='first'),
                dict(kind='PAGE', request_url='u', access_state='CHALLENGE', request_id='blocked'),
                dict(kind='PAGE', request_url='u', access_state='NORMAL', request_id='last'),
                dict(kind='ACP', page_key='p', access_state='NORMAL', request_id='native')]
    lookup = ReceiptLookup(receipts)
    assert [r['request_id'] for r in lookup.pages['u']] == ['first', 'last']
    assert lookup.acp['p'][0]['request_id'] == 'native'
    assert lookup.pages.get('unknown', []) == []


def test_fifty_thousand_union_does_not_hide_source_shortfall(monkeypatch):
    from amazon_es_bestseller.highspeed import three_metrics as module
    from amazon_es_bestseller.highspeed.global_target import MAIN_PRIORITY_50000_POLICY
    from test_three_source_metrics import real_rows
    rows = [{**deepcopy(real_rows()[0]), 'source_type': k} for k in MAIN_PRIORITY_50000_POLICY['source_minima']]
    sizes = {'BEST_SELLERS': 23500, 'NEW_RELEASES': 23500, 'CUSTOMER_FAVORITES': 3000}
    def candidates(items):
        kind = items[0]['source_type']
        return [{'eligible_for_target': True, 'target_review_group': f'TEST_ONLY_{kind}_{i}',
                 'normalized_product_url': f'TEST_ONLY_{kind}_{i}'} for i in range(sizes[kind])]
    monkeypatch.setattr(module, 'build_candidates_v4', candidates)
    result = module.measure_three(rows, 'TEST_ONLY', target_policy=MAIN_PRIORITY_50000_POLICY)
    assert result['target_met'] and result['global_target'] == 50000
    sizes.update(BEST_SELLERS=23501, CUSTOMER_FAVORITES=2999)
    result = module.measure_three(rows, 'TEST_ONLY', target_policy=MAIN_PRIORITY_50000_POLICY)
    assert result['F_global_conservative_review_groups'] == 50000 and not result['target_met']
    assert result['sources']['CUSTOMER_FAVORITES']['shortfall'] == 1


def test_new_task_clock_uses_fixed_twenty_four_hours(tmp_path):
    from amazon_es_bestseller.highspeed.global_target import MAIN_PRIORITY_50000_POLICY
    from amazon_es_bestseller.highspeed.task_clock import start_task_clock
    from amazon_es_bestseller.multisource.storage import read_sealed
    from datetime import datetime, timezone
    plan = {'run_id': 'TEST_ONLY', 'target_policy': MAIN_PRIORITY_50000_POLICY}
    path = tmp_path/'clock.json'
    start_task_clock(path, plan, 'TEST_ONLY', now=datetime(2026, 10, 28, tzinfo=timezone.utc))
    first = path.read_bytes()
    start_task_clock(path, plan, 'TEST_ONLY', now=datetime(2026, 10, 29, tzinfo=timezone.utc))
    assert path.read_bytes() == first and read_sealed(path)['total_hours'] == 24


def test_incremental_metrics_equal_full_with_overlap_conflicts_and_exclusions():
    from test_three_source_metrics import real_rows
    from amazon_es_bestseller.highspeed.snapshot_metrics import SnapshotMetricsIndex
    from amazon_es_bestseller.highspeed.three_metrics import measure_three
    from amazon_es_bestseller.highspeed.global_target import MAIN_PRIORITY_50000_POLICY
    rows = real_rows()
    rows += [{**deepcopy(rows[0]), 'source_type':'NEW_RELEASES', 'observation_id':'TEST_ONLY_OVERLAP'},
             {**deepcopy(rows[0]), 'binding_doubts':['TEST_ONLY_CONFLICT'], 'observation_id':'TEST_ONLY_CONFLICT'},
             {**deepcopy(rows[0]), 'origin':'OFFLINE_FIXTURE', 'observation_id':'TEST_ONLY_EXCLUDED'}]
    index = SnapshotMetricsIndex('TEST_ONLY', MAIN_PRIORITY_50000_POLICY)
    for n in range(0,len(rows),7):
        index.add(rows[n:n+7])
        assert index.measure() == measure_three(rows[:n+7], 'TEST_ONLY', target_policy=MAIN_PRIORITY_50000_POLICY)


def test_read_only_copy_sees_wal_without_touching_source_shm(tmp_path):
    import hashlib
    from amazon_es_bestseller.highspeed.store import RunStore, ReadOnlyRunStore
    source = RunStore(tmp_path/'store')
    try:
        source.db.execute('CREATE TABLE test_only_wal (value INTEGER)')
        source.db.execute('INSERT INTO test_only_wal VALUES (42)')
        def fingerprint():
            return {p.name:(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest())
                    for p in source.root.glob('index.sqlite3*')}
        before = fingerprint()
        reader = ReadOnlyRunStore(source.root)
        try:
            assert reader.db.execute('SELECT value FROM test_only_wal').fetchone()[0] == 42
            import sqlite3
            with pytest.raises(sqlite3.OperationalError):reader.db.execute('UPDATE test_only_wal SET value=999')
        finally: reader.close()
        assert fingerprint() == before
    finally: source.close()


def test_delivery_diagnostics_failure_saves_blocked_evidence(tmp_path):
    from amazon_es_bestseller.highspeed.delivery_preflight import verify_session_delivery
    class Session:
        def ensure_spain_delivery(self, code): raise RuntimeError('TEST_ONLY UI failed')
        def delivery_diagnostics(self): raise RuntimeError('TEST_ONLY closed page')
    out=tmp_path/'failure'
    with pytest.raises(ValueError,match='DELIVERY_PREFLIGHT_STOPPED'):
        verify_session_delivery(Session(),out)
    result=json.loads((out/'delivery_gate.json').read_text('utf8'))
    assert result['status']=='BLOCKED' and result['access_status']=='UNKNOWN'
    assert result['automatic_retry'] is False
