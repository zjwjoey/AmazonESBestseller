import csv
from datetime import datetime, timezone
from pathlib import Path

from amazon_es_bestseller.category_discovery import CategoryNode
from amazon_es_bestseller.cli import (
    _call_probe,
    _write_detail_field_report,
    parse_discovery_page,
    _summary_from_records,
    choose_decision,
    format_tested_pages,
    parse_root_sample,
    run_reconnaissance,
    select_detail_records,
    select_trial_categories,
)
from amazon_es_bestseller.models import AccessState, ProbeEvent
from amazon_es_bestseller.reports import write_report
from amazon_es_bestseller.run_store import RunStore


def blocked_probe(store, targets, delay_seconds=0, start_index=1):
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
    assert choose_decision([normal] * 3, [normal] * 3, [blocked], records=[]) == "CONDITIONAL GO"


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


def test_call_probe_does_not_retry_internal_type_error():
    calls = []

    def broken_probe(*args, **kwargs):
        calls.append(kwargs)
        raise TypeError("internal parser bug")

    try:
        _call_probe(broken_probe, object(), ["https://example.test"], 3, start_index=4)
    except TypeError:
        pass
    else:
        raise AssertionError("internal TypeError should propagate")

    assert calls == [{"start_index": 4}]


def test_category_trial_keeps_full_discovery_result():
    nodes = [
        CategoryNode(f"category-{index}", f"https://example.test/{index}", str(index), "Hogar y cocina", 2, "source")
        for index in range(5)
    ]

    all_nodes, trial_nodes = select_trial_categories(nodes, 3)

    assert len(all_nodes) == 5
    assert len(trial_nodes) == 3


def test_detail_sampling_round_robins_across_leaf_categories():
    from amazon_es_bestseller.models import RankingRecord

    records = [
        RankingRecord(asin="B000000001", product_url="https://www.amazon.es/dp/B000000001", leaf_category="Cocina"),
        RankingRecord(asin="B000000002", product_url="https://www.amazon.es/dp/B000000002", leaf_category="Cocina"),
        RankingRecord(asin="B000000003", product_url="https://www.amazon.es/dp/B000000003", leaf_category="Baño"),
        RankingRecord(asin="B000000004", product_url="https://www.amazon.es/dp/B000000004", leaf_category="Limpieza"),
    ]

    selected = select_detail_records(records, max_detail_samples=3)

    assert [record.asin for record in selected] == ["B000000001", "B000000003", "B000000004"]


def test_discovery_page_with_children_does_not_emit_parent_as_leaf_ranking():
    node = CategoryNode(
        "Baño", "https://www.amazon.es/gp/bestsellers/kitchen/1", "1",
        "Hogar y cocina", 2, "root",
    )
    html = """
    <nav id="category-nav"><a href="/gp/bestsellers/kitchen/2">Accesorios de baño</a></nav>
    <div data-asin="B012345678"><span class="rank">#1</span><a href="/dp/B012345678">Producto</a></div>
    """

    children, records = parse_discovery_page(html, node)

    assert [child.category_name_es for child in children] == ["Accesorios de baño"]
    assert records == []


def test_root_sample_parser_preserves_root_category_context():
    fixture = Path("tests/fixtures/kitchen_sample.html").read_text(encoding="utf-8")

    records = parse_root_sample(
        fixture,
        "https://www.amazon.es/gp/bestsellers/kitchen",
        max_products=2,
    )

    assert len(records) == 2
    assert all(record.level2_category_es is None for record in records)
    assert all(record.root_category_es == "Hogar y cocina" for record in records)


def test_choose_decision_requires_complete_root_and_multi_category_evidence():
    normal = ProbeEvent(
        requested_url="https://example.test/page",
        final_url="https://example.test/page",
        page_title="Normal",
        timestamp="2026-08-24T00:00:00Z",
        load_duration=0.1,
        navigation_result="ok",
        access_state=AccessState.NORMAL,
        body_length=100,
    )
    from amazon_es_bestseller.models import RankingRecord

    decision = choose_decision(
        [normal],
        [normal],
        [],
        [RankingRecord(asin="B012345678", rank=None)],
    )

    assert decision == "NO-GO"


def test_choose_decision_does_not_go_without_records_from_each_category():
    def normal_event(url: str) -> ProbeEvent:
        return ProbeEvent(
            requested_url=url,
            final_url=url,
            page_title="Normal",
            timestamp="2026-08-24T00:00:00Z",
            load_duration=0.1,
            navigation_result="ok",
            access_state=AccessState.NORMAL,
            body_length=100,
        )

    from amazon_es_bestseller.models import RankingRecord

    root_events = [
        normal_event("https://www.amazon.es/"),
        normal_event("https://www.amazon.es/gp/bestsellers"),
        normal_event("https://www.amazon.es/gp/bestsellers/kitchen"),
    ]
    category_events = [
        normal_event(f"https://www.amazon.es/gp/bestsellers/kitchen/{node}")
        for node in ("1", "2", "3")
    ]
    root_only_records = [RankingRecord(asin="B012345678", rank=1)]

    assert choose_decision(root_events, category_events, [], root_only_records) == "CONDITIONAL GO"


def test_choose_decision_does_not_count_url_variants_as_distinct_categories():
    def normal_event(url: str) -> ProbeEvent:
        return ProbeEvent(
            requested_url=url,
            final_url=url,
            page_title="Normal",
            timestamp="2026-08-24T00:00:00Z",
            load_duration=0.1,
            navigation_result="ok",
            access_state=AccessState.NORMAL,
            body_length=100,
        )

    from amazon_es_bestseller.models import RankingRecord

    root_events = [
        normal_event("https://www.amazon.es/"),
        normal_event("https://www.amazon.es/gp/bestsellers"),
        normal_event("https://www.amazon.es/gp/bestsellers/kitchen"),
    ]
    category_urls = [
        f"https://www.amazon.es/gp/bestsellers/kitchen/{index}?node=77&ref={index}"
        for index in (1, 2, 3)
    ]
    category_events = [normal_event(url) for url in category_urls]
    records = [
        RankingRecord(asin=f"B{index:09d}", rank=1, source_url=url)
        for index, url in enumerate(category_urls)
    ]

    assert choose_decision(root_events, category_events, [], records) == "CONDITIONAL GO"


def test_detail_field_report_excludes_blocked_event(tmp_path: Path):
    store = RunStore.create(tmp_path, "detail-report")

    def event(url: str, state: AccessState) -> ProbeEvent:
        return ProbeEvent(
            requested_url=url,
            final_url=url,
            page_title="Detail",
            timestamp="2026-08-24T00:00:00Z",
            load_duration=0.1,
            navigation_result="ok",
            access_state=state,
            body_length=100,
        )

    normal = event("https://www.amazon.es/dp/B012345678", AccessState.NORMAL)
    blocked = event("https://www.amazon.es/dp/B012345679", AccessState.CHALLENGE)
    store.save_html("page_07", '<span id="productTitle">Normal product</span>')
    store.save_html("page_08", '<span id="productTitle">Challenge page</span>')

    _write_detail_field_report(store, [normal, blocked], start_index=7)

    with (store.root / "detail_field_availability.csv").open(encoding="utf-8-sig") as handle:
        title_row = next(row for row in csv.DictReader(handle) if row["field"] == "title")
    assert title_row["samples"] == "1"
    assert title_row["present"] == "1"
    store.close()
