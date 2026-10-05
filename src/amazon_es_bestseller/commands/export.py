# -*- coding: utf-8 -*-
'Frozen workbook export handler and its formal QA/closure gate.'
from __future__ import annotations

from .common import (DEFAULT_DETAILS, DEFAULT_RANKINGS, _load_category_planning,
                     _load_evidence_json, _load_images_by_asin, _load_json)

def cmd_export(args) -> None:
    '\u5546\u54c1\u8868 \u2192 Excel \u5de5\u4f5c\u7c3f\uff08B3x \u91cd\u5199\u4e3a\u65b0 3 \u8868/26 \u5217\u5951\u7ea6\uff09\u3002\n\n    QA \u786c\u95e8\u7981\uff08QA_RULES \xa731\uff09\uff1a\u5bfc\u51fa\u524d\u8dd1\u5168\u91cf QA\uff0c\u5b58\u5728\u4efb\u4f55 P0/P1 \u5373\u62d2\u7edd\u5bfc\u51fa\uff0c\n    \u9664\u975e\u663e\u5f0f --force\uff08\u4fdd\u7559\u4e0a\u6e38\u9519\u8bef\u8bc1\u636e\uff0c\u4e0d\u9759\u9ed8\u4fee\u590d\uff0c\xa725\uff09\u3002\n    '
    from ..export.excel import export_workbook
    from ..qa.run import blocking_issues

    products = _load_json(args.products)
    blocked = blocking_issues(products)

    translations = _load_json(args.translations) if args.translations else None
    closure_findings = []
    from ..qa.field_closure import audit_field_closure
    details = _load_evidence_json(getattr(args, 'details', ''), DEFAULT_DETAILS)
    rankings = _load_evidence_json(getattr(args, 'rankings', ''), DEFAULT_RANKINGS)
    closure_enabled = bool(args.translations or details or rankings or
                            getattr(args, 'html_dir', None) or getattr(args, 'run_dir', ''))
    closure = audit_field_closure(products, details=details, rankings=rankings,
                                  html_dir=getattr(args, 'html_dir', None) or None,
                                  run_dir=getattr(args, 'run_dir', '') or None,
                                  translations=translations) if closure_enabled else {'records': []}
    if not closure_enabled:
        # \u95e8\u7981\u964d\u7ea7\u5fc5\u987b\u53ef\u89c1\uff1a\u9759\u9ed8\u8df3\u8fc7\u4f1a\u8ba9\u5bfc\u51fa\u770b\u8d77\u6765\u901a\u8fc7\u4e86\u5b9e\u9645\u672a\u6267\u884c\u7684\u5ba1\u8ba1\u3002
        print('\u8b66\u544a\uff1a\u672a\u627e\u5230 details/rankings/translations \u8bc1\u636e\uff0c\u5b57\u6bb5\u95ed\u73af\u95e8\u7981\u672a\u8fd0\u884c\uff1b'
              '\u672c\u6b21\u4ec5\u6267\u884c QA \u95e8\u7981')
    blocked_closure = [r for r in closure.get('records', [])
                       if r.get('severity') == 'P1' and r.get('classification') in
                       {'PARSER_MISSED', 'MAPPING_MISSED', 'DERIVED_MISSING',
                        'TRANSLATION_INCOMPLETE'}]
    closure_findings = [(r.get('asin'), r.get('classification'), r.get('message'))
                        for r in blocked_closure]
    blocked = blocked + closure_findings
    if blocked and not args.force:
        lines = ['QA/\u5b57\u6bb5\u95ed\u73af\u95e8\u7981\u672a\u901a\u8fc7\uff1a%d \u6761 P0/P1 \u95ee\u9898\uff0c\u62d2\u7edd\u5bfc\u51fa\uff08--force \u5f3a\u5236\uff09'
                 % len(blocked)]
        for asin, code, msg in blocked[:10]:
            lines.append('   %s %s: %s' % (asin, code, msg))
        raise SystemExit('\n'.join(lines))
    if blocked and args.force:
        print('\u8b66\u544a\uff1a--force \u5ffd\u7565 %d \u6761 QA/\u5b57\u6bb5\u95ed\u73af P0/P1 \u95ee\u9898' % len(blocked))
    images_by_asin = _load_images_by_asin(args.images_dir, products)
    category_planning = _load_category_planning(args.category_planning)
    prev_workbook = None
    if args.prev_workbook:
        import openpyxl
        prev_workbook = openpyxl.load_workbook(args.prev_workbook)
    wb = export_workbook(products, translations=translations,
                         images_by_asin=images_by_asin,
                         category_planning=category_planning,
                         prev_workbook=prev_workbook, out_path=args.out,
                         profile=getattr(args, 'profile', 'research'))
    print('export \u5b8c\u6210\uff1a%s\uff08%s \u6761\u5546\u54c1\uff0c%d \u5f20\u8868\uff09' % (args.out, len(products), len(wb.sheetnames)))
