"""Offline replay, reviewed staging proposals, and immutable V4 exports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import time
import uuid

from ..multisource.storage import immutable_json, immutable_bytes, canonical_bytes
from .contracts import DEFAULT_CONFIG
from .v3_links import NORMALIZER_VERSION
from .v4_sources import SEEDS, DISABLED_SUPPLY, source_for_url
from .v4_adapters import parse_v4_page
from .v4_quality import audit_v4, build_candidates_v4, contribution_v4


def csv_bytes(records):
    if not records:
        return b'\xef\xbb\xbf'
    keys = list(dict.fromkeys(k for r in records for k in r))
    stream = io.StringIO(newline='')
    w = csv.DictWriter(stream, fieldnames=keys, lineterminator='\n'); w.writeheader()
    for row in records:
        # CSV is an interchange export: JSON stores all nested memberships and
        # field proofs. Never collapse them to a display-only summary.
        w.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v
                    for k, v in row.items()})
    return stream.getvalue().encode('utf-8-sig')


def export_v4(pages, observations, sources, output, *, run_id, stage='T0', timings=None, request_receipts=None,
              accepted_empty_urls=()):
    directory = Path(output) / ('report_v4_' + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    qa_started = time.perf_counter()
    quality = audit_v4(pages, observations, sources, require_live=stage != 'T0')
    qa_seconds = time.perf_counter() - qa_started
    candidates = build_candidates_v4(observations)
    from .v4_coverage import loading_coverage, assess_capacity_readiness
    loading=loading_coverage(pages,sources)
    readiness=assess_capacity_readiness(pages,sources,loading,
        central_dom_review_passed=quality['central_full_dom_review_passed'] and stage=='T1',
        accepted_empty_urls=accepted_empty_urls)
    strict_roots_passed={s['url'] for s in SEEDS}.issubset({p['page_url'] for p in pages
        if p['status']=='VERIFIED_WITH_PRODUCTS'})
    conservative = len({c['target_review_group'] for c in candidates if c['eligible_for_target']})
    coverage = {'schema_version': 4, 'run_id': run_id, 'stage': stage,
                'loading_verification':loading,
                'capacity_planning_readiness':readiness,
                'origin': 'OFFLINE_FIXTURE' if stage == 'T0' else 'LIVE_CAPTURE',
                'sources': sources, 'pages': pages, 'disabled_supply': DISABLED_SUPPLY,
                'raw_observations': len(observations), 'normalized_unique_links': len(candidates),
                'conservative_review_groups': conservative, 'target': 30000,
                'target_shortfall': max(0, 30000 - conservative), 'completeness_status': 'UNKNOWN',
                'fresh_live_observations': sum(r['origin'] == 'LIVE_CAPTURE' for r in observations)}
    performance = {'schema_version': 4, 'run_id': run_id, 'stage': stage, **(timings or {}),
                   'central_full_dom_review_seconds': qa_seconds,
                   'live_candidates_per_hour': None, 'live_request_success_rate': None,
                   'ten_hour_collection_goal_demonstrated': False,
                   'capacity_verified': False}
    if request_receipts is not None:
        from ..multisource.storage import read_sealed
        verified = []
        for supplied in request_receipts:
            actual = read_sealed(supplied['evidence_path'])
            if actual['request_id'] != supplied['request_id'] or actual['run_id'] != run_id or actual.get('origin') != 'LIVE_CAPTURE':
                raise ValueError('V4_PERFORMANCE_ACTUAL_RUN_RECEIPTS_REQUIRED')
            if hashlib.sha256(Path(actual['html_path']).read_bytes()).hexdigest() != actual['sha256']:
                raise ValueError('V4_PERFORMANCE_RECEIPT_BODY_SHA_MISMATCH')
            verified.append(actual)
        if len({r['request_id'] for r in verified}) != len(verified):
            raise ValueError('V4_PERFORMANCE_DUPLICATE_RECEIPT')
        successes = sum(r.get('status_code') == 200 and r.get('access_state') == 'NORMAL' for r in verified)
        kinds = sorted({r['source_type'] for r in verified})
        performance.update(actual_request_receipt_count=len(verified), successful_HTTP_200_NORMAL=successes,
            live_request_success_rate=successes / len(verified) if verified else None,
            collection_request_operation_seconds=sum(r.get('duration_seconds', 0) for r in verified),
            mean_request_seconds_by_source={k: sum(r.get('duration_seconds', 0) for r in verified if r['source_type'] == k)
                / sum(r['source_type'] == k for r in verified) for k in kinds},
            measurement_scope='Charged PAGE/ACP operations including browser wait; static assets not counted. Capacity and active candidate rate unverified.')
    artifacts = {
        'candidate_snapshot_v4.csv': csv_bytes(candidates),
        'observations_v4.jsonl': b''.join(canonical_bytes(r) + b'\n' for r in observations),
        'source_coverage_v4.json': canonical_bytes(coverage),
        'data_quality_v4.json': canonical_bytes(quality),
        'performance_v4.json': canonical_bytes(performance),
        'source_contribution_v4.csv': csv_bytes(contribution_v4(observations, candidates)),
    }
    refs = []
    for name, body in artifacts.items():
        immutable_bytes(directory / name, body)
        refs.append({'name': name, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()})
    immutable_json(directory / 'manifest.json', {'schema_version': 4, 'run_id': run_id, 'stage': stage,
                    'passed': quality['central_full_dom_review_passed'] and (stage == 'T0' or
                        stage=='T1' and loading['all_loading_capabilities_observed']
                        and (readiness['ready_for_T2_plan'] if accepted_empty_urls else strict_roots_passed)),
                    'strict_eight_entry_products_passed':strict_roots_passed,
                    'accepted_empty_source_urls':sorted(set(accepted_empty_urls)),
                    'acceptance_policy':'USER_EXPLICIT_RETAIN_REAL_SOURCE_EMPTY' if accepted_empty_urls else 'STRICT_EIGHT_PRODUCT_ROOTS',
                    'pages': pages, 'sources': sources, 'observations': observations, 'files': refs,
                    'loading_verification':loading,
                    'capacity_planning_readiness':readiness,
                    'live_protocol_validation': 'OBSERVED' if stage=='T1' and loading['all_loading_capabilities_observed'] else 'UNVERIFIED',
                    'new_live_candidates': len(candidates) if stage != 'T0' else 0,
                    'strict_independent_buyable_skus_confirmed': False})
    # Independently re-read both formats; counts and membership bytes must agree.
    with (directory / 'candidate_snapshot_v4.csv').open(encoding='utf-8-sig', newline='') as f:
        reread = list(csv.DictReader(f))
    history = [json.loads(line) for line in (directory / 'observations_v4.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(reread) == len(candidates) and len(history) == len(observations)
    assert {r['normalized_product_url'] for r in reread} == {c['normalized_product_url'] for c in candidates}
    for saved, expected in zip(reread, candidates):
        assert json.loads(saved['source_memberships']) == expected['source_memberships']
    for ref in refs:
        assert hashlib.sha256((directory / ref['name']).read_bytes()).hexdigest() == ref['sha256']
    return {'directory': str(directory.resolve()), 'central_dom_review_passed': quality['central_full_dom_review_passed'],
            'normalized_unique_links': len(candidates), 'conservative_review_groups': conservative,
            'raw_observations': len(observations), 'new_live_candidates': 0 if stage == 'T0' else len(candidates),
            'errors': quality['errors']}


def replay_v4(manifest_path, output):
    started = time.perf_counter()
    path = Path(manifest_path).resolve(); specs = json.loads(path.read_text(encoding='utf8'))
    run_id = 'v4_offline_' + uuid.uuid4().hex[:12]
    pages, observations, sources = [], [], []
    seed_urls = {s['url'] for s in SEEDS}
    for proof in specs:
        if proof['url'] not in seed_urls or proof['fixture'] == 'F06_deals_root_complete.html':
            continue
        source = source_for_url(proof['url'])
        source['page_urls'] = [proof['url']]
        if source['source_id'] in {s['source_id'] for s in sources}:
            raise ValueError('V4_ONE_STABLE_FRAME_PER_SOURCE_REQUIRED')
        sources.append(source)
        html_path = path.parent / proof['fixture']; body = html_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != proof['sha256']:
            raise ValueError('V4_FIXTURE_SHA_MISMATCH')
        result = parse_v4_page(body.decode('utf8'), source=source, page_url=proof['url'], run_id=run_id,
                               observed_at=proof['observed_at'], evidence_path=str(html_path),
                               origin='OFFLINE_FIXTURE', http_status=None)
        pages.append({**result['page'], 'run_id': run_id, 'snapshot_id': run_id})
        observations.extend(result['observations'])
    return export_v4(pages, observations, sources, output, run_id=run_id,
                      timings={'offline_parse_seconds': time.perf_counter() - started, 'new_network_requests': 0})


def prepare_v4(manifest_path, output, runtime_root, shared_gate):
    """Draft-only exact eight-source T1 scope; no approval is fabricated."""
    directory = Path(output) / ('prepare_v4_' + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    run_id = 'v4_t1_' + uuid.uuid4().hex[:12]
    specs = json.loads(Path(manifest_path).read_text(encoding='utf8'))
    sources = []; scopes = {}
    for seed in SEEDS:
        proof = next(s for s in specs if s['url'] == seed['url'] and s['fixture'] != 'F06_deals_root_complete.html')
        evidence = (Path(manifest_path).resolve().parent / proof['fixture']).resolve()
        if hashlib.sha256(evidence.read_bytes()).hexdigest() != proof['sha256']:
            raise ValueError('V4_SEED_PROOF_SHA_MISMATCH')
        s = source_for_url(seed['url']); s['page_urls'] = [seed['url']]; sources.append(s)
        scopes[s['source_id']] = {k: s[k] for k in ('source_type', 'source_subtype', 'parser_profile', 'page_urls')}
        scopes[s['source_id']].update(allow_native_acp=s['source_type'] in {'BEST_SELLERS', 'NEW_RELEASES'},
                                     evidence={'path': str(evidence), 'sha256': proof['sha256'], 'reviewed': True})
    plan = {**DEFAULT_CONFIG, 'mode': 'HIGH_SPEED_V4', 'snapshot_contract_version': 4,
            'normalizer_version': NORMALIZER_VERSION, 'run_id': run_id, 'snapshot_id': run_id,
            'stage': 'T1', 'origin': 'LIVE_CAPTURE', 'live_enabled': False, 'sources': sources,
            'shared_access_gate_path': str(Path(shared_gate).resolve()), 'max_requests_per_run': 50,
            'max_requests_per_day': 50, 'max_pages_per_run': 20, 'max_duration_hours': 1,
            'max_raw_observations_per_run': 10000, 'history_observation_limit': 20000,
            'target_unique_links': 30000, 'probe_all_sources': True}
    immutable_json(directory / 'plan.pending.json', plan)
    immutable_json(directory / 'authorization.pending.json', {'approved': False, 'stage': 'T1',
        'snapshot_contract_version': 4, 'run_id': run_id, 'sources': scopes,
        'store_root': str((Path(runtime_root) / run_id / 'store').resolve()),
        'reviewed_at': None, 'expires_at': None, 'approved_by': None,
        'note': 'No LIVE execution authorized. Approval must bind final enabled plan SHA, exact limits and stop conditions.'})
    immutable_json(directory / 'protocol_scope.pending.json', {
        'new_live_requests_authorized': False, 'exact_page_urls': [s['url'] for s in SEEDS],
        'maximum_charged_requests': 50, 'maximum_PAGE_requests': 20, 'duration_hours': 1,
        'request_pacing_seconds': 3, 'category_cooldown_seconds': 0, 'parallelism': 1,
        'native_acp': 'Only actual aborted native BEST/NEW owner-bound requests; no guessed payloads.',
        'new_source_dynamic_loading': 'Protocol discovery pending controlled T1; no unmetered XHR.',
        'new_pagination': 'Save discovered exact hrefs; separate scope review before visits.',
        'stop_conditions': ['403', '429', 'CHALLENGE', 'CAPTCHA', 'ROBOT_CHECK', 'ACCESS_DENIED', 'UNKNOWN',
                            'NETWORK_ERROR', 'GATE_NON_NORMAL', 'BUDGET_EXHAUSTED', 'AUTH_EXPIRED'],
        'T2_approved': False, 'T3_approved': False})
    return {'directory': str(directory.resolve()), 'run_id': run_id, 'approved': False, 'new_live_requests': 0}
