from pathlib import Path

from amazon_es_bestseller.models import AccessState, ProbeEvent
from amazon_es_bestseller.run_store import RunStore


def test_run_store_creates_required_artifact_folders(tmp_path: Path):
    store = RunStore.create(tmp_path, "20260824_120000")
    for name in ("html", "screenshots", "raw", "failures", "parsed", "logs"):
        assert (store.root / name).is_dir()
    store.close()


def test_event_artifact_preserves_http_status_and_audit_log_fields(tmp_path: Path):
    store = RunStore.create(tmp_path, "event")
    store.record_event(
        ProbeEvent(
            requested_url="https://www.amazon.es/",
            final_url="https://www.amazon.es/",
            page_title="Amazon",
            timestamp="2026-08-24T00:00:00Z",
            load_duration=1.25,
            navigation_result="ok",
            access_state=AccessState.NORMAL,
            body_length=123,
            status=200,
        )
    )

    header = (store.root / "access_events.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    log = (store.logs_dir / "run.log").read_text(encoding="utf-8")
    assert "status" in header
    assert "status=200" in log
    assert "body_length=123" in log
    assert "duration=1.250" in log
    store.close()
