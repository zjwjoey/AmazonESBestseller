"""Read-only saved-HTML replay and full-scope candidate review; never promote."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from amazon_es_bestseller.collection.detail import _classify_saved_page, _resolve_page_identity  # noqa: E402
from amazon_es_bestseller.production.spanish_source_closure import (  # noqa: E402
    _merged_records, build_spanish_source_candidate, candidate_manifest_hash,
    write_spanish_source_candidate,
)
from amazon_es_bestseller.quality.source_fields import audit_source_fields  # noqa: E402
from amazon_es_bestseller.quality.detail_identity import extract_detail_identity_signals  # noqa: E402
from amazon_es_bestseller.translation.full_detail import render_details_es, render_bullets_es  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def csv_write(path, rows, keys):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def no_network(*args, **kwargs):
    raise RuntimeError("OFFLINE_REPLAY_NETWORK_FORBIDDEN")


def replay_one(asin, before, paths):
    socket.socket.connect = no_network
    socket.create_connection = no_network
    attempts, rebuilt = [], None
    for path in paths:
        try:
            html_hash = sha(path)
            html = path.read_text(encoding="utf-8")
            meta_path = path.with_suffix(".meta.json")
            meta = read(meta_path) if meta_path.is_file() else {}
            if not isinstance(meta, dict):
                meta = {}
            meta = {**meta, "requested_url": meta.get("requested_url") or before.get("requested_url", ""),
                    "final_url": meta.get("final_url") or before.get("final_url", ""), "cache_html_path": str(path.resolve())}
            classification, access, parsed = _classify_saved_page(html, asin, meta, parser_version="v1")
            identity = _resolve_page_identity(asin, html, meta["final_url"], parsed) if parsed else {}
            attempts.append({"path": str(path), "sha256": html_hash, "classification": classification,
                             "identity_status": identity.get("identity_status")})
            if classification != "VALID_PRODUCT_PAGE" or identity.get("identity_status") != "IDENTITY_MATCH":
                continue
            rebuilt = {**deepcopy(before), **parsed, "identity_status": "MATCH", "identity_status_code": "IDENTITY_MATCH",
                       "identity_evidence": identity["identity_evidence"], "resumed_from_html": True,
                       "offline_replay_evidence": {"html_path": str(path), "html_sha256": html_hash,
                                                   "meta_sha256": sha(meta_path) if meta_path.is_file() else None,
                                                   "original_record_sha256": hashlib.sha256(json.dumps(before, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}}
            rebuilt["detail_identity_signals"] = extract_detail_identity_signals(path.read_bytes().decode("utf-8"))
            break
        except Exception as exc:
            attempts.append({"path": str(path), "error": str(exc), "type": type(exc).__name__})
    if rebuilt is None:
        rebuilt = deepcopy(before)
        rebuilt["offline_replay_status"] = "NOT_REPARSED_OR_IDENTITY_NOT_CONFIRMED"
        changes = []
    else:
        rebuilt["offline_replay_status"] = "REPARSED_IDENTITY_MATCH"
        changes = [key for key in parsed if before.get(key) != rebuilt.get(key)]
    log = {"asin": asin, "status": rebuilt["offline_replay_status"], "changed_fields": changes,
           "attributes_before": len(before.get("attributes") or []), "attributes_after": len(rebuilt.get("attributes") or []),
           "bullets_before": len(before.get("feature_bullets_raw") or []), "bullets_after": len(rebuilt.get("feature_bullets_raw") or []),
           "brand_before": before.get("brand_raw"), "brand_after": rebuilt.get("brand_raw"), "attempts": attempts}
    return rebuilt, log


def validate_reuse_parser(previous_metadata, baseline):
    """Reuse only when the parser's sole code change is _struck_price."""
    key = str(Path("src/amazon_es_bestseller/collection/detail.py"))
    if sha(baseline) != previous_metadata["code_file_sha256"][key]:
        raise ValueError("REUSE_PARSER_BASELINE_HASH_MISMATCH")

    def unchanged_ast(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     or node.name != "_struck_price"]
        return ast.dump(tree, include_attributes=False)

    if unchanged_ast(baseline) != unchanged_ast(ROOT / key):
        raise ValueError("REUSE_NON_PRICE_PARSER_CHANGED")
    allowed = {key, str(Path("src/amazon_es_bestseller/quality/source_fields.py")),
               str(Path("src/amazon_es_bestseller/quality/detail_identity.py"))}
    for name, expected in previous_metadata["code_file_sha256"].items():
        if name not in allowed and sha(ROOT / name) != expected:
            raise ValueError(f"REUSE_OTHER_CODE_DRIFT: {name}")


def replay_validated_one(asin, before, previous):
    """Retain prior lossless parse; refresh only price context and identity QA.

    The full HTML and metadata are rehashed for every SKU. This is not a new
    full parser run and is labelled as such in logs and run metadata.
    """
    from bs4 import BeautifulSoup
    from lxml import html as lxml_html
    from amazon_es_bestseller.collection.detail import _struck_price

    socket.socket.connect = no_network
    socket.create_connection = no_network
    if previous.get("asin") != asin or previous.get("offline_replay_status") != "REPARSED_IDENTITY_MATCH":
        raise ValueError(f"REUSE_UNVERIFIED_CHECKPOINT: {asin}")
    evidence = previous["offline_replay_evidence"]
    expected_original = hashlib.sha256(json.dumps(before, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if evidence.get("original_record_sha256") != expected_original:
        raise ValueError(f"REUSE_ORIGINAL_RECORD_DRIFT: {asin}")
    path = Path(evidence["html_path"])
    data = path.read_bytes()
    html_hash = hashlib.sha256(data).hexdigest()
    meta_path = path.with_suffix(".meta.json")
    meta_hash = sha(meta_path) if meta_path.is_file() else None
    if html_hash != evidence["html_sha256"] or meta_hash != evidence.get("meta_sha256"):
        raise ValueError(f"REUSE_HTML_OR_META_DRIFT: {asin}")
    html = data.decode("utf-8")
    dom = lxml_html.fromstring(html)
    blocks = dom.xpath('//*[@id="corePrice_feature_div" or @id="corePriceDisplay_desktop_feature_div" or @id="apex_price"]')
    # Preserve selector scope/labels/strike ancestors, excluding recommendations.
    fragment = "\n".join(lxml_html.tostring(block, encoding="unicode") for block in blocks)
    rebuilt = deepcopy(previous)
    rebuilt["original_price_raw"] = _struck_price(BeautifulSoup(fragment, "lxml"))
    rebuilt["offline_price_refresh"] = {"producer": "saved_html_price_parser", "html_sha256": html_hash,
        "original_price_raw_before": previous.get("original_price_raw"), "original_price_raw_after": rebuilt["original_price_raw"],
        "parser_source_sha256": sha(ROOT / "src/amazon_es_bestseller/collection/detail.py")}
    rebuilt["detail_identity_signals"] = extract_detail_identity_signals(html)
    rebuilt["offline_replay_mode"] = "VALIDATED_REUSE_PRICE_REFRESH"
    changes = [k for k in before if before.get(k) != rebuilt.get(k)]
    return rebuilt, {"asin": asin, "status": rebuilt["offline_replay_status"],
        "replay_mode": rebuilt["offline_replay_mode"], "changed_fields": changes,
        "attributes_before": len(before.get("attributes") or []), "attributes_after": len(rebuilt.get("attributes") or []),
        "bullets_before": len(before.get("feature_bullets_raw") or []), "bullets_after": len(rebuilt.get("feature_bullets_raw") or []),
        "brand_before": before.get("brand_raw"), "brand_after": rebuilt.get("brand_raw"),
        "previous_original_price_raw": previous.get("original_price_raw"),
        "price_changed_from_previous_replay": previous.get("original_price_raw") != rebuilt["original_price_raw"],
        "attempts": [{"path": str(path), "sha256": html_hash, "meta_sha256": meta_hash, "status": "HASH_VERIFIED_PRICE_REFRESH"}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--historical-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-candidate-hash", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--reuse-validated-replay", type=Path)
    parser.add_argument("--parser-baseline", type=Path)
    args = parser.parse_args()
    socket.socket.connect = no_network
    socket.create_connection = no_network
    root, output = args.production_root.resolve(), args.output_dir.resolve()
    if output.exists() and not args.resume:
        raise FileExistsError(output)
    inputs = {"details": root / "details.json", "rankings": root / "rankings.json",
              "candidate_manifest": root / "candidate_manifest.json",
              "source_audit": root / "source_audit_5480/source_audit_5480.json",
              "historical_audit": args.historical_root / "audit.json",
              "historical_candidate": args.historical_root / "spanish_master_5480.json"}
    hashes = {name: sha(path) for name, path in inputs.items()}
    previous_rows = {}
    reuse_binding = None
    if args.reuse_validated_replay:
        previous_root = args.reuse_validated_replay.resolve()
        previous_metadata = read(previous_root / "input_metadata.json")
        if not args.parser_baseline or previous_metadata["input_sha256"] != hashes:
            raise ValueError("REUSE_INPUT_OR_BASELINE_MISMATCH")
        validate_reuse_parser(previous_metadata, args.parser_baseline)
        previous_rows = {r["asin"]: r for r in read(previous_root / "replayed_details.json")}
        reuse_binding = {"metadata_sha256": sha(previous_root / "input_metadata.json"),
                        "replayed_details_sha256": sha(previous_root / "replayed_details.json"),
                        "parser_baseline_sha256": sha(args.parser_baseline), "previous_root": str(previous_root)}
    candidates, raw_details, rankings = (read(inputs[name]) for name in ("candidate_manifest", "details", "rankings"))
    if candidate_manifest_hash(candidates) != args.expected_candidate_hash:
        raise ValueError("FROZEN_CANDIDATE_HASH_MISMATCH")
    historical_audit = read(inputs["historical_audit"])["closure_audit"]
    original_audit = read(inputs["source_audit"])
    scope = set(original_audit["record_bindings"])
    assert len(scope) == 5480
    original = {row["asin"]: row for row in raw_details if row.get("asin") in scope}
    assert set(original) == scope
    if previous_rows and set(previous_rows) != scope:
        raise ValueError("REUSE_ASIN_SCOPE_MISMATCH")
    html_index = defaultdict(list)
    for directory in sorted((root / "categories").glob("*/detail_cache/html")):
        for path in sorted(directory.glob("*.html")):
            if path.stem.upper() in scope:
                html_index[path.stem.upper()].append(path)
    output.mkdir(parents=True, exist_ok=args.resume)
    (output / "reparsed").mkdir(exist_ok=args.resume)
    if args.resume:
        saved = read(output / "input_metadata.json")
        if saved["input_sha256"] != hashes:
            raise ValueError("RESUME_INPUT_DRIFT")
        if saved.get("reuse_binding") != reuse_binding or any(sha(ROOT / name) != digest for name, digest in saved["code_file_sha256"].items()):
            raise ValueError("RESUME_CODE_OR_REUSE_DRIFT")
    start = time.monotonic()
    def stage(name, **values):
        message = {"stage": name, "elapsed_seconds": round(time.monotonic() - start, 1), **values}
        print(json.dumps(message, ensure_ascii=False), flush=True)
        with (output / "stage.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(message, ensure_ascii=False) + "\n")
    if not args.resume:
        write(output / "input_metadata.json", {"inputs": {k: str(v.resolve()) for k, v in inputs.items()},
          "input_sha256": hashes, "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
          "code_file_sha256": {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / "src/amazon_es_bestseller").rglob("*.py")},
          "parser": "v1", "network": "forbidden", "translation_provider_calls": 0,
          "reuse_binding": reuse_binding,
          "replay_mode": "VALIDATED_REUSE_PRICE_REFRESH" if previous_rows else "FULL_HTML_REPARSE",
          "review_tool_sha256": {str(p.relative_to(ROOT)): sha(p) for p in (Path(__file__), ROOT / "tools/offline_regression_review.py")},
          "scope": 5480, "frozen_candidates": len(candidates), "formal_promotion": False})
    stage("REPLAY_START", scope=len(scope), html_covered=len(html_index))
    replayed, replay_log = [], []
    # Offline CPU parallelism only. Every child rejects socket connections.
    # Existing per-ASIN checkpoints are never overwritten.
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = {(pool.submit(replay_validated_one, asin, original[asin], previous_rows[asin]) if previous_rows
                    else pool.submit(replay_one, asin, original[asin], html_index[asin])): asin for asin in sorted(scope)
                   if not (output / "reparsed" / f"{asin}.json").is_file()}
        for asin in sorted(scope):
            checkpoint = output / "reparsed" / f"{asin}.json"
            if not checkpoint.is_file():
                continue
            rebuilt = read(checkpoint)
            log_path = output / "reparsed" / f"{asin}.log.json"
            if log_path.is_file():
                log = read(log_path)
            else:
                before = original[asin]
                keys = ("brand_raw", "attributes", "feature_bullets_raw", "detail_bullets_raw", "title_es_raw", "parent_asin")
                log = {"asin": asin, "status": rebuilt["offline_replay_status"],
                       "changed_fields": [k for k in keys if before.get(k) != rebuilt.get(k)],
                       "attributes_before": len(before.get("attributes") or []), "attributes_after": len(rebuilt.get("attributes") or []),
                       "bullets_before": len(before.get("feature_bullets_raw") or []), "bullets_after": len(rebuilt.get("feature_bullets_raw") or []),
                       "brand_before": before.get("brand_raw"), "brand_after": rebuilt.get("brand_raw"),
                       "attempts": [rebuilt.get("offline_replay_evidence", {})], "resumed_checkpoint": True}
            replayed.append(rebuilt)
            replay_log.append(log)
        stage("CHECKPOINTS_LOADED", count=len(replayed), workers=args.workers)
        for future in as_completed(pending):
            asin = pending[future]
            rebuilt, log = future.result()
            write(output / "reparsed" / f"{asin}.json", rebuilt)
            write(output / "reparsed" / f"{asin}.log.json", log)
            replayed.append(rebuilt)
            replay_log.append(log)
            if len(replayed) % 50 == 0:
                stage("REPLAY_PROGRESS", processed=len(replayed), total=len(scope))
    replayed.sort(key=lambda r: r["asin"])
    replay_log.sort(key=lambda r: r["asin"])
    write(output / "replayed_details.json", replayed)
    write(output / "replay_log.json", replay_log)
    stage("SOURCE_AUDIT_START")
    merged = _merged_records(candidates, replayed, rankings, scope)
    current_audit = audit_source_fields(merged, ranking_matrix=rankings)
    write(output / "replayed_source_audit.json", current_audit)
    stage("BUILDER_START", source_issues=len(current_audit["issues"]))
    result = build_spanish_source_candidate(candidates, replayed, rankings, current_audit,
               expected_candidate_hash=args.expected_candidate_hash, cache_root=root,
               snapshot_provenance={"offline_replay": True, "original_inputs": hashes, "root": str(root)})
    result["code_versions"] = {"code_root": str(ROOT), "local_uncommitted_repairs": True}
    write_spanish_source_candidate(output / "spanish_candidate", result)
    records = result["records"]
    assert len(records) == 5480 and {r["asin"] for r in records} == scope
    current = result["closure_audit"]
    groups = defaultdict(list)
    for item in current["issues"]:
        groups[(item.get("asin"), item.get("issue_code"), item.get("field"))].append(item)
    comparison = []
    for index, issue in enumerate(historical_audit["issues"]):
        matched = groups.get((issue.get("asin"), issue.get("issue_code"), issue.get("field")), [])
        comparison.append({"historical_issue_index": index, "asin": issue.get("asin"), "issue_code": issue.get("issue_code"),
            "field": issue.get("field"), "status": "STILL_FLAGGED_SAME_ASIN_CODE_FIELD" if matched else "NO_LONGER_FLAGGED_BY_CURRENT_RULES",
            "current_matching_count": len(matched), "historical_issue": issue,
            "limitation": "coarse code/field comparison, not proof of exact source fact repair"})
    csv_write(output / "historical_issue_comparison.csv", comparison,
              ["historical_issue_index", "asin", "issue_code", "field", "status", "current_matching_count", "historical_issue", "limitation"])
    csv_write(output / "current_issues.csv", current["issues"], list(dict.fromkeys(k for row in current["issues"] for k in row)))
    csv_write(output / "replay_changes.csv", replay_log,
              ["asin", "status", "changed_fields", "attributes_before", "attributes_after", "bullets_before", "bullets_after", "brand_before", "brand_after", "attempts"])
    display = [{"asin": r["asin"], "brand": r.get("brand"), "category_l1": r.get("category_l1"),
                "category_l2": r.get("category_l2"), "category_l3": r.get("category_l3"), "leaf_category": r.get("leaf_category"),
                "details_es": render_details_es(r.get("attributes")), "bullets_es": render_bullets_es(r.get("feature_bullets_raw"))} for r in records]
    csv_write(output / "spanish_display_candidate.csv", display, list(display[0]))
    after_hashes = {name: sha(path) for name, path in inputs.items()}
    unchanged = hashes == after_hashes
    bad_replays = [item for item in replay_log if item["status"] != "REPARSED_IDENTITY_MATCH"]
    summary = {"scope": 5480, "unique_asins": len(scope), "replayed": len(scope) - len(bad_replays),
               "replay_unverified": len(bad_replays), "input_files_unchanged": unchanged,
               "candidate_status": result["status"], "source_gate": result["source_gate"], "closure_gate": result["closure_source_gate"],
               "historical_issue_count": len(historical_audit["issues"]), "current_issue_count": len(current["issues"]),
               "historical_issue_types": dict(Counter(i["issue_code"] for i in historical_audit["issues"])),
               "current_issue_types": dict(Counter(i["issue_code"] for i in current["issues"])),
               "current_issue_severity": dict(Counter(i["severity"] for i in current["issues"])),
               "current_affected_skus": len({i["asin"] for i in current["issues"]}),
               "comparison_status": dict(Counter(i["status"] for i in comparison)),
               "changed_skus": sum(bool(i["changed_fields"]) for i in replay_log),
               "attribute_count_decreased_skus": sum(i["attributes_after"] < i["attributes_before"] for i in replay_log),
               "bullet_count_decreased_skus": sum(i["bullets_after"] < i["bullets_before"] for i in replay_log),
               "owner_excluded_asins_retained_for_review": ["B07F6LYVT6", "B077H1MZ35"],
               "translation_started": False, "formal_promotion": False,
               "replay_mode": "VALIDATED_REUSE_PRICE_REFRESH" if previous_rows else "FULL_HTML_REPARSE",
               "price_refreshed_from_verified_html": len(replay_log) if previous_rows else 0,
               "price_changed_from_previous_replay": sum(i.get("price_changed_from_previous_replay", False) for i in replay_log),
               "elapsed_seconds": round(time.monotonic() - start, 1)}
    write(output / "summary.json", summary)
    text = ("# 5480 SKU 离线全量回归\n\n"
            f"HTML重解析：{summary['replayed']}/5480；未验证：{len(bad_replays)}。\n\n"
            f"历史问题：{summary['historical_issue_count']}；当前问题：{summary['current_issue_count']}；影响{summary['current_affected_skus']} SKU。\n\n"
            f"候选状态：{result['status']}。原始输入哈希未变化：{unchanged}。\n\n"
            "未覆盖历史文件，未访问Amazon，未调用Qwen，未排除问题字段，未发布正式版。\n\n"
            "历史问题比对按ASIN、问题代码和字段匹配；不再标记可能是规则变化，不等于源事实已经修复。\n\n"
            "## 当前问题类型\n\n" + "\n".join(f"- {k}: {v}" for k, v in summary["current_issue_types"].items()) + "\n")
    with (output / "summary.md").open("x", encoding="utf-8") as stream:
        stream.write(text)
    if not unchanged:
        raise RuntimeError("ORIGINAL_INPUT_DRIFT_DETECTED")
    stage("COMPLETE", **{k: summary[k] for k in ("replayed", "replay_unverified", "current_issue_count", "candidate_status")})


if __name__ == "__main__":
    main()
