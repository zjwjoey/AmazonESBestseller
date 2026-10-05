# -*- coding: utf-8 -*-
'Detail planning/execution, offline repair, and product enrichment handlers.'
from __future__ import annotations

import argparse
from pathlib import Path

from .common import _load_checkpoint_input, _load_json, _safe_print, _save_json
from .ranking import _load_snapshot_input

def cmd_detail_plan(args, parser: argparse.ArgumentParser) -> None:
    'Build and write a completely offline incremental detail plan.'
    from ..monitoring.detail_planner import build_detail_plan, write_detail_plan
    snapshot = _load_snapshot_input(args.snapshot)
    details = _load_json(args.details) if args.details else []
    state = _load_json(args.state) if args.state else []
    checkpoints = _load_checkpoint_input(args.checkpoints)
    plan = build_detail_plan(snapshot, details, state, saved_html=args.html_dir or None,
                             checkpoints=checkpoints,
                             current_access_state=args.current_access_state,
                             target_parser_version=args.target_parser_version or None)
    paths = write_detail_plan(plan, args.out_dir)
    print('detail plan \u5b8c\u6210\uff1a%d \u6761 \u2192 %s' % (len(plan['records']), paths['json']))


def cmd_detail_run(args, parser: argparse.ArgumentParser) -> None:
    'Execute only actions already present in a saved detail plan.'
    from ..collection.detail_executor import NETWORK_ACTIONS, execute_detail_plan
    from ..access.browser import BrowserSession
    plan_records = _load_json(args.plan)
    if isinstance(plan_records, list):
        snapshot_ids = {str(row.get('snapshot_id') or '')
                        for row in plan_records if isinstance(row, dict)
                        and row.get('snapshot_id')}
        if len(snapshot_ids) > 1:
            parser.error('detail-run \u8ba1\u5212\u5305\u542b\u591a\u4e2a snapshot_id\uff0c\u62d2\u7edd\u6df7\u5408\u6267\u884c')
        plan = {'records': plan_records,
                'snapshot_id': next(iter(snapshot_ids), '')}
    else:
        plan = plan_records
    pending = [row for row in plan.get('records', [])
               if row.get('detail_action') in NETWORK_ACTIONS]
    if args.offline and pending:
        parser.error('detail-run --offline \u53d1\u73b0 %d \u4e2a\u7f51\u7edc\u52a8\u4f5c\uff1b\u8ba1\u5212\u672a\u6267\u884c\u4e14\u4e0d\u4f1a\u8bbf\u95ee Amazon' % len(pending))
    if not pending:
        result = execute_detail_plan(plan, None, args.out_dir, offline=True,
                                     saved_html=args.html_dir or None,
                                     parser_version=args.parser_version)
    else:
        with BrowserSession(headless=not args.headful,
                            profile_dir=args.profile_dir or None) as session:
            result = execute_detail_plan(plan, session, args.out_dir, offline=bool(args.offline),
                                         saved_html=args.html_dir or None,
                                         parser_version=args.parser_version)
    print('detail run \u5b8c\u6210\uff1a\u8ba1\u5212\u7f51\u7edc\u52a8\u4f5c %d\uff0c\u6267\u884c\u8bb0\u5f55 %d \u2192 %s' %
          (result['requested_count'], len(result['records']),
           Path(args.out_dir) / 'detail_execution_manifest.json'))


def cmd_enrich(args) -> None:
    '\u699c\u5355+\u8be6\u60c5 \u2192 \u89c4\u8303\u5316+\u4e2d\u6587\u6d3e\u751f\u5546\u54c1\u8868\uff08products.json\uff09\u3002'
    from ..pipeline import enrich_products, legacy_flat_to_detail, legacy_flat_to_ranking

    if args.legacy:
        data = _load_json(args.legacy)
        rankings = [legacy_flat_to_ranking(r) for r in data]
        details = [legacy_flat_to_detail(r) for r in data]
        print('legacy \u5bfc\u5165\uff1a%d \u6761\u771f\u5b9e\u8bb0\u5f55\uff08\u6784\u9020\u578b BSR \u5217\u5df2\u4e22\u5f03\uff09' % len(data))
    else:
        rankings = _load_json(args.rankings)
        details = _load_json(args.details)
        print('\u699c\u5355 %d \u6761\u3001\u8be6\u60c5 %d \u6761' % (len(rankings), len(details)))

    translations = _load_json(args.translations) if args.translations else None
    products = enrich_products(rankings, details, translations)
    _save_json(products, args.out)
    print('enrich \u5b8c\u6210\uff1a%d \u6761\u5546\u54c1 \u2192 %s' % (len(products), args.out))


def cmd_repair_cache(args) -> None:
    '\u79bb\u7ebf\uff1a\u7528\u5df2\u4fdd\u5b58\u8be6\u60c5 HTML \u8865\u9f50 canonical \u5546\u54c1\u5b57\u6bb5\u3002'
    from ..collection.repair import repair_cached_products

    products = _load_json(args.products)
    repaired, report = repair_cached_products(products, args.html_dir)
    _save_json(repaired, args.out)
    print('repair-cache \u5b8c\u6210\uff1a\u5339\u914d %d \u9875\u3001\u5ffd\u7565 %d \u9875\u3001\u4fee\u6539 %d \u4e2a\u5546\u54c1\u3001%d \u4e2a\u5b57\u6bb5 \u2192 %s'
          % (report['matched_pages'], report['ignored_pages'],
             report['changed_products'], report['changed_fields'], args.out))


def cmd_reparse_details(args) -> None:
    '\u79bb\u7ebf\uff1a\u7528\u4fdd\u5b58 HTML \u5347\u7ea7\u8be6\u60c5 schema\uff0c\u4e0d\u53d1\u8d77 Amazon \u8bf7\u6c42\u3002'
    from ..collection.detail import reparse_saved_details
    from ..collection.planning import DetailState
    state = DetailState(args.state)
    records = reparse_saved_details(args.html_dir, state)
    state.save()
    _save_json(state.records(), args.out)
    print('reparse-details \u5b8c\u6210\uff1a\u91cd\u89e3\u6790 %d \u6761\u3001\u7f13\u5b58\u603b\u8ba1 %d \u6761 \u2192 %s'
          % (len(records), len(state), args.out))


def cmd_audit_detail_cache(args) -> None:
    '\u79bb\u7ebf\uff1a\u5ba1\u8ba1\u4fdd\u5b58\u8be6\u60c5 HTML\uff0c\u8bc6\u522b\u9a8c\u8bc1\u9875\u5e76\u751f\u6210\u9694\u79bb\u6e05\u5355\u3002'
    from ..collection.detail import audit_saved_detail_cache
    from ..collection.planning import DetailState
    if args.move and not args.quarantine_dir:
        raise SystemExit('--move \u9700\u8981\u540c\u65f6\u6307\u5b9a --quarantine-dir\uff1a\u8bc1\u636e\u53ea\u79fb\u52a8\uff0c\u7edd\u4e0d\u5220\u9664')
    state = DetailState(args.state) if args.state else None
    report = audit_saved_detail_cache(args.html_dir, asins=args.asins or None,
                                      quarantine_dir=args.quarantine_dir or None,
                                      state=state, move=args.move)
    if state:
        state.save()
    _save_json(report, args.out)
    s = report['summary']
    print('detail-cache-audit\uff1a\u6709\u6548 %d\u3001\u6311\u6218 %d\u3001\u65e0\u6548/\u7a7a %d \u2192 %s' %
          (s['VALID_PRODUCT_PAGE'], s['CHALLENGE'], s['INVALID_OR_EMPTY'], args.out))
    if args.move:
        print('\u5df2\u79fb\u51fa\u6d3b\u52a8\u7f13\u5b58 %d \u4e2a\u6587\u4ef6 \u2192 %s\uff08\u539f\u4ef6\u4fdd\u7559\u5728\u9694\u79bb\u76ee\u5f55\uff0c\u7eed\u91c7\u53ef\u6062\u590d\uff09'
              % (s.get('removed_from_cache', 0), args.quarantine_dir))
