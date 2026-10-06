# -*- coding: utf-8 -*-
'Argument parsing, compatibility dispatch, and top-level CLI error handling.'
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Mapping, Optional

from .commands import collection, detail, export as export_commands, quality, ranking, translation
from .commands.common import (DEFAULT_DETAILS, DEFAULT_RANKINGS, OUTPUTS,
                              _load_category_planning, _load_checkpoint_input,
                              _load_evidence_json, _load_images_by_asin, _load_json,
                              _load_translation_products, _safe_print, _save_json)

# Public compatibility re-exports: downstream scripts and tests historically
# imported handlers/helpers from this module. Implementations now live in the
# scoped command modules below.
_load_snapshot_records = ranking._load_snapshot_records
_load_snapshot_input = ranking._load_snapshot_input
_identity_extraction = ranking._identity_extraction
_batch_countdown = collection._batch_countdown
_batch_countdown_until = collection._batch_countdown_until
_load_json_array_or_empty = collection._load_json_array_or_empty
_load_quality_records = quality._load_quality_records
_quality_audit_from_args = quality._quality_audit_from_args
_print_quality_result = quality._print_quality_result
_latest_run_dir = quality._latest_run_dir

cmd_ranking_snapshot = ranking.cmd_ranking_snapshot
cmd_ranking_identity_extract = ranking.cmd_ranking_identity_extract
cmd_ranking_identity_snapshot = ranking.cmd_ranking_identity_snapshot
cmd_ranking_identity_audit = ranking.cmd_ranking_identity_audit
cmd_detail_plan = detail.cmd_detail_plan
cmd_detail_run = detail.cmd_detail_run
cmd_discover_tree = ranking.cmd_discover_tree
cmd_validate_category_graph = ranking.cmd_validate_category_graph
cmd_select_quota = collection.cmd_select_quota
cmd_download_images = collection.cmd_download_images
cmd_reconcile_task = collection.cmd_reconcile_task
cmd_translate_ds = translation.cmd_translate_ds
cmd_translate = translation.cmd_translate
cmd_dictionary_only = translation.cmd_dictionary_only
cmd_preclean = translation.cmd_preclean
cmd_enrich = detail.cmd_enrich
cmd_repair_cache = detail.cmd_repair_cache
cmd_reparse_details = detail.cmd_reparse_details
cmd_audit_detail_cache = detail.cmd_audit_detail_cache
cmd_qa = quality.cmd_qa
cmd_audit_fields = quality.cmd_audit_fields
cmd_quality_audit = quality.cmd_quality_audit
cmd_export = export_commands.cmd_export


def cmd_collect(args, parser: argparse.ArgumentParser) -> None:
    return collection.cmd_collect(args, parser)


def cmd_batch_collect(args, parser: argparse.ArgumentParser) -> None:
    return collection.cmd_batch_collect(args, parser, countdown=_batch_countdown,
                                        countdown_until=_batch_countdown_until)


def cmd_stable_research(args) -> None:
    return quality.cmd_stable_research(args, collect_command=cmd_collect,
                                       parser_factory=build_parser)


def cmd_task_collect(args, parser: argparse.ArgumentParser) -> None:
    'Compatibility dispatch for the reviewed task-collection command.'
    from .commands.task_collection import run_task_collection
    report = run_task_collection(args, parser, project_root=Path(__file__).resolve().parents[2])
    _safe_print('task-collect %s\uff1a%s\uff1b\u6700\u7ec8\u552f\u4e00 ASIN %d\uff1b\u62a5\u544a %s' %
                (report['mode'], report['run_status'],
                 report['final_unique_asins'],
                 str(Path(args.out_dir) / 'run_report.json')))


def cmd_translation_production(args) -> None:
    'Thin argparse dispatch for the offline production translation command.'
    from .commands.translation import run_translation_production
    return run_translation_production(
        args, load_products=_load_translation_products, load_json=_load_json,
        save_json=_save_json, load_evidence_json=_load_evidence_json,
        default_details=DEFAULT_DETAILS, default_rankings=DEFAULT_RANKINGS,
        load_images=_load_images_by_asin, load_category_planning=_load_category_planning,
    )


def cmd_production_run(args) -> None:
    'Thin dispatch for the evidence-driven Production V1 runner.'
    from .commands.run import run_production
    result = run_production(args)
    _safe_print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def cmd_production_translation_selection(args) -> None:
    'Thin dispatch for the explicit, human-reviewable translation subset.'
    from .commands.run import create_translation_selection
    result = create_translation_selection(args)
    _safe_print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='amazon-es',
        description='Amazon.es bestseller research pipeline')
    parser.add_argument('--offline', action='store_true',
                        help='\u79bb\u7ebf\u6807\u8bb0\uff1acollect/translate-ds/\u771f\u5b9e translate \u62d2\u7edd\uff1bTranslation V2 dry-run \u53ef\u7528')
    sub = parser.add_subparsers(dest='command', required=True)

    c = sub.add_parser('collect', help='\u8054\u7f51\u91c7\u96c6\u699c\u5355+\u8be6\u60c5\uff08\u4e32\u884c\uff09')
    c.add_argument('--urls', nargs='+', default=[],
                   help='\u699c\u5355\u9875 URL\uff08/zgbs/<NODE>\uff09')
    c.add_argument('--out-dir', default=str(OUTPUTS), help='\u8f93\u51fa\u76ee\u5f55\uff08\u9ed8\u8ba4 outputs/\uff09')
    c.add_argument('--headful', action='store_true', help='\u6709\u5934\u6d4f\u89c8\u5668\uff08\u9ed8\u8ba4 headless\uff09')
    c.add_argument('--profile-dir', default='',
                   help='\u53ef\u9009\uff1a\u590d\u7528\u672c\u673a Chrome \u7528\u6237\u914d\u7f6e\u76ee\u5f55\uff08\u4f8b\u5982 Chrome User Data\uff09')
    c.add_argument('--postal-code', default='28001',
                   help='\u914d\u9001\u5730\u70b9\u68c0\u67e5\u4f7f\u7528\u7684\u897f\u73ed\u7259\u90ae\u7f16\uff08\u9ed8\u8ba4 28001\uff0c\u9a6c\u5fb7\u91cc\uff09')
    c.add_argument('--challenge-wait-seconds', type=float, default=180.0,
                   help='\u9047\u5230\u6311\u6218\u9875\u65f6\u7b49\u5f85\u81ea\u52a8\u6062\u590d\u7684\u79d2\u6570\uff08\u9ed8\u8ba4 180\uff1b\u5206\u6bb5\u8f6e\u8be2\uff09')
    c.add_argument('--manual-assist', action='store_true',
                   help='\u7b49\u5f85\u540e\u4ecd\u662f\u6311\u6218\u9875\u65f6\uff0c\u5728 --headful \u6d4f\u89c8\u5668\u4e2d\u6682\u505c\u5e76\u7b49\u5f85\u4eba\u5de5\u63a5\u7ba1')
    c.add_argument('--pages-per-url', type=int, default=1,
                   help='\u6bcf\u4e2a\u699c\u5355 URL \u4f9d\u6b21\u8bbf\u95ee\u7684\u9875\u6570\uff1b\u9ed8\u8ba4 1\uff0c\u4f7f\u7528 ?pg=N \u5206\u9875')
    c.add_argument('--rankings-only', action='store_true', help='\u53ea\u91c7\u96c6\u699c\u5355\u9875\uff0c\u4e0d\u8bbf\u95ee\u8be6\u60c5\u9875')
    c.add_argument('--rankings-file', default='', help='\u590d\u7528\u5df2\u4fdd\u5b58\u699c\u5355 JSON\uff0c\u4ec5\u8bbf\u95ee manifest \u4e2d\u8be6\u60c5')
    c.add_argument('--manifest', default='', help='\u8be6\u60c5\u91c7\u96c6 ASIN manifest JSON\uff08\u4e0e --rankings-file \u914d\u5408\uff09')
    c.add_argument('--progress', default='', help='\u53ef\u9009\uff1a\u9010 ASIN \u5199\u5165\u8fd0\u884c\u8fdb\u5ea6 JSON')
    c.set_defaults(func=lambda a, p=c: cmd_collect(a, p))

    rs = sub.add_parser('ranking-snapshot', help='\u51bb\u7ed3\u6700\u65b0\u699c\u5355\u5feb\u7167\uff1b--rankings-file \u53ef\u79bb\u7ebf\u8fd0\u884c')
    rs.add_argument('--urls', nargs='*', default=[], help='\u5df2\u5ba1\u6838 Amazon Bestseller \u6765\u6e90 URL')
    rs.add_argument('--rankings-file', default='', help='\u79bb\u7ebf\u51bb\u7ed3\u5df2\u6709\u699c\u5355 JSON')
    rs.add_argument('--out-dir', default='runtime/ranking_snapshots')
    rs.add_argument('--pages-per-url', type=int, default=1)
    rs.add_argument('--parser-version', choices=('v1', 'v2'), default='v1',
                    help='\u699c\u5355\u89e3\u6790\u5951\u7ea6\uff1bv2 \u5bf9\u6bcf\u4e2a\u4fdd\u5b58\u9875\u6267\u884c ACP/\u5b8c\u6574\u6027\u5ba1\u8ba1\uff08\u9ed8\u8ba4 v1\uff09')
    rs.add_argument('--transport', choices=('playwright', 'legacy'), default='playwright',
                    help='\u91c7\u96c6\u4f20\u8f93\u8fb9\u754c\uff1bplaywright \u4e3a\u6b63\u5f0f\u9002\u914d\u5668\uff0clegacy \u4fdd\u7559\u65e7\u8c03\u7528\u8def\u5f84')
    rs.add_argument('--headful', action='store_true')
    rs.add_argument('--profile-dir', default='')
    rs.add_argument('--source-manifest', default='',
                    help='\u79bb\u7ebf\u6b63\u5f0f\u5feb\u7167\u7684\u539f\u59cb\u8ba1\u5212/page-level evidence manifest')
    rs.add_argument('--allow-incomplete-debug', action='store_true',
                    help='\u5141\u8bb8\u4fdd\u5b58 INCOMPLETE \u5feb\u7167\u5e76\u4ee5 0 \u9000\u51fa\uff0c\u4ec5\u4f9b\u4eba\u5de5\u8c03\u8bd5')
    rs.set_defaults(func=lambda a, p=rs: cmd_ranking_snapshot(a, p))

    rie = sub.add_parser('ranking-identity-extract', help='\u79bb\u7ebf\uff1a\u4ece\u4fdd\u5b58\u699c\u5355\u8bc1\u636e\u63d0\u53d6 ASIN \u4e0e\u5546\u54c1\u94fe\u63a5')
    rie.add_argument('--evidence-dir', required=True, help='saved HTML/JSON evidence \u76ee\u5f55')
    rie.add_argument('--out-dir', required=True, help='identity.json/raw_candidates.json/audit.json \u8f93\u51fa\u76ee\u5f55')
    rie.add_argument('--expected-count', type=int, default=None)
    rie.set_defaults(func=lambda a, p=rie: cmd_ranking_identity_extract(a, p))

    ris = sub.add_parser('ranking-identity-snapshot', help='\u79bb\u7ebf\uff1a\u521b\u5efa\u4e0d\u53ef\u53d8 Ranking Identity Snapshot')
    ris.add_argument('--evidence-dir', required=True, help='saved HTML/JSON evidence \u76ee\u5f55')
    ris.add_argument('--out-dir', required=True, help='snapshot \u6839\u76ee\u5f55')
    ris.add_argument('--expected-count', type=int, default=None)
    ris.add_argument('--snapshot-id', default='')
    ris.add_argument('--allow-incomplete-debug', action='store_true')
    ris.set_defaults(func=lambda a, p=ris: cmd_ranking_identity_snapshot(a, p))

    ria = sub.add_parser('ranking-identity-audit', help='\u79bb\u7ebf\uff1a\u5ba1\u8ba1\u4fdd\u5b58\u8bc1\u636e\u4e2d\u7684\u8eab\u4efd\u5b8c\u6574\u6027')
    ria.add_argument('--evidence-dir', required=True, help='saved HTML/JSON evidence \u76ee\u5f55')
    ria.add_argument('--expected-count', type=int, default=None)
    ria.add_argument('--out', default='')
    ria.set_defaults(func=lambda a, p=ria: cmd_ranking_identity_audit(a, p))

    dp = sub.add_parser('detail-plan', help='\u79bb\u7ebf\uff1a\u6309\u699c\u5355\u5feb\u7167\u4e0e\u8be6\u60c5\u7f13\u5b58\u751f\u6210\u589e\u91cf\u8be6\u60c5\u8ba1\u5212')
    dp.add_argument('--snapshot', required=True, help='rankings.json \u6216\u5feb\u7167 records JSON')
    dp.add_argument('--details', default='', help='\u5df2\u6709\u8be6\u60c5\u7f13\u5b58 JSON')
    dp.add_argument('--state', default='', help='\u8be6\u60c5\u72b6\u6001 JSON')
    dp.add_argument('--checkpoints', default='', help='\u8be6\u60c5 checkpoint \u76ee\u5f55\uff0c\u517c\u5bb9 JSON \u6587\u4ef6\u6216\u8bb0\u5f55\u5217\u8868')
    dp.add_argument('--html-dir', default='', help='\u4fdd\u5b58\u7684\u8be6\u60c5 HTML \u76ee\u5f55\uff0c\u7528\u4e8e schema \u79bb\u7ebf\u91cd\u89e3\u6790\u51b3\u7b56')
    dp.add_argument('--current-access-state', default='UNKNOWN',
                    help='\u5f53\u524d Access Gate \u72b6\u6001\uff1b\u6062\u590d\u4e3a NORMAL \u65f6\u5141\u8bb8\u5386\u53f2\u53d7\u9650\u8bb0\u5f55\u91cd\u8bd5')
    dp.add_argument('--target-parser-version', choices=('v1', 'v2'), default='',
                    help='\u53ef\u9009\uff1a\u8981\u6c42\u8be6\u60c5\u7f13\u5b58\u8fbe\u5230\u6307\u5b9a parser contract\uff1bv2 \u7f3a\u5931\u65f6\u5fc5\u987b\u91cd\u89e3\u6790\u6216\u91cd\u65b0\u6293\u53d6')
    dp.add_argument('--out-dir', required=True, help='detail_plan.json/csv/summary \u8f93\u51fa\u76ee\u5f55')
    dp.set_defaults(func=lambda a, p=dp: cmd_detail_plan(a, p))

    dr = sub.add_parser('detail-run', help='\u6309\u5df2\u4fdd\u5b58\u7684 detail plan \u6267\u884c\u660e\u786e\u7f51\u7edc\u52a8\u4f5c')
    dr.add_argument('--plan', required=True, help='detail_plan.json \u6216\u5305\u542b records \u7684 JSON')
    dr.add_argument('--out-dir', required=True)
    dr.add_argument('--headful', action='store_true')
    dr.add_argument('--profile-dir', default='')
    dr.add_argument('--html-dir', default='', help='REPARSE_SAVED_HTML \u4f7f\u7528\u7684\u8be6\u60c5 HTML \u76ee\u5f55')
    dr.add_argument('--offline', action='store_true',
                    help='\u7981\u6b62\u7f51\u7edc\u52a8\u4f5c\uff1bREPARSE/VERIFY/BLOCK/REUSE \u4ecd\u53ef\u79bb\u7ebf\u6267\u884c')
    dr.add_argument('--parser-version', choices=('v1', 'v2'), default='v1',
                    help='\u8be6\u60c5\u89e3\u6790\u5951\u7ea6\uff1bv2 \u4fdd\u7559\u53d8\u4f53/\u8eab\u4efd/\u91cd\u590d\u5c5e\u6027\u8bc1\u636e\uff08\u9ed8\u8ba4 v1\uff09')
    dr.set_defaults(func=lambda a, p=dr: cmd_detail_run(a, p))

    bc = sub.add_parser('batch-collect', help='\u8054\u7f51\uff1a\u6309\u8ba1\u5212\u5206\u6279\u91c7\u96c6\uff0c\u7c7b\u76ee\u95f4\u4fdd\u6301\u5012\u8ba1\u65f6\u51b7\u5374\u5e76\u81ea\u52a8\u7eed\u8dd1')
    bc.add_argument('--plan', required=True, help='\u6765\u6e90\u9875\u63d0\u53d6\u8ba1\u5212 JSON')
    bc.add_argument('--out-dir', required=True, help='\u6279\u5904\u7406\u8f93\u51fa\u76ee\u5f55')
    bc.add_argument('--headful', action='store_true', help='\u6709\u5934\u6d4f\u89c8\u5668')
    bc.add_argument('--profile-dir', default='', help='\u53ef\u9009\uff1a\u590d\u7528\u672c\u673a\u6d4f\u89c8\u5668\u914d\u7f6e\u76ee\u5f55')
    bc.add_argument('--postal-code', default='28001', help='\u914d\u9001\u5730\u70b9\u68c0\u67e5\u4f7f\u7528\u7684\u897f\u73ed\u7259\u90ae\u7f16')
    bc.add_argument('--challenge-wait-seconds', type=float, default=180.0,
                    help='\u517c\u5bb9\u53c2\u6570\uff1b\u6311\u6218\u9875\u73b0\u5728\u7acb\u5373\u505c\u6b62\uff0c\u4e0d\u4f1a\u81ea\u52a8\u7b49\u5f85\u6062\u590d')
    bc.add_argument('--manual-assist', action='store_true',
                    help='\u517c\u5bb9\u53c2\u6570\uff1b\u6311\u6218\u9875\u505c\u6b62\u540e\u9700\u4eba\u5de5\u5904\u7406\u5e76\u91cd\u65b0\u542f\u52a8')
    bc.add_argument('--cooldown-seconds', type=int, default=None,
                    help='\u7c7b\u76ee\u95f4\u51b7\u5374\u79d2\u6570\uff1b\u7701\u7565\u65f6\u8bfb\u53d6\u8ba1\u5212\uff0c\u9ed8\u8ba41800')
    bc.add_argument('--existing-products', default='',
                    help='\u5df2\u6709\u89c4\u8303\u5316\u5546\u54c1 JSON\uff1b\u5176\u4e2d ASIN \u4e0d\u518d\u91cd\u590d\u8bf7\u6c42\u8be6\u60c5')
    bc.add_argument('--existing-details', default='',
                    help='\u5df2\u6709\u8be6\u60c5 JSON\uff1b\u5408\u5e76\u5199\u5165\u6279\u5904\u7406 details.json')
    bc.add_argument('--seed-rankings', default='',
                    help='\u5df2\u670931\u201350\u699c\u5355 JSON\uff1b\u4f5c\u4e3a\u5df2\u5b8c\u6210\u6765\u6e90\u7684\u8bc1\u636e\u79cd\u5b50')
    bc.add_argument('--rankings-only', action='store_true', help='\u53ea\u63d0\u53d6\u699c\u5355\uff0c\u4e0d\u8bbf\u95ee\u8be6\u60c5\u9875')
    bc.set_defaults(func=lambda a, p=bc: cmd_batch_collect(a, p))

    dt = sub.add_parser('discover-tree', help='\u8054\u7f51\uff1a\u53d1\u73b0\u5f53\u524d Amazon.es Bestseller \u7c7b\u76ee\u6811\u5e76\u4fdd\u5b58\u5feb\u7167')
    dt.add_argument('--urls', nargs='+', required=True,
                    help='\u8981\u53d1\u73b0\u7684 Amazon.es Bestseller \u6839\u7c7b\u76ee URL')
    dt.add_argument('--out-dir', required=True, help='\u7c7b\u76ee\u53d1\u73b0\u8f93\u51fa\u76ee\u5f55')
    dt.add_argument('--max-depth', type=int, default=1,
                    help='\u5411\u4e0b\u53d1\u73b0\u5c42\u7ea7\uff1b\u9ed8\u8ba41\uff0c\u53ea\u8bfb\u53d6\u6839\u9875\u548c\u76f4\u63a5\u5b50\u699c\u5355')
    dt.add_argument('--max-pages', type=int, default=200,
                    help='\u6700\u591a\u8bbf\u95ee\u9875\u9762\u6570\uff0c\u9ed8\u8ba4200')
    dt.add_argument('--headful', action='store_true', help='\u6709\u5934\u6d4f\u89c8\u5668')
    dt.add_argument('--profile-dir', default='', help='\u53ef\u9009\uff1a\u590d\u7528\u672c\u673a\u6d4f\u89c8\u5668\u914d\u7f6e\u76ee\u5f55')
    dt.add_argument('--postal-code', default='28001', help='\u897f\u73ed\u7259\u914d\u9001\u90ae\u7f16')
    dt.add_argument('--challenge-wait-seconds', type=float, default=180.0,
                    help='\u517c\u5bb9\u53c2\u6570\uff1b\u6311\u6218\u9875\u73b0\u5728\u7acb\u5373\u505c\u6b62\uff0c\u4e0d\u4f1a\u81ea\u52a8\u7b49\u5f85\u6062\u590d')
    dt.add_argument('--manual-assist', action='store_true',
                    help='\u517c\u5bb9\u53c2\u6570\uff1b\u6311\u6218\u9875\u505c\u6b62\u540e\u9700\u4eba\u5de5\u5904\u7406\u5e76\u91cd\u65b0\u542f\u52a8')
    dt.set_defaults(func=lambda a, p=dt: cmd_discover_tree(a, p))

    cgv = sub.add_parser('category-graph-validate',
                         help='\u79bb\u7ebf\uff1a\u6821\u9a8c\u53ef\u6062\u590d\u7684\u7c7b\u76ee placement graph\uff0c\u4e0d\u8bbf\u95ee Amazon')
    cgv.add_argument('--state', required=True, help='CategoryCrawlerState JSON')
    cgv.add_argument('--out', default='', help='\u53ef\u9009\uff1a\u6821\u9a8c\u62a5\u544a JSON')
    cgv.set_defaults(func=lambda a, p=cgv: cmd_validate_category_graph(a, p))

    tc = sub.add_parser('task-collect', help='\u8054\u7f51\uff1a\u8fd0\u884c\u5ba1\u6838\u540e\u76845000 SKU\u4efb\u52a1')
    tc.add_argument('--plan', required=True, help='\u672c\u8f6e\u5ba1\u6838\u4efb\u52a1\u8ba1\u5212 JSON')
    tc.add_argument('--out-dir', required=True, help='\u672c\u8f6e\u72ec\u7acb\u8f93\u51fa\u76ee\u5f55')
    tc.add_argument('--phase', choices=('ranking', 'detail', 'all'), default='all',
                    help='ranking only / frozen detail only / legacy all')
    tc.add_argument('--resume', action='store_true',
                    help='resume the declared phase from its versioned checkpoint')
    tc.add_argument('--mode', choices=('parallel3', 'serial'), default=None,
                    help='parallel3=\u4e09\u7c7b\u76ee\u5e76\u884c\u4e3b\u6a21\u5757\uff1bserial=\u5355\u7c7b\u76ee\u5907\u7528\u6a21\u5757')
    tc.add_argument('--headful', action='store_true', help='\u6709\u5934\u6d4f\u89c8\u5668')
    tc.add_argument('--profile-dir', default='',
                    help='\u4ec5\u4e32\u884c\u6a21\u5f0f\u4f7f\u7528\u7684\u6d4f\u89c8\u5668\u914d\u7f6e\u76ee\u5f55\uff1bparallel3\u4e0d\u63a5\u53d7\u5171\u4eabProfile')
    tc.add_argument('--postal-code', default='28001', help='\u897f\u73ed\u7259\u914d\u9001\u90ae\u7f16')
    tc.add_argument('--challenge-wait-seconds', type=float, default=None,
                    help='\u517c\u5bb9\u53c2\u6570\uff1b\u6311\u6218\u9875\u73b0\u5728\u7acb\u5373\u505c\u6b62\uff0c\u4e0d\u4f1a\u81ea\u52a8\u7b49\u5f85\u6062\u590d')
    tc.add_argument('--manual-assist', action='store_true',
                    help='\u517c\u5bb9\u53c2\u6570\uff1b\u6311\u6218\u9875\u505c\u6b62\u540e\u9700\u4eba\u5de5\u5904\u7406\u5e76\u91cd\u65b0\u542f\u52a8')
    tc.set_defaults(func=lambda a, p=tc: cmd_task_collect(a, p))

    s = sub.add_parser('select-quota', help='\u79bb\u7ebf\uff1a\u6309\u5ba1\u6838\u7c7b\u76ee\u914d\u7f6e\u9009\u62e9 150/50 \u552f\u4e00 ASIN')
    s.add_argument('--rankings', required=True, help='\u699c\u5355\u8bb0\u5f55 JSON')
    s.add_argument('--config', required=True, help='\u7c7b\u76ee\u914d\u7f6e JSON')
    s.add_argument('--out', required=True, help='\u914d\u989d manifest JSON')
    s.set_defaults(func=cmd_select_quota)

    im = sub.add_parser('download-images', help='\u8054\u7f51\uff1a\u6309 ASIN \u4e32\u884c\u4e0b\u8f7d\u7f3a\u5931\u539f\u56fe')
    im.add_argument('--products', required=True, help='\u5546\u54c1 JSON \u6570\u7ec4')
    im.add_argument('--out-dir', required=True, help='\u56fe\u7247\u7f13\u5b58\u76ee\u5f55')
    im.add_argument('--report', required=True, help='\u4e0b\u8f7d\u7ed3\u679c JSON')
    im.add_argument('--delay', type=float, default=1.0, help='\u56fe\u7247\u8bf7\u6c42\u95f4\u9694\u79d2\u6570')
    im.set_defaults(func=cmd_download_images)

    rc = sub.add_parser('reconcile-task', help='\u79bb\u7ebf\uff1a\u5bf9\u8d26\u4efb\u52a1\u76ee\u6807\u4e0e\u5404\u9636\u6bb5 ASIN \u96c6\u5408')
    rc.add_argument('--task', required=True)
    rc.add_argument('--items', required=True)
    rc.add_argument('--products', required=True)
    rc.add_argument('--translations', default='')
    rc.add_argument('--out', required=True)
    rc.set_defaults(func=cmd_reconcile_task)

    e = sub.add_parser('enrich', help='\u79bb\u7ebf\uff1a\u699c\u5355+\u8be6\u60c5 \u2192 \u89c4\u8303\u5316+\u4e2d\u6587\u6d3e\u751f\u5546\u54c1\u8868')
    e.add_argument('--rankings', default=str(OUTPUTS / 'rankings.json'),
                   help='\u699c\u5355\u8bb0\u5f55 JSON')
    e.add_argument('--details', default=str(OUTPUTS / 'details.json'),
                   help='\u8be6\u60c5\u8bb0\u5f55 JSON')
    e.add_argument('--legacy', default='',
                   help='\u9057\u7559\u6241\u5e73\u6570\u636e\uff08product_details.json\uff09\uff0c\u5bfc\u5165\u65f6\u4e22\u5f03\u6784\u9020\u578b BSR')
    e.add_argument('--translations', default='', help='\u7ffb\u8bd1\u8868 JSON\uff08ASIN \u2192 {title_zh}\uff09')
    e.add_argument('--out', default=str(OUTPUTS / 'products.json'), help='\u8f93\u51fa\u5546\u54c1\u8868 JSON')
    e.set_defaults(func=cmd_enrich)

    r = sub.add_parser('repair-cache', help='\u79bb\u7ebf\uff1a\u7528\u4fdd\u5b58 HTML \u4fee\u590d\u5df2\u6709\u5546\u54c1\u7684 canonical/display \u5b57\u6bb5')
    r.add_argument('--products', required=True, help='\u89c4\u8303\u5316\u5546\u54c1 JSON \u6570\u7ec4')
    r.add_argument('--html-dir', required=True, help='\u4fdd\u5b58\u7684\u8be6\u60c5 HTML \u76ee\u5f55')
    r.add_argument('--out', required=True, help='\u4fee\u590d\u540e\u7684\u5546\u54c1 JSON')
    r.set_defaults(func=cmd_repair_cache)

    rp = sub.add_parser('reparse-details', help='\u79bb\u7ebf\uff1a\u6309\u5f53\u524d\u8be6\u60c5 schema \u91cd\u5efa raw details\uff08\u91cd\u590d ASIN \u53d6\u9996\u4e2a\u6709\u6548\u76ee\u5f55\uff09')
    rp.add_argument('--html-dir', nargs='+', required=True)
    rp.add_argument('--state', required=True, help='DetailState JSON')
    rp.add_argument('--out', required=True, help='\u91cd\u5efa\u540e\u7684 details JSON')
    rp.set_defaults(func=cmd_reparse_details)

    ca = sub.add_parser('audit-detail-cache', help='\u79bb\u7ebf\uff1a\u5ba1\u8ba1\u8be6\u60c5 HTML \u7f13\u5b58\uff0c\u4e0d\u8bbf\u95ee Amazon')
    ca.add_argument('--html-dir', nargs='+', required=True)
    ca.add_argument('--asins', nargs='*', default=[])
    ca.add_argument('--quarantine-dir', default='')
    ca.add_argument('--move', action='store_true',
                    help='\u628a\u6311\u6218/\u65e0\u6548\u9875\u79fb\u51fa\u6d3b\u52a8\u7f13\u5b58\uff08\u79fb\u52a8\u4e0d\u5220\u9664\uff0c\u7eed\u91c7\u624d\u80fd\u6062\u590d\uff09')
    ca.add_argument('--state', default='')
    ca.add_argument('--out', required=True)
    ca.set_defaults(func=cmd_audit_detail_cache)

    t = sub.add_parser('translate-ds', help='\u8054\u7f51\uff1a\u8c03\u7528 DeepSeek API \u7ffb\u8bd1\u4e2d\u6587\u663e\u793a\u5b57\u6bb5')
    t.add_argument('--products', required=True, help='\u89c4\u8303\u5316\u5546\u54c1 JSON \u6570\u7ec4')
    t.add_argument('--cache', default='', help='\u7ffb\u8bd1\u7f13\u5b58 JSON\uff08\u9ed8\u8ba4\u5199\u5165 --out\uff09')
    t.add_argument('--out', required=True, help='ASIN \u2192 \u7ffb\u8bd1\u7ed3\u679c JSON')
    t.add_argument('--endpoint', default='', help='\u5b8c\u6574 API endpoint\uff08\u9ed8\u8ba4 DeepSeek chat/completions\uff09')
    t.add_argument('--model', default='', help='\u6a21\u578b\u540d\uff08\u9ed8\u8ba4 deepseek-chat\uff09')
    t.add_argument('--max-retries', type=int, default=2)
    t.add_argument('--backoff-seconds', type=float, default=1.0)
    t.add_argument('--timeout', type=float, default=60.0)
    t.add_argument('--repair-partial', action='store_true',
                   help='\u5df2\u786e\u8ba4\u8c03\u7528 API \u65f6\uff0c\u7ed5\u8fc7\u540c\u6e90 partial \u7f13\u5b58\u5e76\u8865\u7ffb\u7f3a\u5931\u5b57\u6bb5')
    t.set_defaults(func=cmd_translate_ds)

    tv2 = sub.add_parser('translate', help='Translation V2\uff1a\u5b57\u6bb5\u7ea7 Qwen-MT \u7ffb\u8bd1\uff08\u9ed8\u8ba4\u5148 dry-run\uff09')
    tv2.add_argument('--products', required=True,
                     help='\u89c4\u8303\u5316\u5546\u54c1 JSON \u6570\u7ec4\uff0c\u6216\u5185\u90e8\u7814\u7a76 CSV\uff08\u6309 ASIN/\u897f\u8bed\u5b57\u6bb5\u6620\u5c04\uff09')
    tv2.add_argument('--provider', default='qwen-mt', choices=('qwen-mt',), help='\u7ffb\u8bd1\u63d0\u4f9b\u5546')
    tv2.add_argument('--model', default='', help='\u6a21\u578b\u540d\uff08\u9ed8\u8ba4 qwen-mt-flash\uff09')
    tv2.add_argument('--rate', type=float, default=None,
                     help='Qwen API \u6700\u5927\u8c03\u7528\u901f\u7387\uff08\u6b21/\u79d2\uff0c\u9ed8\u8ba4 0.5\uff1b0 \u8868\u793a\u4e0d\u9650\u901f\uff09')
    tv2.add_argument('--cache', default=str(OUTPUTS / 'translation_v2_cache.json'), help='\u5b57\u6bb5\u7ea7\u7ffb\u8bd1\u7f13\u5b58')
    tv2.add_argument('--out', required=True, help='ASIN \u2192 Translation V2 \u7ed3\u679c JSON')
    tv2.add_argument('--qa-out', default='', help='translation_qa.json \u8f93\u51fa\u8def\u5f84')
    tv2.add_argument('--audit-out', default='', help='\u53ef\u9009\u5b57\u6bb5\u5ba1\u8ba1 JSONL')
    tv2.add_argument('--summary-out', default='', help='\u7ffb\u8bd1\u8fd0\u884c\u6458\u8981\uff08\u542b\u6c60\u72b6\u6001\u4e0e QA \u6c47\u603b\uff09')
    tv2.add_argument('--config', default='', help='configs/translation_v2.json')
    tv2.add_argument('--field', action='append', default=[], help='\u53ea\u7ffb\u8bd1\u6307\u5b9a source/target \u5b57\u6bb5\uff0c\u53ef\u91cd\u590d')
    tv2.add_argument('--fields', default='', help='\u9017\u53f7\u5206\u9694\u7684\u5b57\u6bb5\u540d\uff08--field \u7684\u7b80\u5199\uff09')
    tv2.add_argument('--repair-partial', action='store_true', help='\u91cd\u8bd5 partial \u5b57\u6bb5')
    tv2.add_argument('--repair-failed', action='store_true', help='\u91cd\u8bd5 failed \u5b57\u6bb5')
    tv2.add_argument('--limit', type=int, default=None)
    tv2.add_argument('--offset', type=int, default=0)
    tv2.add_argument('--dry-run', action='store_true', help='\u4ec5\u751f\u6210\u5b57\u6bb5\u8ba1\u5212\uff0c\u4e0d\u8c03\u7528 API')
    tv2.add_argument('--parallel-providers', action='store_true',
                     help='\u6309\u914d\u7f6e\u542f\u7528\u53cc Provider \u5e76\u884c\u6c60\uff08\u771f\u5b9e\u8c03\u7528\u4ecd\u9700 YES\uff09')
    tv2.add_argument('--yes', action='store_true', help='\u8df3\u8fc7\u771f\u5b9e API \u8c03\u7528\u524d\u7684 YES \u786e\u8ba4')
    tv2.set_defaults(func=cmd_translate)

    prod = sub.add_parser('translation-production', help='stage Production Translation V2 artifacts')
    prod.add_argument('--stage', required=True,
                      choices=('build-input', 'preclean', 'plan', 'translate', 'promote', 'export'))
    prod.add_argument('--master', default='')
    prod.add_argument('--run-dir', default='')
    prod.add_argument('--run-id', default='production-run')
    prod.add_argument('--source-run-id', default='')
    prod.add_argument('--source-schema-version', default='master-v1')
    prod.add_argument('--config', default='configs/translation_v2_production.example.json')
    prod.add_argument('--model', default='')
    prod.add_argument('--rate', type=float, default=None)
    prod.add_argument('--limit', type=int, default=None)
    prod.add_argument('--offset', type=int, default=0)
    prod.add_argument('--asin-list', default='', help='ASIN JSON array or comma-separated list')
    prod.add_argument('--category', default='', help='match any canonical category level')
    prod.add_argument('--yes', action='store_true', help='confirm offline fake translation')
    prod.add_argument('--dry-run', action='store_true', help='translate stage writes only an offline plan')
    prod.add_argument('--out', default='', help='production-export workbook output')
    prod.add_argument('--details', default='')
    prod.add_argument('--rankings', default='')
    prod.add_argument('--html-dir', nargs='+', default=[])
    prod.add_argument('--collection-run-dir', default='')
    prod.add_argument('--prev-workbook', default='')
    prod.add_argument('--images-dir', default='')
    prod.add_argument('--category-planning', default='')
    prod.add_argument('--force', action='store_true')
    prod.add_argument('--debug-export', action='store_true',
                      help='write a NOT_FOR_RELEASE diagnostic workbook only')
    prod.add_argument('--profile', choices=('research', 'business', 'task'), default='research')
    prod.set_defaults(func=cmd_translation_production)

    run = sub.add_parser('production-run', help='evidence-driven Production V1 stage runner')
    run.add_argument('--run-dir', required=True)
    run.add_argument('--run-id', required=True)
    run.add_argument('--config', required=True, help='TaskConfig JSON; stage payload injection is forbidden')
    run.add_argument('--schema-version', default='production-v1')
    run.add_argument('--resume', action='store_true', help='reuse only matching READY stage artifacts')
    run.add_argument('--from-stage', default='', help='restart at a named stage after hash verification')
    run.add_argument('--profile', choices=('full', 'source-only'), default='full')
    run.add_argument('--allow-live-transport', action='store_true',
                     help='allow only a reviewed live V1 browser transport declared in TaskConfig')
    run.add_argument('--transport-preflight-only', action='store_true',
                     help='verify reviewed Amazon delivery transport and save diagnostics without ranking/detail collection')
    run.add_argument('--allow-qwen-translation', action='store_true',
                     help='allow configured Qwen translation only through the <=5 CNY durable budget ledger')
    run.set_defaults(func=cmd_production_run)

    selection = sub.add_parser('production-translation-selection',
                               help='bind a reviewed <=1500-ASIN translation batch to spanish-master evidence')
    selection.add_argument('--master-artifact', required=True,
                           help='artifacts/spanish-master.json from a completed source-only run')
    selection.add_argument('--asins', required=True,
                           help='comma/newline ASINs, or a JSON list/file containing a list or {asins:[...]}')
    selection.add_argument('--out', required=True)
    selection.add_argument('--selection-id', default='')
    selection.set_defaults(func=cmd_production_translation_selection)

    pc = sub.add_parser('preclean', help='\u5168\u79bb\u7ebf\uff1aTranslation V2 Pre-Clean \u6e05\u6d17\u4e0e\u5168\u91cf\u5ba1\u8ba1')
    pc.add_argument('--products', required=True,
                    help='\u5185\u90e8\u7814\u7a76 CSV\u3001\u89c4\u8303\u5316\u5546\u54c1 JSON \u6570\u7ec4\uff0c\u6216\u5e26 records \u7684\u5185\u90e8\u7814\u7a76 JSON')
    pc.add_argument('--out-dir', default=str(OUTPUTS / 'translation_v2_preclean'),
                    help='Pre-Clean \u72ec\u7acb\u8f93\u51fa\u76ee\u5f55')
    pc.set_defaults(func=cmd_preclean)

    do = sub.add_parser('dictionary-only', help='\u5168\u79bb\u7ebf\uff1a\u753b\u50cf\u3001\u5b57\u5178\u5019\u9009\u4e0e\u786e\u5b9a\u6027\u89e3\u6790\uff08\u7edd\u4e0d\u8c03\u7528\u7ffb\u8bd1 API\uff09')
    do.add_argument('--products', required=True,
                    help='\u5185\u90e8\u7814\u7a76 CSV\u3001\u89c4\u8303\u5316\u5546\u54c1 JSON \u6570\u7ec4\uff0c\u6216\u5e26 records \u7684\u5185\u90e8\u7814\u7a76 JSON')
    do.add_argument('--out', default=str(OUTPUTS / 'translation_v2_dictionary'),
                    help='\u72ec\u7acb\u62a5\u544a\u76ee\u5f55')
    do.add_argument('--top-n', type=int, default=100,
                    help='\u5019\u9009\u6e05\u5355\u9ed8\u8ba4\u9ad8\u9891\u89c2\u5bdf\u7a97\u53e3\uff08\u62a5\u544a\u4ecd\u4fdd\u7559\u5168\u90e8\u5019\u9009\uff09')
    do.set_defaults(func=cmd_dictionary_only)

    q = sub.add_parser('qa', help='\u79bb\u7ebf\uff1a\u5546\u54c1\u8868 \u2192 QA \u7ed3\u679c')
    q.add_argument('--products', default=str(OUTPUTS / 'products.json'))
    q.add_argument('--out', default=str(OUTPUTS / 'qa.json'))
    q.set_defaults(func=cmd_qa)

    a = sub.add_parser('audit-fields', help='\u79bb\u7ebf\uff1aSource\u2192Raw\u2192Canonical\u2192Derived\u2192Excel \u5b57\u6bb5\u95ed\u73af\u5ba1\u8ba1')
    a.add_argument('--products', default=str(OUTPUTS / 'products.json'), help='\u89c4\u8303\u5316\u5546\u54c1\u8868 JSON')
    a.add_argument('--details', default=str(OUTPUTS / 'details.json'), help='\u8be6\u60c5 raw JSON\uff08\u53ef\u9009\uff09')
    a.add_argument('--rankings', default=str(OUTPUTS / 'rankings.json'), help='\u699c\u5355 raw JSON\uff08\u53ef\u9009\uff09')
    a.add_argument('--html-dir', nargs='+', default=[],
                   help='\u4fdd\u5b58\u7684\u8be6\u60c5 HTML \u76ee\u5f55\uff08\u53ef\u9009\uff0c\u53ef\u4f20\u591a\u4e2a\uff0c\u7528\u4e8e\u8bc6\u522b PARSER_MISSED\uff09')
    a.add_argument('--run-dir', default='', help='\u91c7\u96c6 run \u6839\u76ee\u5f55\uff08\u53ef\u9009\uff0c\u81ea\u52a8\u8bfb\u53d6 ranking_*.html \u4f5c\u4e3a\u7c7b\u76ee\u6765\u6e90\uff09')
    a.add_argument('--workbook', default='', help='\u5bfc\u51fa\u7684 Excel \u5de5\u4f5c\u7c3f\uff08\u53ef\u9009\uff0c\u9010 ASIN \u6838\u9a8c\u5c55\u793a\u5c42\uff09')
    a.add_argument('--translations', default='', help='\u7ffb\u8bd1\u6620\u5c04 JSON\uff08\u53ef\u9009\uff0c\u7528\u4e8e\u4e2d\u6587\u8868\u5bf9\u8d26\uff09')
    a.add_argument('--out', default=str(OUTPUTS / 'field_closure.json'))
    a.add_argument('--md-out', default='', help='Markdown \u8f93\u51fa\u8def\u5f84\uff08\u9ed8\u8ba4\u4e0e JSON \u540c\u540d .md\uff09')
    a.set_defaults(func=cmd_audit_fields)

    qa = sub.add_parser('quality-audit', help='\u79bb\u7ebf\uff1a\u5bf9\u5df2\u6709 V1 \u6570\u636e\u548c HTML \u8fd0\u884c\u7edf\u4e00 Quality Gate')
    qa.add_argument('--rankings', required=True, help='\u699c\u5355\u8bb0\u5f55 JSON')
    qa.add_argument('--details', default='', help='\u8be6\u60c5\u8bb0\u5f55 JSON')
    qa.add_argument('--products', default='', help='\u89c4\u8303\u5316\u5546\u54c1 JSON\uff08\u53ef\u9009\uff09')
    qa.add_argument('--ranking-html', nargs='*', default=[],
                    help='\u4fdd\u5b58\u7684\u699c\u5355 HTML \u76ee\u5f55\uff08\u53ef\u4f20\u591a\u4e2a\uff0c\u79bb\u7ebf\u91cd\u653e\uff09')
    qa.add_argument('--detail-html', nargs='*', default=[],
                    help='\u4fdd\u5b58\u7684\u8be6\u60c5 HTML \u76ee\u5f55\uff08\u53ef\u4f20\u591a\u4e2a\uff0c\u79bb\u7ebf\u91cd\u653e\uff09')
    qa.add_argument('--run-dir', default='', help='\u91c7\u96c6 run \u76ee\u5f55\uff08\u53ef\u9009\uff09')
    qa.add_argument('--source-manifest', default='', help='\u6765\u6e90\u9875\u72b6\u6001/\u8ba1\u5212 manifest JSON')
    qa.add_argument('--translations', default='', help='\u7ffb\u8bd1\u6620\u5c04 JSON\uff08\u53ef\u9009\uff09')
    qa.add_argument('--workbook', default='', help='Excel \u5de5\u4f5c\u7c3f\uff08\u53ef\u9009\uff09')
    qa.add_argument('--asin', action='append', default=[], help='\u53ea\u5ba1\u67e5\u6307\u5b9a ASIN\uff0c\u53ef\u91cd\u590d')
    from .quality.audit import DEFAULT_CHECKS
    qa.add_argument('--check', action='append', choices=DEFAULT_CHECKS, default=[],
                    help='\u53ea\u8fd0\u884c\u6307\u5b9a\u68c0\u67e5\uff0c\u53ef\u91cd\u590d\uff1b\u9ed8\u8ba4\u8fd0\u884c\u5168\u90e8\u68c0\u67e5')
    qa.add_argument('--run-id', default='', help='\u62a5\u544a\u8fd0\u884c ID\uff08\u53ef\u9009\uff09')
    qa.add_argument('--out-dir', required=True, help='\u8d28\u91cf\u62a5\u544a\u6839\u76ee\u5f55')
    qa.add_argument('--allow-non-ready', action='store_true',
                    help='\u5141\u8bb8 REVIEW/BLOCK \u72b6\u6001\u4ee5\u9000\u51fa\u78010\u7ed3\u675f\uff08\u4ec5\u8bca\u65ad\uff09')
    qa.set_defaults(func=cmd_quality_audit)

    sr = sub.add_parser('stable-research', help='V1\u7a33\u5b9a\u91c7\u96c6 + \u79bb\u7ebf Quality Gate')
    sr.add_argument('--offline', action='store_true', default=argparse.SUPPRESS,
                    help='\u4ec5\u4f7f\u7528\u5df2\u4fdd\u5b58 JSON/HTML\uff0c\u4e0d\u6267\u884c\u4efb\u4f55\u7f51\u7edc\u8bf7\u6c42')
    sr.add_argument('--urls', nargs='*', default=[], help='V1 \u699c\u5355\u6765\u6e90 URL\uff08\u8054\u7f51\u6a21\u5f0f\uff09')
    sr.add_argument('--rankings', default='', help='\u79bb\u7ebf\u699c\u5355\u8bb0\u5f55 JSON')
    sr.add_argument('--details', default='', help='\u79bb\u7ebf\u8be6\u60c5\u8bb0\u5f55 JSON')
    sr.add_argument('--products', default='', help='\u89c4\u8303\u5316\u5546\u54c1 JSON\uff08\u53ef\u9009\uff09')
    sr.add_argument('--out-dir', default='runtime/stable_research',
                    help='\u7a33\u5b9a\u7814\u7a76\u8f93\u51fa\u6839\u76ee\u5f55')
    sr.add_argument('--collect-out-dir', default='', help='V1 \u91c7\u96c6\u8f93\u51fa\u76ee\u5f55')
    sr.add_argument('--quality-out-dir', default='', help='Quality Gate \u62a5\u544a\u6839\u76ee\u5f55')
    sr.add_argument('--ranking-html', nargs='*', default=[])
    sr.add_argument('--detail-html', nargs='*', default=[])
    sr.add_argument('--run-dir', default='')
    sr.add_argument('--source-manifest', default='')
    sr.add_argument('--translations', default='')
    sr.add_argument('--workbook', default='')
    sr.add_argument('--asin', action='append', default=[])
    sr.add_argument('--check', action='append', choices=DEFAULT_CHECKS, default=[])
    sr.add_argument('--run-id', default='')
    sr.add_argument('--allow-non-ready', action='store_true',
                    help='\u5141\u8bb8 REVIEW/BLOCK \u72b6\u6001\u4ee5\u9000\u51fa\u78010\u7ed3\u675f\uff08\u4ec5\u8bca\u65ad\uff09')
    sr.add_argument('--headful', action='store_true')
    sr.add_argument('--profile-dir', default='')
    sr.add_argument('--postal-code', default='28001')
    sr.add_argument('--challenge-wait-seconds', type=float, default=180.0)
    sr.add_argument('--manual-assist', action='store_true')
    sr.add_argument('--pages-per-url', type=int, default=1)
    sr.set_defaults(func=cmd_stable_research)

    x = sub.add_parser('export', help='\u79bb\u7ebf\uff1a\u5546\u54c1\u8868 \u2192 Excel')
    x.add_argument('--products', default=str(OUTPUTS / 'products.json'))
    x.add_argument('--translations', default='')
    x.add_argument('--details', default=DEFAULT_DETAILS,
                   help='\u8be6\u60c5 raw JSON\uff08\u9ed8\u8ba4 outputs/details.json\uff0c\u7528\u4e8e\u5b57\u6bb5\u95ed\u73af\u95e8\u7981\uff09')
    x.add_argument('--rankings', default=DEFAULT_RANKINGS,
                   help='\u699c\u5355 raw JSON\uff08\u9ed8\u8ba4 outputs/rankings.json\uff0c\u7528\u4e8e\u5b57\u6bb5\u95ed\u73af\u95e8\u7981\uff09')
    x.add_argument('--html-dir', nargs='+', default=[], help='\u4fdd\u5b58\u7684\u8be6\u60c5 HTML \u76ee\u5f55\uff08\u53ef\u9009\uff09')
    x.add_argument('--run-dir', default='', help='\u91c7\u96c6 run \u6839\u76ee\u5f55\uff08\u53ef\u9009\uff09')
    x.add_argument('--prev-workbook', default='', help='\u524d\u7248\u5de5\u4f5c\u7c3f\uff08\u6309 ASIN \u4fdd\u7559\u5907\u6ce8\uff09')
    x.add_argument('--images-dir', default='', help='\u672c\u5730\u56fe\u7247\u76ee\u5f55\uff08<ASIN>.png/.jpg/.jpeg\uff09')
    x.add_argument('--category-planning', default='', help='\u7c7b\u76ee\u89c4\u5212 JSON\uff08\u5b57\u5178\u884c\u6570\u7ec4\u6216\u4e8c\u7ef4\u6570\u7ec4\uff09')
    x.add_argument('--out', default=str(OUTPUTS / '\u9009\u54c1\u6e05\u5355.xlsx'))
    x.add_argument('--force', action='store_true',
                   help='\u8df3\u8fc7 QA \u786c\u95e8\u7981\uff08\u5b58\u5728 P0/P1 \u4e5f\u5bfc\u51fa\uff0c\u4fdd\u7559\u4e0a\u6e38\u8bc1\u636e\uff09')
    x.add_argument('--profile', choices=('research', 'business', 'task'), default='research',
                   help='research=\u7c7b\u76ee\u89c4\u5212+\u53cc\u8bed\u4e09\u8868\uff1bbusiness=\u4ec5\u897f\u8bed/\u4e2d\u6587\u4e24\u8868\uff1btask=\u4e09\u8868+\u91c7\u96c6\u4efb\u52a1\u5143\u6570\u636e')
    x.set_defaults(func=cmd_export)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    from .access.detector import AccessStopError
    from .access.location import DeliveryLocationError
    from .monitoring.snapshot import SnapshotIncompleteError
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (AccessStopError, DeliveryLocationError, SnapshotIncompleteError) as e:
        # \u8bbf\u95ee\u95e8\u7981\u6216\u914d\u9001\u5730\u70b9\u65e0\u6cd5\u786e\u8ba4\uff1a\u505c\u6b62\u91c7\u96c6\uff0c\u9000\u51fa\u7801 2
        parser.exit(2, '!! %s\n' % e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
