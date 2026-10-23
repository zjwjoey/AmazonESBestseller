"""Read-only saved-page inventory and reproducible completeness comparison.

No browser or network adapter is imported. Outputs always go to --output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from copy import deepcopy
from collections import Counter, defaultdict
from pathlib import Path

from lxml import html as lh
from amazon_es_bestseller.collection.detail_v2 import parse_detail_evidence_v2


def text(node):
    node = deepcopy(node)
    for unwanted in node.xpath('.//script|.//style|.//template|.//*[@hidden]'):
        unwanted.drop_tree()
    for cut in node.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," a-truncate-cut ")]'):
        if cut.getparent().xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," a-truncate-full ")]'):
            cut.drop_tree()
    return re.sub(r"\s+", " ", node.text_content()).strip()


def inventory(roots):
    unique = {}
    counts = Counter()
    for root in roots:
        for path in sorted(root.rglob("*.html")):
            body = path.read_bytes()
            tree = lh.fromstring(body)
            title = tree.xpath('//*[@id="productTitle"]')
            if not title:
                continue
            counts["product_pages"] += 1
            counts["desktop_breadcrumb_pages"] += bool(tree.xpath('//*[@id="desktop-breadcrumbs_feature_div"]'))
            owned = tree.xpath('//input[@id="ASIN" or @name="ASIN"]/@value')
            asin = next((a.upper() for a in owned if re.fullmatch(r"[A-Z0-9]{10}", a, re.I)), path.stem)
            if not re.fullmatch(r"[A-Z0-9]{10}", asin, re.I) or asin in unique:
                continue
            category = tree.xpath('//*[@id="desktop-breadcrumbs_feature_div" or @id="wayfinding-breadcrumbs_feature_div"]//a')
            attrs = tree.xpath('//*[@id="prodDetails" or @id="productOverview_feature_div"]//tr')
            unique[asin] = {"asin": asin, "path": str(path), "sha256": hashlib.sha256(body).hexdigest(),
                            "title": text(title[0]), "category": [text(a) for a in category],
                            "density": len(attrs)}
    return unique, dict(counts)


def run(roots, count, output, baseline=None):
    output.mkdir(parents=True, exist_ok=True)
    if (output / "products.jsonl").exists():
        raise ValueError("choose a new output directory; prior analysis is immutable")
    pages, counts = inventory(roots) if baseline is None else ({}, {})
    groups = defaultdict(list)
    for row in pages.values():
        groups[tuple(row["category"][:2])].append(row)
    # Round-robin category groups, with both dense and sparse layouts retained.
    for rows in groups.values():
        rows.sort(key=lambda r: (r["density"], r["asin"]))
        rows[:] = rows[::2] + rows[1::2][::-1]
    selected = []
    while baseline is None and len(selected) < min(count, len(pages)):
        for key in sorted(groups):
            if groups[key] and len(selected) < count:
                selected.append(groups[key].pop(0))
    if baseline is not None:
        original = json.loads((baseline / "inputs.json").read_text(encoding="utf8"))
        selected, counts = original["selected"], original["inventory"]
        pages = {row["asin"]:row for row in selected}
    (output / "inputs.json").write_text(json.dumps({"inventory": counts, "unique_products": len(pages),
        "selected": selected}, ensure_ascii=True, indent=2), encoding="utf8")
    started = time.perf_counter()
    with (output / "products.jsonl").open("w", encoding="utf8") as handle:
        for i, row in enumerate(selected, 1):
            raw = Path(row["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("saved input changed: " + row["path"])
            body = raw.decode("utf8")
            record = parse_detail_evidence_v2(body, row["asin"])
            tree = lh.fromstring(body)
            ids = Counter(tree.xpath('//*[@id]/@id'))
            independent = []
            for tr in tree.xpath('//*[@id="prodDetails" or @id="productOverview_feature_div" or starts-with(@id,"productDetails_")]//tr'):
                cells = tr.xpath('./th|./td')
                if len(cells) == 2 and text(cells[0]) and text(cells[1]):
                    independent.append([text(cells[0]), text(cells[1])])
            handle.write(json.dumps({**row, "record": record, "independent_table_rows": independent,
                "product_region_ids": {k:v for k,v in ids.items() if re.search(r"detail|overview|breadcrumb|description|aplus", k, re.I)}},
                ensure_ascii=True) + "\n")
            handle.flush()
            (output / "progress.json").write_text(json.dumps({"completed": i, "total": len(selected),
                "elapsed_seconds": time.perf_counter()-started}), encoding="utf8")
    print(json.dumps({"selected": len(selected), "inventory": counts, "elapsed_seconds": time.perf_counter()-started}))
    if baseline is not None:
        before = {r["asin"]:r for r in map(json.loads, (baseline / "products.jsonl").read_text(encoding="utf8").splitlines())}
        after = list(map(json.loads, (output / "products.jsonl").read_text(encoding="utf8").splitlines()))
        changes, counters = [], Counter()
        for row in after:
            old, new = before[row["asin"]]["record"], row["record"]
            old_attrs = {(a['section'],a['label_raw'],a['value_raw']) for a in old['attributes']}
            new_attrs = {(a['section'],a['label_raw'],a['value_raw']) for a in new['attributes']}
            counters['original_attributes'] += len(old['attributes'])
            counters['new_attributes'] += len(new['attributes'])
            counters['lost_attribute_pairs'] += len(old_attrs-new_attrs)
            for field in sorted(set(old)|set(new)):
                if old.get(field) != new.get(field):
                    value, prior = new.get(field), old.get(field)
                    if not value and not prior:
                        kind = 'EMPTY_SCHEMA_EXTENSION'
                    elif isinstance(prior, dict) and isinstance(value, dict) and all(value.get(k)==v for k,v in prior.items()):
                        kind = 'METADATA_EXTENSION'
                    elif field == 'aplus_content_raw':
                        kind = 'NEW_RAW_REGION'
                    elif not prior:
                        kind = 'FIELD_ADDED'
                    else:
                        kind = 'FIELD_CHANGED'
                    counters[kind+':'+field] += 1
                    changes.append({'asin':row['asin'],'field':field,'classification':kind,'before':old.get(field),'after':new.get(field)})
        (output/'differences.json').write_text(json.dumps({'products':len(after),'counts':dict(counters),'changes':changes},ensure_ascii=True,indent=2),encoding='utf8')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", default=[])
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.count <= 300:
        parser.error("count must be 1..300")
    if not args.root and args.baseline is None:
        parser.error("root or baseline required")
    run(args.root, args.count, args.output, args.baseline)
