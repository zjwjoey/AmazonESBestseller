from datetime import datetime, timezone
from pathlib import Path

from amazon_es_bestseller.cli import run_reconnaissance
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
