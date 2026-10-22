"""Offline, same-input detail parser benchmark; never performs network IO."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path

from bs4 import BeautifulSoup

try:
    import psutil
except ImportError:  # pragma: no cover - optional measurement on lean CI hosts
    psutil = None

from amazon_es_bestseller.categories.provenance import category_evidence_from_detail
from amazon_es_bestseller.collection.checkpoints import write_checkpoint
from amazon_es_bestseller.collection.detail import _parse_detail_page_soup
from amazon_es_bestseller.collection.detail_v2 import (
    _canonical_asin, _page_asin_signals, _variation_evidence,
    parse_detail_evidence_v2,
)
from amazon_es_bestseller.identity import resolve_identity
from amazon_es_bestseller.pipeline import normalize_product


def _reference_three_dom(html: str, asin: str, url: str) -> dict:
    """Reproduce the pre-optimization V2 call graph with current P1-P3 rules."""
    # V1 detail extraction created DOM 1; page-wide identity scan created DOM 2;
    # canonical extraction created DOM 3. Keeping this benchmark adapter here
    # isolates the P4 change from intentional P1-P3 output improvements.
    detail_soup = BeautifulSoup(html, "lxml")
    base = _parse_detail_page_soup(detail_soup, html, asin)
    variation = _variation_evidence(html)
    page_asins, weak_page_asins = _page_asin_signals(html)
    canonical_asin = _canonical_asin(BeautifulSoup(html, "lxml"))
    identity = resolve_identity(
        ranking_asin=asin, requested_asin=asin, requested_url=url,
        final_url_asin=url, canonical_asin=canonical_asin,
        embedded_asins=page_asins,
        weak_embedded_asins=weak_page_asins,
        parsed_detail_asin=variation.get("current_asin") or "",
        parent_asin=variation.get("parent_asin") or base.get("parent_asin") or "",
        variation_family_asins=variation.get("family_asins") or [],
    )
    result = dict(base)
    result.update({
        "parser_version": "collection.detail_v2",
        "detail_parser_version": "collection.detail_v2",
        "detail_parser_contract_version": 2,
        "variation_evidence": variation,
        "variation_family_asins": variation["family_asins"],
        "requested_asin": identity["requested_asin"],
        "resolved_asin": identity["resolved_asin"],
        "identity_status": identity["identity_status"],
        "identity_status_code": identity["identity_status_code"],
        "identity_evidence": identity["identity_evidence"],
        "category_evidence": category_evidence_from_detail(base).to_dict(),
        "ordered_detail_evidence": list(base.get("attributes") or []),
    })
    return result


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "total_seconds": round(sum(values), 6),
        "mean_ms": round(statistics.mean(values) * 1000, 3),
        "p50_ms": round(statistics.median(values) * 1000, 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(.95 * (len(ordered) - 1)))] * 1000, 3),
    }


def run(input_dir: Path, count: int, mode: str) -> dict:
    files = sorted(input_dir.glob("*.html"))[:count]
    if len(files) != count:
        raise ValueError(f"expected {count} HTML files, found {len(files)}")
    proc = psutil.Process() if psutil is not None else None
    rss_start = proc.memory_info().rss if proc else None
    peak_rss = rss_start
    read_times, parse_times, normalize_times, checkpoint_times = [], [], [], []
    identities, attribute_rows, records = {}, 0, []
    manifest_digest = hashlib.sha256()
    cpu_start, wall_start = time.process_time(), time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="detail_p1_p4_benchmark_") as temp_dir:
        for path in files:
            read_start = time.perf_counter()
            payload = path.read_bytes()
            html = payload.decode("utf-8")
            read_times.append(time.perf_counter() - read_start)
            manifest_digest.update(path.name.encode("utf-8"))
            manifest_digest.update(hashlib.sha256(payload).digest())
            asin = path.stem.upper()
            url = f"https://www.amazon.es/dp/{asin}"

            parse_start = time.perf_counter()
            if mode == "reference_three_dom":
                record = _reference_three_dom(html, asin, url)
            else:
                record = parse_detail_evidence_v2(
                    html, asin, requested_url=url, final_url=url)
            parse_times.append(time.perf_counter() - parse_start)

            normalize_start = time.perf_counter()
            normalized = normalize_product(record)
            normalize_times.append(time.perf_counter() - normalize_start)

            checkpoint_start = time.perf_counter()
            write_checkpoint(temp_dir, asin, {
                "status": "success", "record": record, "normalized": normalized,
            })
            checkpoint_times.append(time.perf_counter() - checkpoint_start)

            identities[record.get("identity_status", "")] = identities.get(
                record.get("identity_status", ""), 0) + 1
            attribute_rows += len(record.get("attributes") or [])
            records.append({"asin": asin, "record": record, "normalized": normalized})
            if proc:
                peak_rss = max(peak_rss, proc.memory_info().rss)

    canonical = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        "mode": mode,
        "input_dir": str(input_dir),
        "file_count": len(files),
        "input_bytes": sum(path.stat().st_size for path in files),
        "input_manifest_sha256": manifest_digest.hexdigest(),
        "record_output_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "phases": {
            "read": _stats(read_times),
            "parse_extract_category_identity": _stats(parse_times),
            "normalize": _stats(normalize_times),
            "checkpoint_write": _stats(checkpoint_times),
        },
        "total_wall_seconds": round(time.perf_counter() - wall_start, 6),
        "process_cpu_seconds": round(time.process_time() - cpu_start, 6),
        "rss_start_bytes": rss_start,
        "rss_peak_observed_bytes": peak_rss,
        "rss_observed_delta_bytes": max(0, peak_rss - rss_start) if proc else None,
        "identity_distribution": identities,
        "attribute_rows": attribute_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=44)
    parser.add_argument("--mode", choices=("reference_three_dom", "current_one_dom"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.input_dir, args.count, args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
