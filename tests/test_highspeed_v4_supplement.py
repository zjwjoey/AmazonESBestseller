"""Offline supplementary plans use real saved navigation, never fresh HTTP."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.highspeed import v4_supplement as supplement
from amazon_es_bestseller.highspeed.v4_frontier import extract_page_navigation
from amazon_es_bestseller.highspeed.v4_authorization import validate_v4_authorization
from amazon_es_bestseller.multisource.storage import immutable_json, digest
from test_highspeed_v4_execution import setup_scope
from test_highspeed_v4_pagination_chain import real_chain, FIXTURES


def reference(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture(scope='module')
def saved(tmp_path_factory):
    directory = tmp_path_factory.mktemp('supplement_real_saved_DOM')
    plan, auth = setup_scope(directory)
    sources = [deepcopy(s) for s in plan['sources'] if s['source_subtype'] != 'LIGHTNING_DEAL_FILTER']
    chain = real_chain()
    chain['pagination_evidence'].pop(); chain['page_urls'].pop()
    sources = [chain if s['source_id'] == chain['source_id'] else s for s in sources]
    pages = []
    for s in sources:
        for url in s['page_urls']:
            path = (Path(chain['pagination_evidence'][0]['evidence_path']) if url == chain['actual_source_url']
                    else FIXTURES / 'favorites_chain_page2.html' if url in chain['page_urls']
                    else Path(auth['sources'][s['source_id']]['evidence']['path']))
            pages.append({**{k: s[k] for k in ('source_id', 'source_type', 'source_subtype')},
                'page_url': url, 'evidence_path': str(path),
                'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'observed_at': '2026-10-15T02:00:00Z', 'http_status': 200, 'access_status': 'NORMAL',
                'status': 'VERIFIED_WITH_PRODUCTS', 'completeness_status': 'UNKNOWN'})
    best = next(s for s in sources if s['source_type'] == 'BEST_SELLERS')
    rows, _ = extract_page_navigation(next(p for p in pages if p['source_id'] == best['source_id']), best)
    new_row = next(r for r in rows if r['url'].endswith('/kitchen/ref=zg_bs_kitchen_sm'))
    aliases = [r for r in rows if '/kitchen/ref=' in r['url']]
    next_rows, _ = extract_page_navigation(next(p for p in pages if p['page_url'] == chain['page_urls'][-1]), chain)
    next_row = next(r for r in next_rows if r['status'] == 'PENDING_PAGINATION_REVIEW')
    plan.update(stage='T2', sources=sources)
    prior_refs = {}
    for stage in ('T0', 'T1'):
        path = directory / (stage + '.json'); immutable_json(path, {'TEST_ONLY': True})
        prior_refs[stage] = reference(path)
    auth['prerequisites'] = prior_refs
    auth_path = directory / 'prior_approval.json'; immutable_json(auth_path, auth)
    pack = {'run_id': 'prior_test_only', 'sources': sources, 'pages': pages,
        'capacity_execution': {'plan': plan, 'authorization_references': [reference(auth_path)]},
        'capacity_audit': {'conservative_review_groups': 619, 'sample_goal': 2000, 'sample_goal_shortfall': 1381}}
    return pack, new_row, next_row, aliases, prior_refs


def inputs(tmp_path, monkeypatch, saved):
    pack, new_row, next_row, aliases, priors = deepcopy(saved)
    t2 = tmp_path / 'T2.json'; immutable_json(t2, {'TEST_ONLY': True})
    t2_ref = reference(t2)
    nav = tmp_path / 'nav'; nav.mkdir()
    rows = [new_row, next_row]
    entries = [{'exact_url': r['url'], 'status': r['status'], 'provenance': [r],
                'approved': False, 'live_enabled': False} for r in rows]
    inventory = {'schema_version': 1, 'kind': 'OFFLINE_NAVIGATION_REVIEW_QUEUE',
        't2_reference': t2_ref, 'source_run_id': pack['run_id'], 'approved': False,
        'live_enabled': False, 'T3_execution_enabled': False, 'entries': entries}
    immutable_json(nav / 'navigation_inventory.json', inventory)
    (nav / 'pending_exact_urls.txt').write_text('\n'.join(e['exact_url'] for e in entries) + '\n', 'utf8')
    manifest = {'t2_reference': t2_ref, 'approved': False, 'live_enabled': False,
        'T3_execution_enabled': False, 'files': [{'name': p.name, 'sha256': reference(p)['sha256']}
            for p in sorted(nav.iterdir())]}
    immutable_json(nav / 'manifest.json', manifest)
    selection = {'schema_version': 1, 'kind': 'V4_T2_SUPPLEMENT_SELECTION',
        'reason': 'TEST_ONLY bounded saved-DOM selection',
        'existing_source_ids': [s['source_id'] for s in pack['sources']],
        'new_source_urls': [new_row['url']], 'pagination_urls': [next_row['url']],
        'budgets': {'max_requests_per_run': 120, 'max_pages_per_run': 20, 'max_duration_hours': 1,
            'max_native_requests_per_source': 20, 'max_dynamic_requests_per_source': 20,
            'max_raw_observations_per_run': 50000, 'history_observation_limit': 100000,
            'target_unique_links': 2000}}
    path = tmp_path / 'selection.json'; immutable_json(path, selection)
    seen = []
    empty = {'page_url': 'https://www.amazon.es/deals?bubble-id=deals-collection-lightning-deals',
             'status': 'EXPLICIT_EMPTY', 'evidence_path': 'test_only_empty', 'evidence_sha256': 'test_only'}
    def verify(ref, stage):
        seen.append(stage)
        if stage == 'T2': return pack
        assert ref == priors[stage]
        return {'pages': [empty], 'accepted_empty_source_urls': [empty['page_url']]} if stage == 'T1' else {}
    monkeypatch.setattr(supplement, 'verify_stage_reference', verify)
    args = [t2_ref, reference(nav / 'manifest.json'), reference(path), tmp_path / 'out',
            tmp_path / 'new_runtime', pack['capacity_execution']['plan']['shared_access_gate_path']]
    return args, pack, selection, inventory, seen


def update_selection(args, value):
    path = Path(args[2]['path']); path.write_text(json.dumps(value), 'utf8'); args[2] = reference(path)


def test_supplement_is_fresh_disabled_exact_scope_and_retains_real_empty(tmp_path, monkeypatch, saved):
    args, pack, selection, _, seen = inputs(tmp_path, monkeypatch, saved)
    result = supplement.prepare_t2_supplement(*args)
    directory = Path(result['directory'])
    plan = json.loads((directory / 'plan.pending.json').read_text('utf8'))
    auth = json.loads((directory / 'authorization.pending.json').read_text('utf8'))
    assert not plan['live_enabled'] and not auth['approved'] and not result['T3_enabled']
    assert plan['run_id'] != pack['run_id'] and not plan.get('seed_reference')
    assert result['source_count'] == 8 and result['exact_PAGE_count'] == 10
    assert result['nominal_scope_request_ceiling'] == 110
    assert result['historical_candidates_in_new_target'] == 0
    assert result['pending_plan_sha256'] == digest(plan) == auth['plan_sha256']
    assert result['enabled_plan_sha256_preview'] == digest({**plan, 'live_enabled': True})
    assert (directory / '真实空源保留.json').read_text('utf8').find('EXPLICIT_EMPTY') >= 0
    assert seen == ['T2', 'T0', 'T1']
    assert auth['reviewed_at'] is None and auth['expires_at'] is None and auth['approval_id'] is None
    with pytest.raises(ValueError, match='LIVE_AUTHORIZATION_REQUIRED'):
        validate_v4_authorization(plan, auth, {'state': 'NORMAL'}, store_root=auth['store_root'])
    manifest = json.loads((directory / 'manifest.json').read_text('utf8'))
    assert all(reference(directory / f['name'])['sha256'] == f['sha256'] for f in manifest['files'])


@pytest.mark.parametrize('bad', ['invented', 'duplicate', 'missing_family', 'not_pending', 'skipped_page'])
def test_selection_cannot_expand_or_bypass_navigation_chain(tmp_path, monkeypatch, saved, bad):
    args, pack, selection, _, _ = inputs(tmp_path, monkeypatch, saved)
    if bad == 'invented': selection['new_source_urls'].append('https://www.amazon.es/gp/bestsellers/invented')
    elif bad == 'duplicate': selection['new_source_urls'] *= 2
    elif bad == 'missing_family': selection['existing_source_ids'] = selection['existing_source_ids'][:1]
    elif bad == 'not_pending': selection['new_source_urls'] = [pack['sources'][0]['actual_source_url']]
    else: selection['pagination_urls'] = [selection['pagination_urls'][0].replace('page=3', 'page=4')]
    update_selection(args, selection)
    with pytest.raises(ValueError): supplement.prepare_t2_supplement(*args)
    assert not Path(args[3]).exists()


@pytest.mark.parametrize('key,value', [('max_requests_per_run', 109), ('max_pages_per_run', 9),
    ('max_duration_hours', 11), ('max_duration_hours', float('inf')), ('target_unique_links', 1381),
    ('max_requests_per_run', True), ('parallelism', 2)])
def test_budget_and_goal_validation_before_output(tmp_path, monkeypatch, saved, key, value):
    args, _, selection, _, _ = inputs(tmp_path, monkeypatch, saved)
    selection['budgets'][key] = value; update_selection(args, selection)
    with pytest.raises(ValueError): supplement.prepare_t2_supplement(*args)
    assert not Path(args[3]).exists()


@pytest.mark.parametrize('artifact', ['selection', 'navigation', 'html'])
def test_tampered_evidence_or_selection_never_produces_plan(tmp_path, monkeypatch, saved, artifact):
    args, pack, _, _, _ = inputs(tmp_path, monkeypatch, saved)
    if artifact == 'selection': Path(args[2]['path']).write_text('{}', 'utf8')
    elif artifact == 'navigation': (Path(args[1]['path']).parent / 'navigation_inventory.json').write_text('{}', 'utf8')
    else:
        parent = next(p for p in pack['pages'] if p['source_type'] == 'BEST_SELLERS')
        p = tmp_path / 'changed.html'; p.write_text('changed', 'utf8'); parent['evidence_path'] = str(p)
    with pytest.raises(ValueError): supplement.prepare_t2_supplement(*args)
    assert not Path(args[3]).exists()


def test_prior_review_failure_is_not_waived_by_existing_pass_flag(tmp_path, monkeypatch, saved):
    args, _, _, _, _ = inputs(tmp_path, monkeypatch, saved)
    def reject(*a): raise ValueError('V4_PRIOR_STAGE_DOM_REVIEW_FAILED')
    monkeypatch.setattr(supplement, 'verify_stage_reference', reject)
    with pytest.raises(ValueError, match='DOM_REVIEW_FAILED'): supplement.prepare_t2_supplement(*args)
    assert not Path(args[3]).exists()


def test_supplement_cli_is_offline_only(tmp_path, monkeypatch):
    from amazon_es_bestseller.highspeed.v4_cli import main
    p = tmp_path / 'reference.json'; immutable_json(p, {'TEST_ONLY': True})
    calls = []
    monkeypatch.setattr(supplement, 'prepare_t2_supplement', lambda *a: calls.append(a) or {'new_network_requests': 0})
    argv = ['prepare-t2-supplement', '--t2', str(p), '--navigation', str(p), '--selection', str(p),
            '--out', str(tmp_path), '--runtime-root', str(tmp_path / 'runtime'), '--shared-gate', str(tmp_path / 'gate')]
    assert main(argv)['new_network_requests'] == 0 and len(calls) == 1
    with pytest.raises(SystemExit): main([*argv, '--live'])


def test_ranking_alias_group_is_review_only_and_keeps_unknown_filters():
    base = 'https://www.amazon.es/gp/bestsellers/kitchen'
    assert supplement.ranked_review_key(base + '/ref=zg_bs_kitchen_sm') == base
    assert supplement.ranked_review_key(base + '/ref=zg_bs_nav_kitchen_0') == base
    for tail in ('?unknown=1', '?unknown=2', '?x=%2f', '?x=%2F', '#section'):
        assert supplement.ranked_review_key(base + '/ref=zg_bs_kitchen_sm' + tail) == base + tail
    assert supplement.ranked_review_key(base + '/ref=unknown') == base + '/ref=unknown'


@pytest.mark.parametrize('field,value', [('anchor_dom_index', -1), ('label_raw', 'invented category')])
def test_rehashed_inventory_cannot_replace_actual_dom_provenance(tmp_path, monkeypatch, saved, field, value):
    args, _, _, inventory, _ = inputs(tmp_path, monkeypatch, saved)
    inventory['entries'][0]['provenance'][0][field] = value
    directory = Path(args[1]['path']).parent
    path = directory / 'navigation_inventory.json'
    path.write_text(json.dumps(inventory), 'utf8')
    manifest = json.loads(Path(args[1]['path']).read_text('utf8'))
    for f in manifest['files']:
        f['sha256'] = reference(directory / f['name'])['sha256']
    Path(args[1]['path']).write_text(json.dumps(manifest), 'utf8')
    args[1] = reference(Path(args[1]['path']))
    with pytest.raises(ValueError, match='NAVIGATION_DOM_MISMATCH'):
        supplement.prepare_t2_supplement(*args)
    assert not Path(args[3]).exists()
