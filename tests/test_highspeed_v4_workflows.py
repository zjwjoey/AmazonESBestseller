from copy import deepcopy
import csv
import hashlib
import json

import pytest

from test_highspeed_v4_adapters import FIXTURES, MANIFEST, parse_fixture
from amazon_es_bestseller.highspeed.v4_sources import source_for_url, SEEDS
from amazon_es_bestseller.highspeed.v4_workflows import replay_v4, prepare_v4
from amazon_es_bestseller.highspeed.v4_quality import audit_v4, build_candidates_v4, contribution_v4
from amazon_es_bestseller.highspeed.v4_authorization import validate_plan_v4


def test_complete_offline_exports_central_dom_and_roundtrip(tmp_path):
    result = replay_v4(FIXTURES / 'manifest.json', tmp_path)
    assert result['central_dom_review_passed'], result['errors'][:2]
    assert result['raw_observations'] == 336
    assert result['new_live_candidates'] == 0
    path = tmp_path / result['directory'].split('report_v4_')[-1]
    from pathlib import Path
    path = Path(result['directory'])
    manifest = json.loads((path / 'manifest.json').read_text(encoding='utf8'))
    assert manifest['stage'] == 'T0'
    assert all(r['origin'] == 'OFFLINE_FIXTURE' for r in manifest['observations'])
    for f in manifest['files']:
        assert hashlib.sha256((path / f['name']).read_bytes()).hexdigest() == f['sha256']


def test_central_review_detects_plausible_field_tampering():
    p = MANIFEST[0]; result = parse_fixture(p['fixture'][:-5])
    pages = [{**result['page'], 'run_id': 'offline_v4', 'snapshot_id': 'offline_v4'}]
    source = source_for_url(p['url'])
    observations = deepcopy(result['observations'])
    observations[0]['rating_value'] = 1.1
    quality = audit_v4(pages, observations, [source])
    assert not quality['central_full_dom_review_passed']
    assert quality['errors'][0]['code'] == 'OBSERVATION_DOM_MISMATCH'


def test_contribution_preserves_cross_source_memberships():
    first = parse_fixture('S06_favorites_home_complete')['observations'][0]
    other = {**deepcopy(first), 'source_type': 'DEALS', 'source_id': 'deals-test', 'observation_id': 'other'}
    records = build_candidates_v4([first, other])
    assert len(records) == 1
    assert len(records[0]['source_memberships']) == 2
    assert records[0]['strict_buyable_confirmed'] is False
    stats = contribution_v4([first, other], records)
    assert next(r for r in stats if r['source_type'] == 'DEALS')['also_in_other_sources'] == 1


def test_preparation_is_draft_only_eight_sources(tmp_path):
    result = prepare_v4(FIXTURES / 'manifest.json', tmp_path, tmp_path / 'runtime', tmp_path / 'shared_gate.json')
    from pathlib import Path
    directory = Path(result['directory'])
    p = json.loads((directory / 'plan.pending.json').read_text(encoding='utf8'))
    assert p['live_enabled'] is False
    assert p['max_requests_per_run'] == 50 and p['max_pages_per_run'] == 20
    assert len(p['sources']) == 8
    validate_plan_v4(p)
    approval = json.loads((directory / 'authorization.pending.json').read_text(encoding='utf8'))
    assert approval['approved'] is False
    assert result['new_live_requests'] == 0


@pytest.mark.parametrize('key,value', [('max_requests_per_run', True), ('request_pacing_seconds', 0),
                                     ('parallelism', 2), ('max_duration_hours', 11)])
def test_plan_rejects_invalid_caps_and_parallelism(tmp_path, key, value):
    from pathlib import Path
    result = prepare_v4(FIXTURES / 'manifest.json', tmp_path, tmp_path / 'runtime', tmp_path / 'g')
    p = json.loads((Path(result['directory']) / 'plan.pending.json').read_text(encoding='utf8'))
    p[key] = value
    with pytest.raises(ValueError):
        validate_plan_v4(p)
