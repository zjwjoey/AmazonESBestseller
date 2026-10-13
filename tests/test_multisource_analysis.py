"""Offline assertions for evidence closure, time series and entity quotas."""
from copy import deepcopy
import json

import pytest

from amazon_es_bestseller.multisource.comparison import SnapshotComparator
from amazon_es_bestseller.multisource.budgets import BudgetLedger
from amazon_es_bestseller.multisource.contracts import RANK_SEMANTICS, SOURCE_ROOTS, timestamp
from amazon_es_bestseller.multisource.quality import SnapshotQualityGate, audit_snapshot, coverage_matrix
from amazon_es_bestseller.multisource.reports import SnapshotReportExporter
from amazon_es_bestseller.multisource.sampling import SamplingPlanner
from amazon_es_bestseller.multisource.storage import digest, immutable_json, sealed


def bundle(tmp_path, day="2026-10-01", ids=("p1", "p2"), *, source="BEST_SELLERS", ranks=None,
           origin="OFFLINE_FIXTURE", name=None, category="厨房用品"):
    name = name or day
    url = SOURCE_ROOTS[source] + "/kitchen/123"
    html = '<h1>Offline fixture</h1><div data-expected-count="' + str(len(ids)) + '">' + name + "</div>"
    path = tmp_path / (name + ".html")
    path.write_text(html, encoding="utf-8")
    checksum = digest(html.encode())
    page = {"source_id": "src_" + source, "source_type": source, "page_url": url,
            "page_number": 1, "research_category": category, "evidence_path": str(path),
            "evidence_sha256": checksum, "access_status": "NORMAL", "recognized_page_type": True,
            "rank_semantics": RANK_SEMANTICS[source], "expected_count": len(ids),
            "actual_count": len(ids), "category_navigation_valid": True,
            "expected_count_evidence": {"kind": "HTML_ATTRIBUTE", "locator": "[data-expected-count]",
                "raw": 'data-expected-count="' + str(len(ids)) + '"', "value": len(ids), "evidence_sha256": checksum}}
    instant = day + "T10:00:00Z"
    rows = [{"observation_id": name + "_" + str(index), "source_id": page["source_id"],
             "source_type": source, "research_category": category, "page_url": url,
             "rank_semantics": RANK_SEMANTICS[source], "slot_position": index,
             "rank_value": ranks[index] if ranks else index + 1, "rank_raw": "#" + str(index + 1),
             "raw_product_url": "/dp/B00000000" + str(index), "asin_from_card": "B00000000" + str(index),
             "evidence_path": str(path), "product_id": product_id,
             "identity_status": "CONFIRMED" if product_id else "UNCONFIRMED", "identity_conflict_reason": [],
             "observed_at": instant, "local_date": day} for index, product_id in enumerate(ids)]
    manifest = {"run_id": "run_" + name, "snapshot_id": "snapshot_" + name, "origin": origin,
                **timestamp(instant), "phase": "P0", "request_count": 0,
                "planned_pages": [{"source_id": page["source_id"], "page_url": url}]}
    return {"manifest": manifest, "pages": [page], "observations": rows}


def codes(result):
    return {row["code"] for row in result["findings"]}


def qa(value):
    return audit_snapshot(value["manifest"], value["pages"], value["observations"])


def test_complete_evidence_and_pending_p0_identity_counts(tmp_path):
    value = bundle(tmp_path, ids=("p1", None))
    result = qa(value)
    assert result["status"] == "COMPLETE" and result["gate_passed"]
    assert result["counts"]["confirmed_unique_products"] == 1
    assert result["counts"]["unconfirmed_observations"] == 1
    assert result["rates"]["evidence_traceability"] == 1
    assert result["stage"] == "P0"


@pytest.mark.parametrize("proof_mutation", ["missing", "no_hash", "wrong_hash", "locator_only", "wrong_value", "target_kind", "unrelated_raw"])
def test_completion_requires_metadata_count_proof(tmp_path, proof_mutation):
    value = bundle(tmp_path)
    page = value["pages"][0]
    proof = page["expected_count_evidence"]
    if proof_mutation == "missing":
        page.pop("expected_count_evidence")
    elif proof_mutation == "no_hash":
        proof.pop("evidence_sha256")
    elif proof_mutation == "wrong_hash":
        proof["evidence_sha256"] = "0" * 64
    elif proof_mutation == "locator_only":
        proof.pop("raw")
    elif proof_mutation == "wrong_value":
        proof["value"] = 50
    elif proof_mutation == "target_kind":
        proof["kind"] = "CONFIGURED_TARGET"
    else:
        proof["raw"] = "Offline fixture"
    result = qa(value)
    assert result["status"] == "UNVERIFIED" and not result["gate_passed"]


def test_recs_metadata_html_entities_are_proven(tmp_path):
    value = bundle(tmp_path)
    page = value["pages"][0]
    raw = '[{"asin":"B000000000"},{"asin":"B000000001"}]'
    html = '<div data-client-recs-list="' + raw.replace('"', '&quot;') + '"></div>'
    path = tmp_path / "recs.html"
    path.write_text(html, encoding="utf-8")
    page.update(evidence_path=str(path), evidence_sha256=digest(html.encode()),
                expected_count_evidence={"kind": "CLIENT_RECS_METADATA", "locator": "[data-client-recs-list]",
                                        "raw": raw, "value": 2, "evidence_sha256": digest(html.encode())})
    for row in value["observations"]:
        row["evidence_path"] = str(path)
    assert qa(value)["gate_passed"]
    page["expected_count"] = 1
    page["expected_count_evidence"]["value"] = 1
    assert not qa(value)["gate_passed"]


@pytest.mark.parametrize("mutation, expected", [("count", "EXPECTED_COUNT_MISMATCH"), ("slot", "DUPLICATE_PRODUCT_SLOT"),
    ("rank", "DUPLICATE_VISIBLE_RANK"), ("link", "PRODUCT_LINK_MISSING"), ("semantics", "RANK_SEMANTICS_MISMATCH"),
    ("acp", "ACP_INCOMPLETE"), ("nav", "CATEGORY_NAVIGATION_MISMATCH"), ("missed", "PARSER_MISSED_SLOTS")])
def test_page_errors_fail_gate(tmp_path, mutation, expected):
    value = bundle(tmp_path)
    page, rows = value["pages"][0], value["observations"]
    if mutation == "count":
        rows.pop()
    elif mutation == "slot":
        rows[1]["slot_position"] = 0
    elif mutation == "rank":
        rows[1]["rank_value"] = 1
    elif mutation == "link":
        rows[0]["raw_product_url"] = None
    elif mutation == "semantics":
        rows[0]["rank_semantics"] = "DETAIL_BSR"
    elif mutation == "acp":
        page.update(acp_required=True, acp_complete=False)
    elif mutation == "nav":
        page["category_navigation_valid"] = False
    else:
        page["rendered_count"] = 3
    result = qa(value)
    assert expected in codes(result) and not result["gate_passed"]


def test_continuity_only_applies_with_explicit_rank_range(tmp_path):
    value = bundle(tmp_path, source="MOST_GIFTED", ranks=(2, 8))
    assert qa(value)["gate_passed"]
    value["pages"][0].update(contiguous_ranks=True, expected_rank_start=1, expected_rank_end=2)
    assert "RANK_COVERAGE_GAP" in codes(qa(value))


@pytest.mark.parametrize("mutation", ["bytes", "missing", "extra_acp"])
def test_missing_and_corrupt_evidence_is_never_accepted(tmp_path, mutation):
    value = bundle(tmp_path)
    path = tmp_path / "2026-10-01.html"
    if mutation == "bytes":
        path.write_text("corrupted")
    elif mutation == "missing":
        path.unlink()
    else:
        value["pages"][0]["evidence_files"] = [{"path": str(tmp_path / "missing-acp.json"), "sha256": "0" * 64}]
    assert qa(value)["status"] == "FAILED"


def test_identity_conflict_even_if_incorrectly_marked_confirmed_is_blocked(tmp_path):
    value = bundle(tmp_path)
    value["observations"][0]["identity_conflict_reason"] = ["CARD_URL_ASIN_MISMATCH"]
    result = qa(value)
    assert not result["gate_passed"] and "CONFLICT_AUTO_ASSIGNED_PRODUCT" in codes(result)
    assert result["counts"]["confirmed_unique_products"] == 1


def test_restriction_stops_live_gate_and_records_enforcement(tmp_path):
    value = bundle(tmp_path, origin="LIVE_CAPTURE")
    value["pages"][0].update(http_status=429, access_status="RATE_LIMITED")
    result = qa(value)
    assert result["status"] == "BLOCKED" and "ACCESS_GATE_NOT_STOPPED" in codes(result)
    value["manifest"]["access_stop_enforced"] = True
    assert "ACCESS_GATE_NOT_STOPPED" not in codes(qa(value))


def test_same_slots_in_different_html_pages_fail_pagination(tmp_path):
    value = bundle(tmp_path)
    other = bundle(tmp_path, name="second_page")
    other["pages"][0]["page_url"] += "?pg=2"
    for row in other["observations"]:
        row["page_url"] = other["pages"][0]["page_url"]
    value["pages"] += other["pages"]
    value["observations"] += other["observations"]
    assert "DUPLICATE_PAGINATION_SLOTS" in codes(qa(value))


def test_matrix_has_all_combinations_without_unsupported_guess():
    rows = coverage_matrix([{"source_type": "BEST_SELLERS", "research_category": "厨房用品", "source_status": "AVAILABLE"},
                            {"source_type": "MOST_GIFTED", "research_category": "玩具", "source_status": "UNSUPPORTED"}])
    assert len(rows) == 75 and all(row["source_status"] == "UNVERIFIED" for row in rows)
    assert next(row for row in coverage_matrix([{"source_type": "MOST_GIFTED", "research_category": "玩具", "source_status": "BLOCKED"}])
                if row["source_type"] == "MOST_GIFTED" and row["research_category"] == "玩具")["source_status"] == "BLOCKED"


def test_coverage_matrix_preserves_grouped_registry_evidence_and_ids():
    coverage = {"combinations": [{"source_type": "BEST_SELLERS", "research_category": "厨房用品",
        "source_status": "AVAILABLE", "source_ids": ["src_a", "src_b"],
        "verification_evidence": [{"evidence_path": "saved-reviewed-page", "evidence_sha256": "known-hash"}]}]}
    row = next(item for item in coverage_matrix(coverage) if item["source_type"] == "BEST_SELLERS" and item["research_category"] == "厨房用品")
    assert row["source_status"] == "AVAILABLE" and row["source_ids"] == ["src_a", "src_b"]
    second = next(item for item in coverage_matrix(coverage_matrix(coverage)) if item["source_type"] == "BEST_SELLERS" and item["research_category"] == "厨房用品")
    assert second == row


def test_comparison_rank_new_exit_and_source_scope(tmp_path):
    left = bundle(tmp_path, ids=("p1", "p2"), ranks=(5, 6))
    right = bundle(tmp_path, day="2026-10-02", ids=("p1", "p3"), ranks=(2, 8))
    result = SnapshotComparator().compare(left, right, research_category="厨房用品")
    assert result["status"] == "COMPARABLE"
    assert result["new_products"] == ["p3"] and result["exited_products"] == ["p2"]
    assert result["rank_changes"][0]["rank_change"] == 3
    assert result["rank_changes"][0]["direction"] == "UP"


@pytest.mark.parametrize("mutation, reason", [("origin", "ORIGIN_MISMATCH"), ("date", "INSUFFICIENT_OBSERVATION_TIME"),
    ("coverage", "SOURCE_COVERAGE_MISMATCH"), ("partial", "SNAPSHOT_QA_INCOMPLETE"), ("timestamp", "SNAPSHOT_DATE_TIMESTAMP_MISMATCH")])
def test_unreliable_comparison_returns_not_comparable(tmp_path, mutation, reason):
    left, right = bundle(tmp_path), bundle(tmp_path, day="2026-10-02")
    if mutation == "origin":
        right["manifest"]["origin"] = "HISTORICAL_REPLAY"
    elif mutation == "date":
        right["manifest"].update(left["manifest"])
    elif mutation == "coverage":
        right["pages"][0]["page_number"] = 2
    elif mutation == "partial":
        right["pages"][0].pop("expected_count_evidence")
    else:
        right["manifest"]["local_date"] = "2026-10-03"
    result = SnapshotComparator().compare(left, right)
    assert result["status"] == "NOT_COMPARABLE" and reason in result["reasons"]


def test_unknown_identity_suppresses_definitive_absence(tmp_path):
    left, right = bundle(tmp_path), bundle(tmp_path, day="2026-10-02", ids=("p1", None))
    result = SnapshotComparator().compare(left, right)
    assert result["status"] == "PARTIALLY_COMPARABLE"
    assert result["new_products"] is None and result["exited_products"] is None


def test_history_duplicate_date_streak_reappearance_and_replay_provenance(tmp_path):
    snapshots = [bundle(tmp_path, day="2026-10-0" + str(day), ids=("p1",) if day != 3 else ("p2",)) for day in range(1, 5)]
    snapshots.append(bundle(tmp_path, day="2026-10-02", name="same_date", ids=("p1",)))
    result = SnapshotComparator().history(snapshots)
    assert result["collapsed_same_date_snapshots"] == 1
    product = next(item for item in result["entities"] if item["product_id"] == "p1")
    assert product["distinct_dates"] == 3 and product["observation_count"] == 3
    assert product["contexts"][0]["maximum_daily_streak"] == 2
    assert product["contexts"][0]["reappeared_dates"] == ["2026-10-04"]
    assert result["origins"]["OFFLINE_FIXTURE"]["real_live_days"] == 0


def test_period_comparison_requires_every_date_and_same_coverage(tmp_path):
    snapshots = [bundle(tmp_path, day="2026-10-0" + str(day)) for day in range(1, 5)]
    comparator = SnapshotComparator()
    assert comparator.compare_periods(snapshots, "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04")["status"] == "COMPARABLE"
    assert comparator.compare_periods(snapshots[1:], "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04")["status"] == "NOT_COMPARABLE"


def test_sampling_requires_confirmed_entities_and_global_single_allocation(tmp_path):
    left = bundle(tmp_path, ids=("p1", "p2", None))
    right = bundle(tmp_path, day="2026-10-02", source="MOST_GIFTED", ids=("p1", "p3"))
    rows = left["observations"] + right["observations"]
    report = SamplingPlanner().feasibility(rows, prior_product_ids={"p1"})
    assert report["confirmed_unique_candidates"] == 3 and report["allocated_unique_products"] == 3
    assert len({row["product_id"] for row in report["allocations"]}) == 3
    assert report["cross_source_products"] == 1 and report["insufficient_identity_observations"] == 1
    assert report["historical_existing_products"] == 1 and report["historical_first_discovered_products"] == 2
    assert report["global_shortfall"] == 5497
    assert report["source_quotas"]["BEST_SELLERS"]["shortfall"] >= 0


def test_sampling_ambiguous_category_never_guesses(tmp_path):
    rows = bundle(tmp_path, ids=("p1",))["observations"] + bundle(tmp_path, name="ambiguous", category="玩具", ids=("p1",))["observations"]
    report = SamplingPlanner().feasibility(rows)
    assert report["allocated_unique_products"] == 0 and report["excluded_category_ambiguous_products"] == 1
    assert report["historical_existing_ratio"] is None


def test_primary_category_requires_review_evidence_and_actual_source_pair(tmp_path):
    rows = bundle(tmp_path, ids=("p1",))["observations"] + bundle(tmp_path, name="ambiguous", category="玩具", source="MOST_GIFTED", ids=("p1",))["observations"]
    for row in rows:
        row["primary_research_category"] = "厨房用品"
    assert SamplingPlanner().feasibility(rows)["allocated_unique_products"] == 0
    for row in rows:
        row["primary_research_category_evidence"] = {"reviewed_by": "human", "evidence_path": "mapping-review"}
    report = SamplingPlanner().feasibility(rows)
    assert report["allocated_unique_products"] == 1
    assert report["allocations"] == [{"product_id": "p1", "source_type": "BEST_SELLERS", "research_category": "厨房用品"}]


def test_sampling_flow_respects_both_quotas_and_reassigns_flexible_source():
    # Source 2 is mandatory for the flexible product once source 1 fills up.
    rows = [{"product_id": "p" + str(i), "identity_status": "CONFIRMED", "source_type": "BEST_SELLERS", "research_category": "厨房用品"} for i in range(700)]
    rows += [{"product_id": "p" + str(i), "identity_status": "CONFIRMED", "source_type": "MOST_GIFTED", "research_category": "厨房用品"} for i in range(100)]
    report = SamplingPlanner().feasibility(rows)
    assert report["allocated_unique_products"] == 650
    assert report["category_quotas"]["厨房用品"]["shortfall"] == 0
    assert len({row["product_id"] for row in report["allocations"]}) == 650
    assert report["dynamic_reserve_unique_candidates"] == 50


def test_report_export_creates_immutable_revisions_and_clear_fixture_label(tmp_path):
    snapshot = bundle(tmp_path, ids=("p1", None))
    exporter = SnapshotReportExporter()
    report = exporter.build(snapshot, prior_product_ids={"p1"})
    output = exporter.export(report, tmp_path / "reports")
    assert json.loads((tmp_path / "reports" / report["report_id"] / "report.json").read_text(encoding="utf-8"))["origin"] == "OFFLINE_FIXTURE"
    assert not report["real_amazon_live_test"]["executed"]
    assert not report["real_amazon_live_test"]["fourteen_day_series_completed"]
    assert len(report["exceptions"]) == 1 and "已确认唯一商品：1" in exporter.text(report)
    assert exporter.export(report, tmp_path / "reports") == output
    changed = deepcopy(report)
    changed["origin"] = "HISTORICAL_REPLAY"
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_CONFLICT"):
        exporter.export(changed, tmp_path / "reports")
    next_report = exporter.build(snapshot)
    assert exporter.export(next_report, tmp_path / "reports")["directory"] != output["directory"]


@pytest.mark.parametrize("identifier", ["../overwrite", "C:\\outside", "report_../relative"])
def test_report_id_cannot_escape_output_directory(tmp_path, identifier):
    report = SnapshotReportExporter().build(bundle(tmp_path))
    report["report_id"] = identifier
    with pytest.raises(ValueError, match="INVALID_REPORT_ID"):
        SnapshotReportExporter().export(report, tmp_path / "reports")


def test_filtered_comparison_keeps_missing_planned_source_page(tmp_path):
    left, right = bundle(tmp_path), bundle(tmp_path, day="2026-10-02")
    right["manifest"]["planned_pages"].append({"source_id": "src_BEST_SELLERS", "page_url": right["pages"][0]["page_url"] + "?pg=2"})
    assert SnapshotComparator().compare(left, right, source_type="BEST_SELLERS")["status"] == "NOT_COMPARABLE"


def test_tracking_aliases_match_one_captured_and_planned_page(tmp_path):
    value = bundle(tmp_path)
    base = value["pages"][0]["page_url"]
    value["pages"][0]["page_url"] = base + "?ref=first"
    for row in value["observations"]:
        row["page_url"] = base + "?ref=second"
    value["manifest"]["planned_pages"] = [
        {"source_id": "src_BEST_SELLERS", "page_url": base + "?ref=first"},
        {"source_id": "src_BEST_SELLERS", "page_url": base + "?ref=second"}]
    result = qa(value)
    assert result["gate_passed"] and result["counts"]["observations"] == 2
    value["manifest"]["planned_pages"].append({"source_id": "src_BEST_SELLERS", "page_url": base + "?pg=2&ref=third"})
    assert not qa(value)["gate_passed"] and "PLANNED_PAGE_MISSING" in codes(qa(value))


def test_tracking_aliases_have_same_comparison_coverage(tmp_path):
    first, second = bundle(tmp_path), bundle(tmp_path, day="2026-10-02")
    for value, tracking in ((first, "first"), (second, "second")):
        value["pages"][0]["page_url"] += "?ref=" + tracking
        for row in value["observations"]:
            row["page_url"] = value["pages"][0]["page_url"]
    assert SnapshotComparator().compare(first, second)["status"] == "COMPARABLE"


def test_category_binding_and_unplanned_page_cannot_silently_pass(tmp_path):
    value = bundle(tmp_path)
    value["observations"][0]["research_category"] = "玩具"
    assert "OBSERVATION_CATEGORY_MISMATCH" in codes(qa(value))
    value["observations"][0]["research_category"] = "厨房用品"
    value["manifest"]["planned_pages"] = [{"source_id": "src_BEST_SELLERS", "page_url": value["pages"][0]["page_url"] + "?pg=2"}]
    assert "UNPLANNED_PAGE" in codes(qa(value))


def test_open_run_complete_pages_cannot_pass_final_stage_gate(tmp_path):
    value = bundle(tmp_path)
    value["manifest"]["frozen"] = False
    result = qa(value)
    assert result["pages"][0]["status"] == "COMPLETE"
    assert not result["gate_passed"] and "SNAPSHOT_NOT_FROZEN" in result["gate_block_reasons"]


def test_date_comparison_filters_unrelated_snapshot_sources(tmp_path):
    snapshots = [bundle(tmp_path), bundle(tmp_path, day="2026-10-02"),
                 bundle(tmp_path, name="unrelated", source="MOST_GIFTED")]
    assert SnapshotComparator().compare_dates(snapshots, "2026-10-01", "2026-10-02", source_type="BEST_SELLERS")["status"] == "COMPARABLE"


def test_proven_empty_page_establishes_absence_and_reappearance(tmp_path):
    snapshots = [bundle(tmp_path, ids=("p1",)), bundle(tmp_path, day="2026-10-02", ids=()),
                 bundle(tmp_path, day="2026-10-03", ids=("p1",))]
    history = SnapshotComparator().history(snapshots)
    assert history["entities"][0]["contexts"][0]["reappeared_dates"] == ["2026-10-03"]


def test_history_missing_date_does_not_prove_reappearance(tmp_path):
    snapshots = [bundle(tmp_path, ids=("p1",)), bundle(tmp_path, day="2026-10-02", ids=("p2",)),
                 bundle(tmp_path, day="2026-10-04", ids=("p1",))]
    history = SnapshotComparator().history(snapshots)
    product = next(row for row in history["entities"] if row["product_id"] == "p1")
    assert product["contexts"][0]["reappeared_dates"] == []


def test_saved_challenge_marker_cannot_be_hidden_by_normal_metadata(tmp_path):
    value = bundle(tmp_path)
    page = value["pages"][0]
    path = tmp_path / "2026-10-01.html"
    path.write_text(path.read_text(encoding="utf-8") + "<div>Robot Check</div>", encoding="utf-8")
    page["evidence_sha256"] = digest(path.read_bytes())
    page["expected_count_evidence"]["evidence_sha256"] = page["evidence_sha256"]
    assert qa(value)["status"] == "BLOCKED"


def test_fixture_relabelled_live_without_receipt_fails_provenance(tmp_path):
    value = bundle(tmp_path, origin="LIVE_CAPTURE")
    value["pages"][0].update(origin="LIVE_CAPTURE", observed_at=value["manifest"]["observed_at"])
    value["manifest"]["request_count"] = 1
    result = qa(value)
    assert result["status"] == "FAILED" and "LIVE_CAPTURE_PROVENANCE_INVALID" in codes(result)
    assert result["counts"]["proven_live_page_snapshots"] == 0
    assert not SnapshotReportExporter().build(value)["real_amazon_live_test"]["executed"]


@pytest.mark.parametrize("mutation", [None, "source", "timestamp", "body", "approval", "http_403", "response_url", "request_url"])
def test_offline_simulated_receipt_must_match_page_scope_and_evidence(tmp_path, mutation):
    value = bundle(tmp_path, origin="LIVE_CAPTURE")
    page = value["pages"][0]
    page.update(origin="LIVE_CAPTURE", observed_at=value["manifest"]["observed_at"])
    receipt = {"origin": "LIVE_CAPTURE", "kind": "PAGE", "source_id": page["source_id"],
               "page_url": page["page_url"], "access_status": "NORMAL", "approval_id": "OFFLINE_SIMULATION",
               "observed_at": page["observed_at"], "transport_evidence": {
                   "request": {"url": page["page_url"]}, "response": {
                   "url": page["page_url"], "status_code": 200,
                   "body": (tmp_path / "2026-10-01.html").read_text(encoding="utf-8")}}}
    if mutation == "source":
        receipt["source_id"] = "wrong"
    elif mutation == "timestamp":
        receipt["observed_at"] = "2026-10-02T10:00:00Z"
    elif mutation == "body":
        receipt["transport_evidence"]["response"]["body"] = "wrong body"
    elif mutation == "approval":
        receipt["approval_id"] = None
    elif mutation == "http_403":
        receipt["transport_evidence"]["response"]["status_code"] = 403
    elif mutation == "response_url":
        receipt["transport_evidence"]["response"]["url"] = "https://evil.example/gp/bestsellers/kitchen/123"
    elif mutation == "request_url":
        receipt["transport_evidence"]["request"]["url"] = SOURCE_ROOTS["MOST_GIFTED"] + "/toys/999"
    path = tmp_path / "simulated_receipt.json"
    immutable_json(path, sealed(receipt))
    page["capture_receipt_path"] = str(path)
    result = qa(value)
    assert result["gate_passed"] == (mutation is None)
    assert result["counts"]["proven_live_page_snapshots"] == (1 if mutation is None else 0)


@pytest.mark.parametrize("instant", [None, "2026-10-01T10:00:00", "invalid", "2026-10-02T10:00:00Z"])
def test_timestamp_integrity_gate(tmp_path, instant):
    value = bundle(tmp_path)
    value["observations"][0]["observed_at"] = instant
    assert "OBSERVATION_TIME_INVALID" in codes(qa(value))


def test_fourteen_fixture_days_never_become_fourteen_live_days(tmp_path):
    snapshots = [bundle(tmp_path, day="2026-10-" + str(day).zfill(2)) for day in range(1, 15)]
    report = SnapshotReportExporter().build(snapshots[-1], history=snapshots)
    assert len(report["history_report"]["origins"]["OFFLINE_FIXTURE"]["observed_dates"]) == 14
    assert report["real_amazon_live_test"] == {"executed": False, "real_observation_days": 0,
        "attempted_requests": 0, "verified_normal_pages": 0,
        "execution_definition": "CHECKED_SAVED_AUTHORIZED_TRANSPORT_ATTEMPT_NOT_COLLECTION_SUCCESS",
        "fourteen_day_series_completed": False, "note": "Offline fixture/replay success is not a real Amazon collection result."}


def test_report_distinguishes_current_snapshot_from_validated_history_pool(tmp_path):
    snapshots = [bundle(tmp_path, ids=("p1", "p2")), bundle(tmp_path, day="2026-10-02", ids=("p2", "p3")),
                 bundle(tmp_path, day="2026-10-03", ids=("p3", "p4"))]
    replay = bundle(tmp_path, day="2026-10-04", name="replay", ids=("r1",), origin="HISTORICAL_REPLAY")
    bad = bundle(tmp_path, day="2026-10-05", ids=("bad",))
    bad["pages"][0].pop("expected_count_evidence")
    report = SnapshotReportExporter().build(snapshots[-1], history=snapshots + [snapshots[-1], replay, bad],
        prior_product_ids={"p1", "p2"}, prior_scope="EARLIER_OBSERVATIONS_IN_SAME_ORIGIN_TEST_STORE")
    assert report["quota_feasibility"]["candidate_scope"] == "CURRENT_SNAPSHOT"
    assert report["quota_feasibility"]["confirmed_unique_candidates"] == 2
    pool = report["historical_candidate_pool_feasibility"]
    assert pool["origins"]["OFFLINE_FIXTURE"]["confirmed_unique_candidates"] == 4
    assert pool["origins"]["OFFLINE_FIXTURE"]["observation_count"] == 6
    assert pool["origins"]["HISTORICAL_REPLAY"]["confirmed_unique_candidates"] == 1
    assert pool["origins"]["HISTORICAL_REPLAY"]["prior_scope"] == "UNKNOWN_NO_PRIOR_SET"
    assert len(pool["excluded_snapshots"]) == 1
    result = SnapshotReportExporter().export(report, tmp_path / "pool_report")
    assert json.loads((tmp_path / "pool_report" / result["report_id"] / "quota_pool_feasibility.json").read_text(encoding="utf-8"))["origins"]["OFFLINE_FIXTURE"]["confirmed_unique_candidates"] == 4


def test_pooled_parse_revision_identity_conflict_is_not_two_confirmed_products(tmp_path):
    first = bundle(tmp_path, ids=("p1",))
    changed = deepcopy(first)
    changed["observations"][0]["product_id"] = "p2"
    report = SnapshotReportExporter().build(changed, history=[first, changed])
    pool = report["historical_candidate_pool_feasibility"]["origins"]["OFFLINE_FIXTURE"]
    assert pool["confirmed_unique_candidates"] == 0 and pool["insufficient_identity_observations"] == 1


def test_live_attempt_count_without_receipts_cannot_claim_actual_visit(tmp_path):
    value = bundle(tmp_path, origin="LIVE_CAPTURE")
    value["manifest"]["known_saved_live_request_attempts"] = 99
    report = SnapshotReportExporter().build(value)
    assert not report["real_amazon_live_test"]["executed"]
    assert report["real_amazon_live_test"]["attempted_requests"] == 0
    assert not report["quality_report"]["gate_passed"]


def test_blocked_live_transport_attempt_is_reported_without_normal_page_success(tmp_path):
    value = bundle(tmp_path, origin="LIVE_CAPTURE")
    page = value["pages"][0]
    page.update(origin="LIVE_CAPTURE", access_status="BLOCKED", http_status=403,
                observed_at=value["manifest"]["observed_at"])
    receipt = {"request_id": "req_OFFLINE_SIMULATION", "origin": "LIVE_CAPTURE", "kind": "PAGE",
        "source_id": page["source_id"], "page_url": page["page_url"], "access_status": "BLOCKED",
        "approval_id": "OFFLINE_SIMULATION", "observed_at": page["observed_at"],
        "transport_evidence": {"request": {"url": page["page_url"]}, "response": {
            "url": page["page_url"], "status_code": 403,
            "body": (tmp_path / "2026-10-01.html").read_text(encoding="utf-8")}}}
    path = tmp_path / "blocked_receipt.json"
    immutable_json(path, sealed(receipt))
    page["capture_receipt_path"] = str(path)
    value["manifest"].update(request_count=1, known_saved_live_request_attempts=1,
        live_request_receipts=[{"path": str(path), "sha256": digest(path.read_bytes())}])
    report = SnapshotReportExporter().build(value)
    assert report["real_amazon_live_test"]["executed"]
    assert report["real_amazon_live_test"]["attempted_requests"] == 1
    assert report["real_amazon_live_test"]["verified_normal_pages"] == 0
    assert not report["quality_report"]["gate_passed"]


def test_cumulative_phase_budgets_and_30000_limit_without_live_access(tmp_path):
    ledger = BudgetLedger(tmp_path / "budget")
    schedule = [("P0", "BEST_SELLERS", 1500), ("P1", "BEST_SELLERS", 3500),
                ("P2", "BEST_SELLERS", 4000), ("P2", "MOVERS_SHAKERS", 6000),
                ("P3", "MOVERS_SHAKERS", 1500), ("P3", "NEW_RELEASES", 6000),
                ("P3", "MOST_WISHED_FOR", 3750), ("P3", "MOST_GIFTED", 3750)]
    offset = 0
    for index, (phase, source, count) in enumerate(schedule):
        rows = [{"observation_id": "obs_" + str(offset + position), "source_type": source} for position in range(count)]
        assert ledger.accept("batch_" + str(index), rows, origin="OFFLINE_FIXTURE", phase=phase)
        assert not ledger.accept("batch_" + str(index), rows, origin="OFFLINE_FIXTURE", phase=phase)
        offset += count
        assert ledger.counters("OFFLINE_FIXTURE")["observation_count"] == offset
    counts = ledger.counters("OFFLINE_FIXTURE")
    assert counts["observation_count"] == 30000 and counts["request_count"] == 0
    assert counts["source_counts"] == {"BEST_SELLERS": 9000, "MOVERS_SHAKERS": 7500, "NEW_RELEASES": 6000,
                                       "MOST_WISHED_FOR": 3750, "MOST_GIFTED": 3750}
    with pytest.raises(ValueError, match="OBSERVATION_BUDGET_EXHAUSTED"):
        ledger.accept("overflow", [{"observation_id": "obs_extra", "source_type": "MOST_GIFTED"}], origin="OFFLINE_FIXTURE", phase="P3")
    assert ledger.counters("LIVE_CAPTURE")["observation_count"] == 0


def replacement_source(identifier, source="BEST_SELLERS", category="厨房用品", *, capacity=10, pages=1, status="AVAILABLE"):
    url = SOURCE_ROOTS[source] + "/kitchen/" + identifier
    return {"source_id": identifier, "source_type": source, "research_category": category,
            "actual_source_url": url, "source_status": status, "enabled": True,
            "verification_evidence": [{"kind": "REVIEWED_FIXTURE", "evidence_path": "offline-test"}],
            "reviewed_page_urls": [url + "?pg=" + str(index + 1) for index in range(pages)],
            "page_capacity": capacity, "snapshot_request_cost_per_page": 1}


def replacement_kwargs(sources, gap=30, **overrides):
    return {"missing_source_type": "BEST_SELLERS", "missing_research_category": "厨房用品", "gap": gap,
            "sources": sources, "completed_by_source": {}, "max_replacement_pages": 10,
            "max_replacement_requests": 10, "actor": "offline-review", "reason": "source shortfall",
            "review_evidence": {"path": "fixture-review"}, **overrides}


def test_replacements_obey_tiers_and_cross_source_approval():
    sources = [replacement_source("gift", "MOST_GIFTED"), replacement_source("other", category="玩具"), replacement_source("same")]
    plan = SamplingPlanner().replacement_plan(**replacement_kwargs(sources))
    assert [row["tier"] for row in plan["replacement_sources"]] == [1, 2]
    assert plan["remaining_gap"] == 10 and plan["status"] == "REPLACEMENT_SHORTFALL"
    assert any(row["reason"] == "CROSS_SOURCE_APPROVAL_REQUIRED" for row in plan["skipped_sources"])
    approved = SamplingPlanner().replacement_plan(**replacement_kwargs(sources, allow_cross_source=True))
    assert [row["tier"] for row in approved["replacement_sources"]] == [1, 2, 3]
    assert approved["replacement_quantity"] == 30 and approved["remaining_gap"] == 0


def test_replacement_global_source_page_and_request_limits():
    sources = [replacement_source("same", capacity=50, pages=3)]
    plan = SamplingPlanner().replacement_plan(**replacement_kwargs(sources, gap=200, completed_by_source={"BEST_SELLERS": 8995},
                                                                    max_replacement_pages=1, max_replacement_requests=1))
    assert plan["replacement_quantity"] == 5
    assert plan["replacement_sources"][0]["page_count"] == 1
    assert plan["remaining_source_budgets"]["BEST_SELLERS"] == 0
    global_plan = SamplingPlanner().replacement_plan(**replacement_kwargs(sources, gap=200, global_completed=29999))
    assert global_plan["replacement_quantity"] == 1 and global_plan["remaining_global_budget"] == 0


def test_reviewed_replacement_plan_is_accepted_by_durable_ledger(tmp_path):
    plan = SamplingPlanner().replacement_plan(**replacement_kwargs([replacement_source("same")], gap=10))
    ledger = BudgetLedger(tmp_path / "reviewed-budget")
    from amazon_es_bestseller.multisource.contracts import SOURCE_BUDGETS
    ledger.adjust(dict(SOURCE_BUDGETS), actor=plan["actor"], reason=plan["reason"],
                  replacement_sources=plan["replacement_sources"], approved=True)
    audit = ledger.counters("LIVE_CAPTURE")["adjustments"][-1]
    assert audit["replacement_sources"] == plan["replacement_sources"]
    assert ledger.counters("LIVE_CAPTURE")["observation_count"] == 0


@pytest.mark.parametrize("mutation", ["review", "unknown", "unavailable", "cross_category_url", "capacity"])
def test_replacement_never_queues_unreviewed_or_unknown_capacity(mutation):
    candidate = replacement_source("same")
    kwargs = replacement_kwargs([candidate])
    if mutation == "review":
        kwargs["review_evidence"] = None
        with pytest.raises(ValueError, match="REPLACEMENT_REVIEW_REQUIRED"):
            SamplingPlanner().replacement_plan(**kwargs)
        return
    if mutation == "unknown":
        candidate["source_status"] = "UNVERIFIED"
    elif mutation == "unavailable":
        candidate["enabled"] = False
    elif mutation == "cross_category_url":
        candidate["reviewed_page_urls"] = [SOURCE_ROOTS["BEST_SELLERS"] + "/toys/999"]
    else:
        candidate["page_capacity"] = None
    assert SamplingPlanner().replacement_plan(**kwargs)["replacement_quantity"] == 0
