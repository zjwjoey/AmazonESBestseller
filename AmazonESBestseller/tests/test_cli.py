from datetime import datetime, timezone
from pathlib import Path

from amazon_es_bestseller.cli import _summary_from_records, choose_decision, format_tested_pages, run_reconnaissance
from amazon_es_bestseller.models import AccessState, ProbeEvent
from amazon_es_bestseller.reports import write_report


def blocked_probe(store, targets, delay_seconds=0):
    event = ProbeEvent(
        requested_url=targets[0],
        final_url=targets[0],
        page_title="Robot Check",
        timestamp=datetime.now(timezone.utc).isoformat(),
        load_duration=0.1,
        navigation_result="ok",
        access_state=AccessState.BLOCKED,
        body_length=12,
        reason="marker: robot check",
    )
    store.record_event(event)
    return [event]


def test_cli_stops_before_category_pages_when_root_probe_is_blocked(tmp_path: Path):
    result = run_reconnaissance(tmp_path, probe=blocked_probe)
    assert result.visited_page_count == 1
    assert result.decision in {"NO-GO", "CONDITIONAL GO"}


def test_report_contains_required_final_sections(tmp_path: Path):
    report = write_report(tmp_path, {"decision": "CONDITIONAL GO"})
    content = report.read_text(encoding="utf-8")
    assert "## 11. ASIN提取成功率" in content
    assert "## 30. 是否建议进入正式开发" in content


def test_detail_page_block_changes_go_to_conditional_go():
    normal = ProbeEvent(
        requested_url="https://www.amazon.es/gp/bestsellers/kitchen",
        final_url="https://www.amazon.es/gp/bestsellers/kitchen",
        page_title="Kitchen",
        timestamp="2026-08-24T00:00:00Z",
        load_duration=0.1,
        navigation_result="ok",
        access_state=AccessState.NORMAL,
        body_length=100,
    )
    blocked = ProbeEvent(
        requested_url="https://www.amazon.es/dp/B012345678",
        final_url="https://www.amazon.es/dp/B012345678",
        page_title="Robot Check",
        timestamp="2026-08-24T00:00:00Z",
        load_duration=0.1,
        navigation_result="ok",
        access_state=AccessState.CHALLENGE,
        body_length=100,
        reason="marker: robot check",
    )
    assert choose_decision([normal], [normal], [blocked], records=[]) == "CONDITIONAL GO"


def test_summary_classifies_field_availability_for_report():
    from amazon_es_bestseller.models import RankingRecord

    summary = _summary_from_records([RankingRecord(asin="B012345678", title="Sample")])
    assert "asin" in summary["stable_fields"]
    assert "price" in summary["unavailable_fields"]


def test_report_page_list_includes_all_accessed_event_urls():
    events = [
        ProbeEvent(
            requested_url="https://example.test/root",
            final_url="https://example.test/root",
            page_title="Root",
            timestamp="2026-08-24T00:00:00Z",
            load_duration=0.1,
            navigation_result="ok",
            access_state=AccessState.NORMAL,
            body_length=1,
        ),
        ProbeEvent(
            requested_url="https://example.test/category",
            final_url="https://example.test/category",
            page_title="Category",
            timestamp="2026-08-24T00:00:00Z",
            load_duration=0.1,
            navigation_result="ok",
            access_state=AccessState.NORMAL,
            body_length=1,
        ),
    ]
    assert format_tested_pages(events) == "https://example.test/root, https://example.test/category"
