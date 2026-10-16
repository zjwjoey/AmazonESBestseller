"""Build an explicit offline-replay envelope around a committed real HTML.

Not a new Amazon request, producer success, or Detail5500 acceptance. The
synthetic receipt is deliberately labelled; real HTML origin/hash is retained.
"""
from __future__ import annotations

import argparse
import gzip
from pathlib import Path

from amazon_es_bestseller.collection.detail_bridge5500 import parse_candidate_detail
from amazon_es_bestseller.multisource.storage import canonical_bytes, digest, immutable_bytes, immutable_json, sealed
from amazon_es_bestseller.source_audit.contracts import file_sha, read_json


def build(out):
    out = Path(out).resolve()
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/detail_bridge5500"
    provenance = read_json(fixture / "provenance.json")
    body = gzip.decompress((fixture / "actual_product.html.gz").read_bytes())
    if digest(body) != provenance["sha256"]:
        raise ValueError("REAL_FIXTURE_SHA_MISMATCH")
    url = provenance["metadata"]["final_url"]
    task = dict(candidate_id="historical_real_B0GK9PLT15", selection_id="offline_replay_only",
                task_id="historical_sample", run_id="source_audit_historical_replay",
                request_url=url, raw_product_url=url, normalized_product_url=url,
                source_snapshot_id="historical_saved_html_not_detail5500", source_memberships=[])
    immutable_json(out / "selected_5500.json", [{"candidate_id": task["candidate_id"],
        "selection_id": task["selection_id"], "absolute_product_url": url}])
    plan = {"tasks": [task], "selection_sha256": file_sha(out / "selected_5500.json")}
    immutable_json(out / "detail_plan_5500.json", plan)
    hp = out / "tasks/historical_sample/html/actual_product.html"
    immutable_bytes(hp, body)
    parsed = parse_candidate_detail(task, body.decode("utf8"), provenance["metadata"])
    if parsed["status"] != "SUCCESS":
        raise ValueError("REAL_HTML_REPLAY_FAILED")
    rp = out / "receipts/offline_replay_envelope.json"
    immutable_json(rp, sealed({"task_id": task["task_id"], "run_id": task["run_id"],
        "state": "NORMAL", "status_code": 200, "request_url": url, "final_url": url,
        "kind": "OFFLINE_REPLAY_SYNTHETIC_ENVELOPE_NOT_NETWORK_RECEIPT", "paid_requests": 0,
        "plan_sha256": digest(plan), "html_sha256": digest(body), "original_provenance": provenance}))
    immutable_json(out / "sample_provenance.json", {**provenance,
        "data_kind": "HISTORICAL_REAL", "real_saved_html_count": 1,
        "request_receipt_is_synthetic_replay_envelope": True, "formal_detail5500_validation": False})
    outcome = {**parsed, **{k: task[k] for k in ("task_id", "run_id", "candidate_id", "selection_id")},
               "html_path": str(hp), "html_sha256": digest(body), "receipt_path": str(rp), "receipt_sha256": file_sha(rp)}
    immutable_json(out / "tasks/historical_sample/checkpoint.json", sealed(outcome))
    immutable_bytes(out / "details_5500_raw.jsonl", canonical_bytes(parsed["record"]) + b"\n")
    return {"real_saved_html_count": 1, "amazon_requests": 0, "formal_detail5500_validation": False}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    print(json.dumps(build(parser.parse_args().out)))
