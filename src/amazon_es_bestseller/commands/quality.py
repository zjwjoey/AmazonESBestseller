# -*- coding: utf-8 -*-
'Offline QA, field-closure, and stable-research quality-gate handlers.'
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, Mapping

from .collection import cmd_collect
from .common import _load_json, _safe_print, _save_json

def cmd_qa(args) -> None:
    '\u5546\u54c1\u8868 \u2192 QA \u7ed3\u679c\uff08qa.json\uff09+ \u63a7\u5236\u53f0\u6c47\u603b\u3002'
    from ..qa.run import qa_summary, run_qa

    products = _load_json(args.products)
    results = []
    p0p1 = []
    for p in products:
        res = run_qa(p)
        rec = {'asin': p.get('asin'), 'qa_status': res['qa_status'], 'counts': res['counts'],
               'issues': [{'code': i.code, 'severity': i.severity, 'field': i.field,
                           'message': i.message} for i in res['qa_issues']]}
        results.append(rec)
        for i in res['qa_issues']:
            if i.severity in ('P0', 'P1'):
                p0p1.append((p.get('asin'), i.code, i.message))
    summary = qa_summary(products)
    out = {'summary': summary, 'records': results}
    _save_json(out, args.out)
    print('QA\uff1a%s' % summary)
    print('QA \u7ed3\u679c \u2192 %s' % args.out)
    if p0p1:
        _safe_print('!! P0/P1 \u95ee\u9898 %d \u6761\uff1a' % len(p0p1))
        for asin, code, msg in p0p1[:20]:
            _safe_print('   %s %s: %s' % (asin, code, msg))
    else:
        print('0 P0 / 0 P1 OK')


def cmd_audit_fields(args) -> None:
    'Audit Source \u2192 Raw \u2192 Canonical \u2192 Derived \u2192 Excel without mutation.'
    from ..qa.field_closure import audit_field_closure, write_report

    products = _load_json(args.products)
    details = _load_json(args.details) if args.details else []
    rankings = _load_json(args.rankings) if args.rankings else []
    translations = _load_json(args.translations) if args.translations else None
    # Field closure may inspect large saved HTML pages and therefore take a few
    # minutes.  Emit an immediate, flushed status line so a long-running audit
    # is distinguishable from a hung process; the final summary remains the
    # authoritative result.
    print('\u5f00\u59cb\u5b57\u6bb5\u95ed\u73af\u5ba1\u67e5\uff1a%d SKU\uff1bHTML=%s' %
          (len(products), '\u5df2\u542f\u7528' if args.html_dir else '\u672a\u542f\u7528'), flush=True)
    report = audit_field_closure(products, details=details, rankings=rankings,
                                 html_dir=args.html_dir or None, run_dir=args.run_dir or None,
                                 workbook_path=args.workbook or None, translations=translations)
    write_report(report, args.out, args.md_out or None)
    s = report['summary']
    print('Field Closure Audit\uff1a%d SKU\u3001%d \u5b57\u6bb5\uff1bPASS %d / SOURCE_MISSING %d / PARSER_MISSED %d / MAPPING_MISSED %d / DERIVED_MISSING %d / EXPORT_MISMATCH %d / IMAGE_MISSING %d'
          % (s['total_skus'], s['fields_checked'], s['pass'], s['SOURCE_MISSING'],
             s['PARSER_MISSED'], s['MAPPING_MISSED'], s['DERIVED_MISSING'],
             s.get('EXPORT_VALUE_MISMATCH', 0), s.get('IMAGE_MISSING', 0)))
    print('\u5ba1\u8ba1 JSON \u2192 %s' % args.out)
    print('\u5ba1\u8ba1 Markdown \u2192 %s' % (args.md_out or str(Path(args.out).with_suffix('.md'))))


def _load_quality_records(path: str, kind: str, *, required: bool = False) -> list:
    'Load a JSON array or a named record wrapper for the Quality Gate.'
    if not path:
        if required:
            raise SystemExit('quality-audit \u9700\u8981 --%s' % kind)
        return []
    data = _load_json(path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in (kind, 'records', 'items'):
            if isinstance(data.get(key), list):
                return data[key]
    raise SystemExit('\u8f93\u5165\u6587\u4ef6\u4e0d\u662f %s \u8bb0\u5f55\u6570\u7ec4\uff1a%s' % (kind, path))


def _quality_audit_from_args(args, *, network_mode: str = 'OFFLINE_QUALITY_AUDIT') -> dict:
    from ..quality.audit import run_quality_audit
    from ..quality.report import write_quality_report

    rankings = _load_quality_records(args.rankings, 'rankings', required=True)
    details = _load_quality_records(getattr(args, 'details', ''), 'details')
    products = _load_quality_records(getattr(args, 'products', ''), 'products')
    source_manifest = (_load_json(args.source_manifest)
                       if getattr(args, 'source_manifest', '') else None)
    if source_manifest is None and getattr(args, 'run_dir', ''):
        status_path = Path(args.run_dir) / 'page_statuses.json'
        if status_path.exists():
            source_manifest = _load_json(str(status_path))
    translations = (_load_json(args.translations)
                    if getattr(args, 'translations', '') else None)
    report = run_quality_audit(
        rankings,
        details,
        products=products or None,
        ranking_html_dirs=getattr(args, 'ranking_html', None) or None,
        detail_html_dirs=getattr(args, 'detail_html', None) or None,
        run_dir=getattr(args, 'run_dir', '') or None,
        source_manifest=source_manifest,
        translations=translations,
        workbook_path=getattr(args, 'workbook', '') or None,
        asins=getattr(args, 'asin', None) or None,
        checks=getattr(args, 'check', None) or None,
        profile='stable-research',
        network_mode=network_mode,
        run_id=getattr(args, 'run_id', '') or '',
    )
    saved = write_quality_report(report, args.out_dir)
    report['report'] = saved
    return report


def _print_quality_result(report: Mapping) -> None:
    summary = report.get('summary') or {}
    saved = report.get('report') or {}
    print('Quality Gate\uff1a%s\uff1b\u7814\u7a76\u72b6\u6001\uff1a%s\uff1bSKU %d\uff1bBLOCK %d / REVIEW %d / WARN %d' % (
        report.get('final_quality_status', 'BLOCKED'),
        (report.get('gate') or {}).get('research_status', 'BLOCKED'),
        summary.get('total_skus', 0), summary.get('block', 0),
        summary.get('review', 0), summary.get('warn', 0)))
    print('\u7f51\u7edc\u8bf7\u6c42\uff1a%d\uff1b\u62a5\u544a \u2192 %s' %
          (summary.get('network_requests', 0), saved.get('path', '')))


def cmd_quality_audit(args) -> None:
    'Offline-only audit of saved V1 records and HTML evidence.'
    report = _quality_audit_from_args(args)
    _print_quality_result(report)
    if ((report.get('gate') or {}).get('research_status') != 'RESEARCH_READY'
            and not args.allow_non_ready):
        raise SystemExit(2)


def _latest_run_dir(root: str) -> str:
    runs = sorted(Path(root).glob('runs/*'), reverse=True)
    return str(runs[0]) if runs else ''


def cmd_stable_research(args, *, collect_command: Callable = cmd_collect,
                        parser_factory: Callable[[], argparse.ArgumentParser] | None = None) -> None:
    'Run unchanged V1 collection, then the offline stable-research gate.'
    from ..quality.profile import execution_plan_text

    print(execution_plan_text())
    collect_root = Path(args.collect_out_dir or (Path(args.out_dir) / 'collection'))
    quality_root = Path(args.quality_out_dir or (Path(args.out_dir) / 'quality'))
    if args.offline:
        if not args.rankings:
            raise SystemExit('stable-research --offline \u9700\u8981 --rankings')
        audit_args = argparse.Namespace(**vars(args))
        audit_args.out_dir = str(quality_root)
        report = _quality_audit_from_args(audit_args)
    else:
        if not args.urls:
            raise SystemExit('stable-research \u8054\u7f51\u6a21\u5f0f\u9700\u8981 --urls\uff1b\u79bb\u7ebf\u6a21\u5f0f\u8bf7\u4f7f\u7528 --offline --rankings')
        collect_args = argparse.Namespace(
            offline=False, urls=args.urls, out_dir=str(collect_root),
            headful=args.headful, profile_dir=args.profile_dir,
            postal_code=args.postal_code,
            challenge_wait_seconds=args.challenge_wait_seconds,
            manual_assist=args.manual_assist, pages_per_url=args.pages_per_url,
            rankings_only=False, rankings_file='', manifest='', progress='',
        )
        collect_command(collect_args, parser_factory() if parser_factory else argparse.ArgumentParser(prog='amazon-es'))
        audit_args = argparse.Namespace(**vars(args))
        audit_args.rankings = str(collect_root / 'rankings.json')
        audit_args.details = str(collect_root / 'details.json')
        audit_args.products = args.products or ''
        audit_args.out_dir = str(quality_root)
        audit_args.run_dir = args.run_dir or _latest_run_dir(str(collect_root))
        audit_args.ranking_html = args.ranking_html or (
            [str(Path(audit_args.run_dir) / 'html')
             if (Path(audit_args.run_dir) / 'html').is_dir() else audit_args.run_dir]
            if audit_args.run_dir else [])
        audit_args.detail_html = args.detail_html or [str(collect_root / 'html')]
        report = _quality_audit_from_args(
            audit_args, network_mode='V1_COLLECTION_THEN_OFFLINE_AUDIT')
    _print_quality_result(report)
    if ((report.get('gate') or {}).get('research_status') != 'RESEARCH_READY'
            and not args.allow_non_ready):
        raise SystemExit(2)
