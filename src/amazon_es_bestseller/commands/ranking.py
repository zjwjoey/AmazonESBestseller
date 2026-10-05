# -*- coding: utf-8 -*-
'Ranking snapshots, identity evidence, and category discovery handlers.'
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import _load_json, _safe_print

def _load_snapshot_records(path: str) -> list:
    data = _load_json(path)
    if isinstance(data, dict):
        if isinstance(data.get('rankings'), list):
            return data['rankings']
        if isinstance(data.get('records'), list):
            return data['records']
    return data if isinstance(data, list) else []


def _load_snapshot_input(path: str) -> dict:
    'Load ranking rows together with a sibling snapshot manifest when present.'
    target = Path(path)
    if target.is_dir():
        root = target
        rankings_path = root / 'rankings.json'
        identity_path = root / 'identity.json'
        manifest_path = root / 'manifest.json'
        if not rankings_path.exists() and identity_path.exists():
            rankings_path = identity_path
    else:
        rankings_path = target
        root = target.parent
        manifest_path = root / 'manifest.json'
    if not rankings_path.exists():
        raise SystemExit('\u627e\u4e0d\u5230\u5feb\u7167 rankings.json: %s' % rankings_path)
    rows = _load_snapshot_records(str(rankings_path))
    result = {'records': rows}
    if rankings_path.name == 'identity.json':
        result['snapshot_kind'] = 'RANKING_IDENTITY'
    if manifest_path.exists():
        manifest = _load_json(str(manifest_path))
        if isinstance(manifest, dict):
            if manifest.get('parser_version') == 'ranking_identity_v1':
                result['snapshot_kind'] = 'RANKING_IDENTITY'
                result['snapshot_status'] = manifest.get('status')
                result['identity_complete'] = bool(manifest.get('identity_complete'))
            else:
                result['snapshot_status'] = manifest.get('snapshot_status')
            result['snapshot_id'] = manifest.get('snapshot_id')
    return result


def cmd_ranking_snapshot(args, parser: argparse.ArgumentParser) -> None:
    'Freeze a latest ranking observation; ``--rankings-file`` is offline-only.'
    from ..monitoring.snapshot import build_ranking_snapshot, collect_ranking_snapshot
    output_root = Path(args.out_dir)
    if args.rankings_file:
        records = _load_snapshot_records(args.rankings_file)
        source_statuses = []
        planned_sources = []
        offline_frozen = True
        if args.source_manifest:
            manifest = _load_json(args.source_manifest)
            if isinstance(manifest, dict):
                source_statuses = (manifest.get('page_statuses')
                                   or manifest.get('source_statuses') or [])
                planned_sources = (manifest.get('planned_pages')
                                   or manifest.get('pages') or source_statuses)
            elif isinstance(manifest, list):
                source_statuses = manifest
                planned_sources = manifest
            offline_frozen = False
        result = build_ranking_snapshot(records, output_root,
                                        planned_sources=planned_sources,
                                        source_statuses=source_statuses,
                                        offline_frozen=offline_frozen)
    else:
        if not args.urls:
            parser.error('ranking-snapshot \u9700\u8981 --urls \u6216 --rankings-file')
        if args.offline:
            parser.error('ranking-snapshot --offline \u9700\u8981 --rankings-file\uff1b\u4e0d\u4f1a\u8bbf\u95ee Amazon')
        from ..access.browser import BrowserSession
        from ..transport.playwright import PlaywrightTransport
        with BrowserSession(headless=not args.headful,
                            profile_dir=args.profile_dir or None) as session:
            transport = (PlaywrightTransport(session)
                         if args.transport == 'playwright' else None)
            result = collect_ranking_snapshot(args.urls, session, output_root,
                                              pages_per_url=args.pages_per_url,
                                              parser_version=args.parser_version,
                                              transport=transport)
    print('ranking snapshot %s\uff1a%s\uff08%d \u6761\u8bb0\u5f55\uff09' %
          (result['manifest']['snapshot_status'], result['path'],
           result['manifest']['record_count']))
    if result['manifest']['snapshot_status'] != 'AUTHORITATIVE' and not args.allow_incomplete_debug:
        from ..monitoring.snapshot import SnapshotIncompleteError
        raise SnapshotIncompleteError(
            '\u5feb\u7167\u5df2\u4fdd\u5b58\u4e3a INCOMPLETE\uff1b\u672a\u66f4\u65b0 latest_authoritative pointer\u3002'
            '\u751f\u4ea7\u6a21\u5f0f\u62d2\u7edd\u4ee5 0 \u9000\u51fa\uff0c\u8bf7\u68c0\u67e5 manifest/page_statuses\u3002')


def _identity_extraction(args) -> dict:
    from ..monitoring.ranking_identity.extract import extract_identity_from_evidence
    return extract_identity_from_evidence(args.evidence_dir,
                                          expected_count=args.expected_count)


def cmd_ranking_identity_extract(args, parser: argparse.ArgumentParser) -> None:
    '\u79bb\u7ebf\uff1a\u4ece\u4fdd\u5b58\u7684\u699c\u5355\u8bc1\u636e\u63d0\u53d6 ASIN + canonical product URL\u3002'
    result = _identity_extraction(args)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'identity.json').write_text(
        json.dumps(result['records'], ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'raw_candidates.json').write_text(
        json.dumps(result['raw_candidates'], ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'audit.json').write_text(
        json.dumps(result['audit'], ensure_ascii=False, indent=2), encoding='utf-8')
    print('ranking identity extract\uff1a%s\uff08%d \u4e2a\u552f\u4e00 ASIN\uff0c\u72b6\u6001 %s\uff09' %
          (out, result['audit']['unique_asin_count'], result['audit']['status']))


def cmd_ranking_identity_snapshot(args, parser: argparse.ArgumentParser) -> None:
    '\u79bb\u7ebf\uff1a\u63d0\u53d6\u5e76\u5199\u5165 append-only Ranking Identity Snapshot\u3002'
    from ..monitoring.ranking_identity.snapshot import write_identity_snapshot
    from ..monitoring.snapshot import SnapshotIncompleteError
    result = _identity_extraction(args)
    saved = write_identity_snapshot(result, args.out_dir, snapshot_id=args.snapshot_id or None,
                                    evidence_dir=args.evidence_dir)
    print('ranking identity snapshot %s\uff1a%s\uff08%d \u4e2a\u552f\u4e00 ASIN\uff09' %
          (saved['manifest']['status'], saved['path'],
           saved['audit']['unique_asin_count']))
    if saved['manifest']['status'] != 'IDENTITY_COMPLETE' and not args.allow_incomplete_debug:
        raise SnapshotIncompleteError(
            'identity snapshot \u672a\u8fbe\u5230 IDENTITY_COMPLETE\uff1b\u5df2\u4fdd\u5b58\u4e0d\u53ef\u53d8\u8bca\u65ad\u5feb\u7167\uff0c'
            '\u5982\u9700\u4eba\u5de5\u68c0\u67e5\u8bf7\u4f7f\u7528 --allow-incomplete-debug\u3002')


def cmd_ranking_identity_audit(args, parser: argparse.ArgumentParser) -> None:
    '\u79bb\u7ebf\uff1a\u53ea\u8f93\u51fa\u8eab\u4efd\u63d0\u53d6\u5ba1\u8ba1\uff0c\u4e0d\u5199 snapshot\u3002'
    result = _identity_extraction(args)
    payload = json.dumps(result['audit'], ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding='utf-8')
    else:
        print(payload)


def cmd_discover_tree(args, parser: argparse.ArgumentParser) -> None:
    'Discover a bounded current Amazon Bestseller navigation snapshot.'
    if args.offline:
        parser.error('discover-tree \u9700\u8981\u8054\u7f51\uff0c\u4e0d\u80fd\u4e0e --offline \u540c\u7528')
    if not args.urls:
        parser.error('discover-tree \u9700\u8981\u81f3\u5c11\u4e00\u4e2a --urls')
    from ..access.browser import BrowserSession
    from ..access.location import ensure_spain_delivery
    from ..collection.discovery import discover_bestseller_tree

    with BrowserSession(headless=not args.headful,
                       profile_dir=args.profile_dir or None) as session:
        session.challenge_wait_seconds = args.challenge_wait_seconds
        session.manual_assist = args.manual_assist
        ensure_spain_delivery(session, args.postal_code)
        result = discover_bestseller_tree(args.urls, session, args.out_dir,
                                          max_depth=args.max_depth,
                                          max_pages=args.max_pages)
    _safe_print('\u7c7b\u76ee\u53d1\u73b0\u5b8c\u6210\uff1a\u9875\u9762 %d\uff0c\u699c\u5355\u94fe\u63a5 %d \u2192 %s' %
                (result['page_count'], result['link_count'], args.out_dir))


def cmd_validate_category_graph(args, parser: argparse.ArgumentParser) -> None:
    'Validate a persisted placement graph without network access.'
    from ..categories.crawler import CategoryCrawlerState
    from ..categories.models import PlacementStatus

    state = CategoryCrawlerState(args.state)
    errors = state.graph.validate()
    completion_errors = [
        f"{placement_id} status is {placement.status.value}"
        for placement_id, placement in state.graph.placements.items()
        if placement.status is not PlacementStatus.DONE
    ]
    report = {
        'state': str(Path(args.state)),
        'placement_count': len(state.graph.placements),
        'tree_valid': not errors,
        'crawl_complete': not completion_errors,
        'tree_errors': errors,
        'completion_errors': completion_errors,
        'authoritative_graph': str(state.path.parent / 'latest_authoritative_category_graph.json')
        if not errors and not completion_errors else None,
    }
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    label = 'VALID' if not errors and not completion_errors else ('INVALID' if errors else 'INCOMPLETE')
    _safe_print('category-graph-validate\uff1a%s\uff0cplacement %d%s' %
                (label, len(state.graph.placements),
                 (' \u2192 ' + str(args.out)) if args.out else ''))
    if errors:
        parser.error('\u7c7b\u76ee placement graph \u6821\u9a8c\u5931\u8d25\uff1a%s' % '; '.join(errors))
    if completion_errors:
        parser.error('\u7c7b\u76ee placement graph \u5c1a\u672a\u5b8c\u6210\uff1a%s' % '; '.join(completion_errors))
