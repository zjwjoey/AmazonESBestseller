"""Offline phase measurements over immutable evidence. No supply extrapolation."""
from pathlib import Path
import time

from .v4_adapters import parse_v4_page
from .v4_quality import audit_v4, build_candidates_v4
from .three_metrics import measure_three, qualify_three
from .v4_workflows import csv_bytes
from ..multisource.storage import immutable_json


def benchmark_saved_snapshot(store, run_id, output):
    start = time.perf_counter()
    run = store.get_run(run_id); pages = store.pages(run_id); rows = list(store.iter_observations(run_id))
    database_seconds = time.perf_counter() - start
    start = time.perf_counter()
    bodies = {p['evidence_path']: Path(p['evidence_path']).read_bytes() for p in pages}
    file_seconds = time.perf_counter() - start
    start = time.perf_counter(); parsed_count = 0
    for p in pages:
        source = p.get('source') or next(s for s in run['plan']['sources'] if s['source_id'] == p['source_id'])
        parsed = parse_v4_page(bodies[p['evidence_path']].decode('utf8'), source=source, page_url=p['page_url'],
            run_id=run_id, snapshot_id=p.get('snapshot_id'), observed_at=p['observed_at'], evidence_path=p['evidence_path'],
            http_status=p.get('http_status'), origin=p['origin'], parser_profile_version=p['parser_version'],
            page_number=source['page_urls'].index(p['page_url'])+1,
            snapshot_metadata_contract=p.get('snapshot_metadata_contract'), snapshot_field_contract=p.get('snapshot_field_contract'))
        parsed_count += len(parsed['observations'])
    parse_seconds = time.perf_counter() - start
    start = time.perf_counter()
    quality = audit_v4(pages, rows, run['plan']['sources'], require_live=True)
    audit_seconds = time.perf_counter() - start
    start = time.perf_counter()
    metrics = measure_three(rows, run_id, target_policy=run['plan']['target_policy'])
    metrics_seconds = time.perf_counter() - start
    start = time.perf_counter()
    _, qualified, _, _ = qualify_three(rows, run_id)
    payload = csv_bytes(build_candidates_v4(qualified))
    serialize_seconds = time.perf_counter() - start
    result = {'run_id': run_id, 'pages': len(pages), 'observations': len(rows), 'parsed_observations': parsed_count,
        'phase_seconds': {'database_and_projection': database_seconds, 'HTML_file_read': file_seconds,
                         'parse': parse_seconds, 'full_DOM_validation_including_repeat_parse_and_hash': audit_seconds,
                         'full_metrics': metrics_seconds, 'candidate_aggregation_and_CSV': serialize_seconds},
        'CSV_bytes': len(payload), 'DOM_review_passed': quality['central_full_dom_review_passed'],
        'measured_qualified_groups': metrics['F_global_conservative_review_groups'],
        'quantity_status_not_modified': True, 'production_acceptance': 'NOT_EVALUATED',
        'new_network_requests': 0, 'translation_API_calls': 0,
        'note': 'Separate measured phases; cache effects apply. No claim of 50k supply, speed or production acceptance.'}
    immutable_json(Path(output), result)
    return result
