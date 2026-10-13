"""T0 benchmarks use saved bytes and generated fixtures; never perform HTTP."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import uuid

from .adapters import parse_page
from .performance import estimate_capacity, summarize_metrics
from .reports import export_report, eligible_candidate_ids


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _synthetic_pages(unique_count, observation_count, batch_size=1000):
    for offset in range(0, observation_count, batch_size):
        count = min(batch_size, observation_count - offset)
        cards = []
        for index in range(offset, offset + count):
            # Opaque fixture tokens, not ASIN generation or real source evidence.
            token = index % unique_count
            href = f"/product/offline-fixture-{token}?tag=benchmark-{index}"
            cards.append(f'<div data-ranking-card="true"><span class="zg-bdg-text">#{index + 1}</span>'
                         f'<a href="{href}">Offline synthetic candidate {token}</a></div>')
        yield '<h1>Los más vendidos</h1><div id="zg" data-expected-count="' + str(count) + '">' + ''.join(cards) + '</div>'


def _exercise(pages, directory, run_id, *, origin_label, target):
    from .store import RunStore
    from .quality import audit_run
    started_at = _now()
    started_clock = time.perf_counter()
    store = RunStore(directory / "store")
    plan = {"mode": "HIGH_SPEED_V2", "stage": "T0", "run_id": run_id,
            "origin": "OFFLINE_FIXTURE", "benchmark_origin": origin_label,
            "target_unique_links": target, "max_duration_hours": 10,
            "category_cooldown_seconds": 0, "request_pacing_seconds": 3,
            "max_requests_per_run": 10000, "max_pages_per_run": 10000,
            "max_requests_per_day": 10000, "max_raw_observations_per_run": 2000000,
            "history_observation_limit": 2000000, "global_access_gate": True,
            "detail_collection": False, "translation": False, "image_download": False,
            "action_filtering": False, "sources": [{"source_id": "benchmark", "source_type": "BEST_SELLERS"}]}
    store.begin_run(plan)
    events, page_count, product_page_count, source_types, source_ids = [], 0, 0, set(), set()
    for index, spec in enumerate(pages):
        source = spec.get("source") or {"source_id": "fixture", "source_type": "BEST_SELLERS",
            "research_category": "居家生活", "actual_source_url": "https://www.amazon.es/gp/bestsellers/fixture"}
        source_types.add(source["source_type"])
        source_ids.add(source["source_id"])
        page_url = spec.get("page_url") or source["actual_source_url"] + f"?pg={index+1}"
        before = time.perf_counter()
        html = spec.get("html")
        if html is None:
            html = Path(spec["html_path"]).read_text(encoding="utf-8")
        evidence = store.save_html(html)
        events.append({"stage": "html_read_and_save", "duration_seconds": time.perf_counter() - before,
                       "source_type": source["source_type"], "research_category": source.get("research_category")})
        before = time.perf_counter()
        result = parse_page(html, source=source, run_id=run_id, page_url=page_url,
            page_number=spec.get("page_number", index + 1), observed_at=spec.get("observed_at") or started_at,
            evidence_path=evidence["evidence_path"], origin="OFFLINE_FIXTURE",
            acp_responses=spec.get("acp_responses"), http_status=spec.get("http_status", 200))
        events.append({"stage": "parse", "duration_seconds": time.perf_counter() - before,
                       "source_type": source["source_type"], "research_category": source.get("research_category")})
        before = time.perf_counter()
        page_key = hashlib.sha256((source["source_id"] + "\0" + page_url).encode()).hexdigest()
        store.commit_page(run_id, page_key, {**result["page"], "observation_count": len(result["observations"])}, result["observations"])
        events.append({"stage": "dedup_and_persist", "duration_seconds": time.perf_counter() - before,
                       "source_type": source["source_type"], "research_category": source.get("research_category")})
        page_count += 1
        product_page_count += bool(result["observations"])
    before = time.perf_counter()
    # Restart against the actual store, then reparse actual evidence as strong QA.
    reopened = RunStore(directory / "store")
    observations = list(reopened.iter_observations(run_id))
    candidates = list(reopened.iter_candidates(run_id))
    normalized = [c["normalized_product_url"] for c in candidates]
    errors = []
    if len(normalized) != len(set(normalized)):
        errors.append("DUPLICATE_NORMALIZED_CANDIDATE_KEYS")
    if any(not Path(row["evidence_path"]).is_file() for row in observations):
        errors.append("MISSING_OBSERVATION_EVIDENCE")
    strong_audit = audit_run(reopened, run_id)
    errors.extend(f["code"] for f in strong_audit["findings"] if f["severity"] == "ERROR")
    reopened.close()
    events.append({"stage": "restart_and_basic_qa", "duration_seconds": time.perf_counter() - before})
    store.update_run(run_id, started_at=started_at, ended_at=_now(), status="FINISHED", qa=strong_audit)
    checkpoint_start = time.perf_counter()
    checkpoint_result = list(store.db.execute("PRAGMA wal_checkpoint(FULL)").fetchone())
    if checkpoint_result[0] or checkpoint_result[1] != checkpoint_result[2]:
        raise ValueError("FINAL_FULL_CHECKPOINT_NOT_COMPLETE")
    events.append({"stage": "sqlite_final_full_checkpoint", "duration_seconds": time.perf_counter() - checkpoint_start})
    finished_at = _now()
    metric = summarize_metrics(events, raw_count=len(observations),
        valid_link_count=sum(bool(r.get("candidate_id")) for r in observations),
        unique_count=len(eligible_candidate_ids(candidates)), started_at=started_at, finished_at=finished_at, target=target)
    metric.update(measured_monotonic_seconds=time.perf_counter() - started_clock,
                  benchmark_origin=origin_label, live_requests=0, product_pages=product_page_count, page_count=page_count,
                  source_count=len(source_ids),
                  final_full_checkpoint_result=checkpoint_result,
                  independent_sku_identity_verified=False, qa_errors=errors,
                  include_report_export_in_task_wall=True,
                  source_types_observed=sorted(source_types),
                  ten_hour_live_target_passed=False)
    metric["ten_hour_target_passed"] = metric["ten_hour_target_passed"] and strong_audit["gate_passed"]
    report = export_report(store, run_id, directory / "reports", metrics=metric)
    close_started = time.perf_counter()
    store.close()
    close_duration = time.perf_counter() - close_started
    metric = json.loads(Path(report["report_directory"]).joinpath("performance.json").read_text(encoding="utf-8"))
    metric["sqlite_final_close_seconds"] = close_duration
    metric["measured_monotonic_seconds"] = time.perf_counter() - started_clock
    metric["total_including_report_manifest_seconds"] = metric["measured_monotonic_seconds"]
    metric["end_to_end_scope"] = "FIRST_CASE_START_THROUGH_EXPORT_MANIFEST_AND_BOTH_DATABASE_CONNECTIONS_CLOSED"
    metric["data_export_and_manifest_finished_at"] = _now()
    return {"run_id": run_id, "metrics": metric, "report": report, "restart_verified": not errors,
            "strong_audit": strong_audit,
            "capacity_model": estimate_capacity(metric), "store_root": str(directory / "store")}


def run_benchmark(pages, output_root, synthetic_unique=30000, synthetic_observations=100000):
    if synthetic_unique < 1 or synthetic_observations < synthetic_unique:
        raise ValueError("SYNTHETIC_COUNTS_INVALID")
    pages = list(pages)
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in Path(__file__).parent.glob("*.py")}
    from ..multisource.storage import read_sealed
    provenance = []
    for spec in pages:
        receipt_path = spec.get("capture_receipt_path")
        proven = False
        if receipt_path:
            try:
                receipt = read_sealed(receipt_path)
                response = receipt["transport_evidence"]["response"]
                content = spec.get("html")
                if content is None:
                    content = Path(spec["html_path"]).read_text(encoding="utf-8")
                proven = bool(receipt.get("origin") == "LIVE_CAPTURE" and receipt.get("kind") == "PAGE"
                    and receipt.get("access_status") == "NORMAL" and response.get("status_code") == 200
                    and receipt.get("page_url") == spec.get("page_url")
                    and hashlib.sha256(content.encode()).hexdigest() == hashlib.sha256(response.get("body", "").encode()).hexdigest())
            except (ValueError, OSError, KeyError, TypeError):
                proven = False
        provenance.append({"page_url": spec.get("page_url"), "capture_receipt_path": receipt_path,
                           "real_capture_proof_verified": proven})
    origin_label = "SAVED_REAL_HTML_OFFLINE_REPLAY" if pages and all(p["real_capture_proof_verified"] for p in provenance) else "SAVED_HTML_OFFLINE_REPLAY_PROVENANCE_UNVERIFIED"
    directory = Path(output_root) / ("benchmark_" + uuid.uuid4().hex)
    directory.mkdir(parents=True, exist_ok=False)
    real = _exercise(pages, directory / "saved_real", "t0_saved_real", origin_label=origin_label, target=30000)
    synthetic = _exercise(({"html": html} for html in _synthetic_pages(synthetic_unique, synthetic_observations)),
        directory / "synthetic", "t0_synthetic", origin_label="GENERATED_OFFLINE_FIXTURE", target=synthetic_unique)
    result = {"stage": "T0", "live_requests": 0, "saved_real": real, "synthetic": synthetic,
              "saved_input_provenance": provenance,
              "working_tree_source_sha256_at_start": source_hashes,
              "live_t3_completed": False, "ten_hour_live_feasibility": "CANNOT_CONFIRM_WITHOUT_LIVE_CAPACITY_AND_TIMINGS",
              "synthetic_target_verified": synthetic["metrics"]["unique_link_candidates"] == synthetic_unique
                    and synthetic["metrics"]["raw_observations"] == synthetic_observations
                    and synthetic["restart_verified"], "output_directory": str(directory)}
    final_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in Path(__file__).parent.glob("*.py")}
    result["working_tree_source_sha256_at_finish"] = final_hashes
    result["source_files_changed_during_benchmark"] = sorted(name for name in set(source_hashes) | set(final_hashes)
                                                            if source_hashes.get(name) != final_hashes.get(name))
    (directory / "benchmark.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
