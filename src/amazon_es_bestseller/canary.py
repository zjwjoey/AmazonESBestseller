"""Bounded offline Canary gates extracted from 8c302c58, without its live runner.

Ranking five-tuples and stable detail fields retain the historical comparison
contract. Current parsers, identity authority and Spain detection own the facts.
An offline pass never authorizes collection or promotes a candidate snapshot.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .access.detector import detect_access_status
from .access.location import inspect_delivery_location
from .collection.detail_v2 import parse_detail_evidence_v2
from .collection.ranking_v2 import parse_ranking_snapshot_v2
from .monitoring.ranking_identity.extract import extract_identity_from_html
from .monitoring.ranking_identity.urls import is_valid_asin
from .multisource.storage import digest

CONTRACT = 'CANARY_OFFLINE_REVIEW_V1'
EXTRACTED_FROM = '8c302c58cdeda36f1cedea013a551d9dcad30050'


def validate_canary_url(url):
    parsed = urlsplit(str(url))
    parts = parsed.path.lower().split('/')
    if (parsed.scheme != 'https' or parsed.hostname not in {'amazon.es', 'www.amazon.es'}
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not ('zgbs' in parts or parsed.path.lower().startswith('/gp/bestsellers/'))):
        raise ValueError('CANARY_REVIEWED_BESTSELLER_URL_REQUIRED')
    return str(url)


def build_canary_execution_plan(source_urls, pages_per_source=1, detail_limit=0):
    if (not isinstance(source_urls, list) or len(source_urls) != 1
            or type(pages_per_source) is not int or not 1 <= pages_per_source <= 2
            or type(detail_limit) is not int or not 0 <= detail_limit <= 5):
        raise ValueError('CANARY_SCOPE_EXCEEDED')
    return {'contract': CONTRACT, 'sources': [validate_canary_url(source_urls[0])],
            'pages_per_source': pages_per_source, 'detail_limit': detail_limit,
            'planned_network_requests': 0, 'live_authorized': False,
            'task_clock_started': False, 'execution_mode': 'OFFLINE_ONLY'}


def ranking_tuple(row):
    return (str(row.get('asin') or row.get('ranking_asin') or '').upper(),
            str(row.get('ranking_rank') or row.get('bestseller_rank') or ''),
            str(row.get('ranking_source_url') or ''), str(row.get('ranking_page_url') or ''),
            str(row.get('ranking_page_number') or row.get('page_number') or ''))


def compare_replay_tuples(observed, replayed):
    if any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
           for rows in (observed, replayed)):
        raise ValueError('CANARY_RECORD_ARRAY_REQUIRED')
    left, right = list(map(ranking_tuple, observed)), list(map(ranking_tuple, replayed))
    return {'equal': left == right, 'observed_count': len(left), 'replay_count': len(right)}


def canonical_identity_status(value):
    value = str(value or '').upper()
    return 'IDENTITY_MATCH' if value in {'MATCH', 'EXACT_ASIN', 'IDENTITY_MATCH'} else value


def compare_detail_replay(observed, replayed):
    fields = ('requested_asin', 'resolved_asin', 'parent_asin', 'variation_family_asins',
              'identity_status', 'detail_parser_version', 'detail_parser_contract_version',
              'ordered_detail_evidence', 'category_evidence')
    def index(rows):
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('CANARY_DETAIL_ARRAY_REQUIRED')
        values = {}
        for row in rows:
            asin = str(row.get('requested_asin') or '').upper()
            if not is_valid_asin(asin) or asin in values:
                raise ValueError('CANARY_DUPLICATE_OR_INVALID_DETAIL_ASIN')
            values[asin] = tuple(canonical_identity_status(row.get(key)) if key == 'identity_status'
                                 else row.get(key) for key in fields)
        return values
    return {'equal': index(observed) == index(replayed), 'observed_count': len(observed),
            'replay_count': len(replayed)}


def _verified(ref, base, protected):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('CANARY_HASH_BOUND_REFERENCE_REQUIRED')
    path = (base/str(ref['path'])).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('CANARY_EVIDENCE_SHA_MISMATCH')
    protected.add(path.parent)
    return raw


def _json(ref, base, protected):
    value = json.loads(_verified(ref, base, protected).decode('utf-8-sig'))
    if isinstance(value, dict) and 'payload' in value:
        if value.get('schema_version') != 1 or digest(value['payload']) != value.get('payload_sha256'):
            raise ValueError('CANARY_SEALED_REFERENCE_MISMATCH')
        return value['payload']
    return value


def review_saved_evidence(manifest_path, *, output=None):
    manifest_path = Path(manifest_path).resolve()
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw.decode('utf-8-sig'))
    base = manifest_path.parent
    protected = {base}
    if manifest.get('contract') != CONTRACT:
        raise ValueError('CANARY_OFFLINE_CONTRACT_REQUIRED')
    pages, details = manifest['pages'], manifest.get('details', [])
    if any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
           for rows in (pages, details)):
        raise ValueError('CANARY_PAGE_AND_DETAIL_ARRAYS_REQUIRED')
    plan = build_canary_execution_plan([manifest['source_url']], len(pages), len(details))
    source = urlsplit(plan['sources'][0])
    checks, page_reports, detail_reports, ranked_asins = {}, [], [], set()
    numbers = [page['page_number'] for page in pages]
    if numbers != list(range(1, len(pages)+1)) or any(type(n) is not int for n in numbers):
        raise ValueError('CANARY_UNIQUE_ORDERED_PAGES_REQUIRED')
    observed_details, replayed_details, all_ranks = [], [], []
    for index, page in enumerate(pages):
        url = validate_canary_url(page['url'])
        parsed_url = urlsplit(url)
        if (parsed_url.hostname != source.hostname or parsed_url.path != source.path
                or parse_qs(parsed_url.query).get('pg', ['1']) != [str(page['page_number'])]):
            raise ValueError('CANARY_PAGE_OUTSIDE_REVIEWED_SOURCE')
        expected = page.get('expected_count')
        if type(expected) is not int or not 1 <= expected <= 50:
            raise ValueError('CANARY_BOUNDED_EXPECTED_COUNT_REQUIRED')
        html = _verified(page['html'], base, protected).decode('utf-8')
        observed = _json(page['observed_records'], base, protected)
        replay = parse_ranking_snapshot_v2(html, url, page['observed_at'],
            status_code=page.get('http_status'), expected_count=expected,
            expected_count_source='CANARY_REVIEWED_SCOPE', ranking_source_url=manifest['source_url'],
            page_number=page['page_number'])
        identity = extract_identity_from_html(html, source_url=url, page_number=page['page_number'],
            expected_count=expected)['audit']
        page_checks = {'access_normal': replay['access_state'] == 'NORMAL',
            'spain_header_verified': inspect_delivery_location(html).is_spain is True,
            'ranking_complete': replay['audit']['page_authoritative'] is True,
            'identity_complete': identity['identity_complete'] is True and identity['ranking_slot_complete'] is True,
            'tuple_replay_equal': compare_replay_tuples(observed, replay['records'])['equal']}
        checks.update({f'page_{index+1}_{key}': value for key, value in page_checks.items()})
        ranked_asins.update(str(row['asin']).upper() for row in replay['records'])
        all_ranks.extend(int(row['bestseller_rank']) for row in replay['records'] if row.get('bestseller_rank') is not None)
        page_reports.append({'url': url, 'checks': page_checks, 'audit': replay['audit']})
    checks['global_rank_slots_contiguous_unique'] = all_ranks == list(range(1, sum(p['expected_count'] for p in pages)+1))
    for item in details:
        asin = str(item['asin']).upper()
        if not is_valid_asin(asin) or asin not in ranked_asins:
            raise ValueError('CANARY_DETAIL_NOT_FROM_RANKING_AUTHORITY')
        from .identity import asin_from_url
        final_url = urlsplit(item['final_url'])
        if (final_url.scheme != 'https' or final_url.hostname not in {'amazon.es', 'www.amazon.es'}
                or final_url.username or final_url.password or final_url.port not in (None, 443)
                or asin_from_url(item['final_url']) != asin):
            raise ValueError('CANARY_EXACT_DETAIL_URL_REQUIRED')
        html = _verified(item['html'], base, protected).decode('utf-8')
        observed = _json(item['observed_detail'], base, protected)
        replay = parse_detail_evidence_v2(html, asin, requested_url=item['final_url'], final_url=item['final_url'])
        detail_reports.append({'asin': asin,
            'access_normal': detect_access_status(item.get('http_status'), html).value == 'NORMAL',
            'spain_header_verified': inspect_delivery_location(html).is_spain is True,
            'identity_match': canonical_identity_status(replay['identity_status']) == 'IDENTITY_MATCH'})
        observed_details.append(observed)
        replayed_details.append(replay)
    checks['detail_pages_normal_spain_identity'] = all(all(row[key] is True for key in
        ('access_normal', 'spain_header_verified', 'identity_match')) for row in detail_reports)
    checks['detail_replay_equal'] = compare_detail_replay(observed_details, replayed_details)['equal']
    # Existing clocks may be reviewed as evidence, never started, extended or edited.
    if ('task_plan' in manifest) != ('task_clock' in manifest):
        raise ValueError('CANARY_PLAN_AND_CLOCK_PAIR_REQUIRED')
    if 'task_plan' in manifest:
        task = _json(manifest['task_plan'], base, protected)
        clock = _json(manifest['task_clock'], base, protected)
        checks['existing_clock_binding'] = clock.get('run_id') == task.get('run_id') and clock.get('plan_sha256') == digest(task)
    report = {'contract': CONTRACT, 'extracted_from': EXTRACTED_FROM, 'plan': plan,
        'checks': checks, 'passed': all(checks.values()), 'pages': page_reports, 'details': detail_reports,
        'network_requests': 0, 'live_authorized': False, 'ready_for_detail_plan': False,
        'source_completeness_claimed': False, 'production_acceptance': 'NOT_EVALUATED',
        'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest()}
    report['status'] = 'OFFLINE_EVIDENCE_CHECKS_PASSED' if report['passed'] else 'OFFLINE_EVIDENCE_CHECKS_FAILED'
    if output is not None:
        from .multisource.storage import immutable_json
        output = Path(output).resolve()
        if any(output == root or output.is_relative_to(root) or root.is_relative_to(output) for root in protected):
            raise ValueError('OUTPUT_MUST_BE_OUTSIDE_EVIDENCE')
        immutable_json(output, report)
    return report


def register(subparsers):
    parser = subparsers.add_parser('canary', help='Offline bounded evidence checks only; no live runner')
    sub = parser.add_subparsers(dest='canary_operation', required=True)
    plan = sub.add_parser('plan', help='Offline scope proposal; zero network requests')
    plan.add_argument('--source-url', action='append', required=True)
    plan.add_argument('--pages-per-source', type=int, default=1)
    plan.add_argument('--detail-limit', type=int, default=0)
    plan.add_argument('--out')
    review = sub.add_parser('review', help='Hash-verified saved-HTML replay; never grants live approval')
    review.add_argument('--manifest', required=True)
    review.add_argument('--out', required=True)
    def command(args):
        try:
            if args.canary_operation == 'plan':
                result = build_canary_execution_plan(args.source_url, args.pages_per_source, args.detail_limit)
                if args.out:
                    from .multisource.storage import immutable_json
                    immutable_json(Path(args.out), result)
            else:
                result = review_saved_evidence(args.manifest, output=args.out)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except (ValueError, OSError, KeyError, TypeError) as error:
            parser.exit(2, 'CANARY_OFFLINE_ERROR: '+str(error)+'\n')
        if args.canary_operation == 'review' and not result['passed']:
            parser.exit(2)
    parser.set_defaults(func=command)
