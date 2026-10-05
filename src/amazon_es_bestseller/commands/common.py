# -*- coding: utf-8 -*-
'Shared file, evidence, and safe-output helpers for CLI command handlers.'
from __future__ import annotations

import argparse
import csv
from io import BytesIO
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import List, Mapping, Optional

#: \u9ed8\u8ba4\u6570\u636e\u76ee\u5f55\uff08\u4ed3\u5e93\u76f8\u5bf9\uff0c\u907f\u514d\u786c\u7f16\u7801\u7edd\u5bf9\u8def\u5f84\uff09
OUTPUTS = Path('outputs')

#: \u8bc1\u636e\u8f93\u5165\u9ed8\u8ba4\u8def\u5f84\u3002export \u4e0e enrich/qa \u5171\u7528\u540c\u4e00\u7ec4\u9ed8\u8ba4\u503c\uff0c\u4fdd\u8bc1\u5b57\u6bb5\u95ed\u73af
#: \u95e8\u7981\u5728\u9ed8\u8ba4\u8c03\u7528\u4e0b\u4e5f\u4f1a\u8fd0\u884c\uff08\u7f3a\u7701\u65f6\u66fe\u9759\u9ed8\u8df3\u8fc7\uff0c\u89c1 QA_RULES \xa731\uff09\u3002
DEFAULT_DETAILS = str(OUTPUTS / 'details.json')
DEFAULT_RANKINGS = str(OUTPUTS / 'rankings.json')


def _safe_print(*parts) -> None:
    'Print diagnostics without letting a narrow Windows code page abort QA.'
    text = ' '.join(str(part) for part in parts)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        sys.stdout.write(text.encode(encoding, errors='replace').decode(encoding) + '\n')


def _load_json(path: Optional[str]) -> list:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise SystemExit('\u627e\u4e0d\u5230\u8f93\u5165\u6587\u4ef6: %s' % path)
    with p.open(encoding='utf-8') as f:
        return json.load(f)


TRANSLATION_RESEARCH_CSV_FIELDS = {
    'ASIN': 'asin',
    '\u5546\u54c1\u540d\u79f0\uff08\u897f\u8bed\uff09': 'title_es_raw',
    '\u54c1\u724c': 'brand',
    '\u4e00\u7ea7\u7c7b\u76ee': 'category_l1',
    '\u4e8c\u7ea7\u7c7b\u76ee': 'category_l2',
    '\u4e09\u7ea7\u7c7b\u76ee': 'category_l3',
    '\u7ec6\u5206\u7c7b\u76ee': 'leaf_category',
    '\u5f53\u524d\u9009\u4e2d\u89c4\u683c / \u53d8\u4f53\uff08\u897f\u8bed\uff09': 'selected_variant_es',
    '\u6838\u5fc3\u89c4\u683c\uff08\u897f\u8bed\uff09': 'specification_es',
    '\u5b8c\u6574\u5546\u54c1\u8be6\u60c5\uff08\u897f\u8bed\u539f\u6587\uff09': 'product_details_es',
    '\u5546\u54c1\u5356\u70b9\uff08\u897f\u8bed\u539f\u6587\uff09': 'feature_bullets_es',
}


def _load_translation_products(path: Optional[str]) -> list:
    'Load V2 JSON records or the frozen internal-research CSV contract.'
    if not path or Path(path).suffix.casefold() != '.csv':
        data = _load_json(path)
        if isinstance(data, dict) and isinstance(data.get('records'), list):
            return data['records']
        return data
    p = Path(path)
    if not p.exists():
        raise SystemExit('\u627e\u4e0d\u5230\u8f93\u5165\u6587\u4ef6: %s' % path)
    with p.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or ())
        if 'ASIN' not in headers:
            raise SystemExit('Translation V2 CSV \u7f3a\u5c11 ASIN \u5217: %s' % path)
        records = []
        for row in reader:
            # Keep every frozen Spanish-Master column (notably notes and URLs)
            # while adding canonical aliases consumed by Translation V2.
            record = {str(key): (value or '').strip() for key, value in row.items()
                      if key is not None}
            for source, target in TRANSLATION_RESEARCH_CSV_FIELDS.items():
                if not record.get(target):
                    record[target] = (row.get(source) or '').strip()
            records.append(record)
    return records


def _save_json(data, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_evidence_json(path: Optional[str], default_path: str):
    '\u95ed\u73af\u95e8\u7981\u7684\u8bc1\u636e\u8f93\u5165\uff1a\u663e\u5f0f\u6307\u5b9a\u4f46\u7f3a\u5931 \u2192 \u62a5\u9519\uff1b\u9ed8\u8ba4\u8def\u5f84\u7f3a\u5931 \u2192 \u89c6\u4e3a\u4e0d\u53ef\u7528\u3002\n\n    \u9ed8\u8ba4\u8def\u5f84\u53ef\u4ee5\u5408\u6cd5\u5730\u4e0d\u5b58\u5728\uff08\u4f8b\u5982\u53ea\u8dd1\u79bb\u7ebf\u5b50\u94fe\uff09\uff0c\u6b64\u65f6\u7531\u8c03\u7528\u65b9\u663e\u5f0f\u58f0\u660e\u95e8\u7981\n    \u964d\u7ea7\uff1b\u663e\u5f0f\u4f20\u5165\u7684\u8def\u5f84\u7f3a\u5931\u4ecd\u5fc5\u987b\u5931\u8d25\uff0c\u907f\u514d\u6253\u9519\u8def\u5f84\u88ab\u5f53\u6210"\u6ca1\u6709\u8bc1\u636e"\u3002\n    '
    if not path:
        return None
    if not Path(path).exists():
        if str(path) != str(default_path):
            raise SystemExit('\u627e\u4e0d\u5230\u8f93\u5165\u6587\u4ef6: %s' % path)
        return None
    return _load_json(path)


def _load_category_planning(path: Optional[str]):
    if not path:
        return None
    data = _load_json(path)
    if not isinstance(data, list):
        raise SystemExit('\u7c7b\u76ee\u89c4\u5212 JSON \u9876\u5c42\u5fc5\u987b\u662f\u6570\u7ec4: %s' % path)
    return data


def _load_checkpoint_input(path: Optional[str]):
    'Read the canonical checkpoint directory, with legacy JSON support.'
    if not path:
        return []
    target = Path(path)
    if target.is_dir():
        rows = []
        for item in sorted(target.glob('*.json')):
            try:
                value = json.loads(item.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows
    return _load_json(path)


def _load_images_by_asin(directory: Optional[str], records: list) -> dict:
    if not directory:
        return {}
    root = Path(directory)
    if not root.is_dir():
        print('\u8b66\u544a\uff1a\u56fe\u7247\u76ee\u5f55\u4e0d\u5b58\u5728\uff0c\u8df3\u8fc7\u5185\u5d4c\u56fe\u7247: %s' % directory)
        return {}
    out = {}
    for record in records:
        asin = str(record.get('asin') or '').strip().upper()
        if not asin:
            continue
        for suffix in ('.png', '.jpg', '.jpeg'):
            path = root / (asin + suffix)
            if path.exists():
                try:
                    out[asin] = (BytesIO(path.read_bytes()), 70, 70)
                except OSError as exc:
                    print('\u8b66\u544a\uff1a\u65e0\u6cd5\u8bfb\u53d6\u56fe\u7247 %s\uff1a%s' % (path, exc))
                break
    return out
