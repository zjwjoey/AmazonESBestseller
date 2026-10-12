import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.orchestration import link_first
from amazon_es_bestseller.runtime_state import VersionedCheckpointStore

ENTRY = Path(__file__).resolve().parents[1] / "tools" / "bounded_link_details.py"
SPEC = importlib.util.spec_from_file_location("bounded_link_details", ENTRY)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
NORMAL = '<button id="nav-global-location-popover-link">Enviar a Madrid 28001</button>'


def input_fixture(tmp_path):
    origin, output = tmp_path / "origin", tmp_path / "output"
    origin.mkdir()
    (origin / "run_metadata.json").write_text("{}")
    segments = {}
    for n, group in enumerate(sorted(module.GROUPS)):
        html = "ranking_snapshots/%d/page.html" % n
        path = origin / html
        path.parent.mkdir(parents=True)
        path.write_text(NORMAL)
        manifest = {"research_category": group, "asin_extraction": False,
                    "pages": [{"html_file": html, "html_sha256": module.file_hash(path)}],
                    "records": [{"snapshot_item_id": str(n), "research_category": group,
                                 "product_link": "https://www.amazon.es/dp/B000CRBEJ2"}]}
        (path.parent / "manifest.json").write_text(json.dumps(manifest))
        segments[str(n)] = module.canonical_json_sha256(manifest)
    VersionedCheckpointStore(origin / "checkpoints", "link_first_state").save({
        "completed_segments": segments, "detail_results": {}})
    return origin, output


def test_frozen_batch_preserves_source_and_detects_html_drift(tmp_path):
    origin, output = input_fixture(tmp_path)
    before = module.file_hash(origin / "checkpoints" / "link_first_state.latest.json")
    frozen = module.freeze_inputs(origin, output, 2)
    assert frozen["snapshot_link_count"] == 2 and frozen["ranking_requests"] == 0
    assert before == module.file_hash(origin / "checkpoints" / "link_first_state.latest.json")
    assert module.freeze_inputs(origin, output, 2) == frozen
    (origin / "ranking_snapshots/0/page.html").write_text("changed")
    with pytest.raises(ValueError, match="SOURCE_RANKING_HTML_DRIFT"):
        module.freeze_inputs(origin, output, 2)


def test_frozen_scope_rejects_wrong_link_count(tmp_path):
    origin, output = input_fixture(tmp_path)
    with pytest.raises(ValueError, match="BOUNDED_LINK_COUNT"):
        module.freeze_inputs(origin, output, 980)


def fake_batch(tmp_path):
    batch = module.Batch.__new__(module.Batch)
    batch.output = tmp_path
    batch.ready = asyncio.Event()
    batch.ready.set()
    batch.dispatch_lock = asyncio.Lock()
    batch.allowed_links = {"https://www.amazon.es/dp/B000CRBEJ2"}
    batch.holds = {}
    batch.token = ""
    batch.guard = lambda: None
    batch.progress = lambda *a: None
    return batch


class Page:
    def __init__(self, status=200, html=NORMAL):
        self.status, self.html = status, html
        self.calls = []
        self.url = ""

    async def goto(self, url, **kwargs):
        self.calls.append(url)
        self.url = url
        return SimpleNamespace(status=self.status)

    async def content(self):
        return self.html


@pytest.mark.parametrize("status,html", [(403, "Forbidden"), (429, "Too many requests"), (200, "Robot Check")])
def test_access_restriction_stops_shared_dispatch_and_never_reopens(tmp_path, monkeypatch, status, html):
    async def scenario():
        batch = fake_batch(tmp_path)
        page = Page(status, html)
        original_gate = batch.gate
        async def gate():
            if not batch.ready.is_set():
                assert list(tmp_path.glob("attempt_*.html"))
                raise RuntimeError("human needed")
            await original_gate()
        monkeypatch.setattr(batch, "gate", gate)
        with pytest.raises(RuntimeError, match="human needed"):
            await batch.capture(page, "https://www.amazon.es/dp/B000CRBEJ2", tmp_path, 1)
        assert len(page.calls) == 1 and not batch.ready.is_set()
        other = Page()
        with pytest.raises(RuntimeError, match="human needed"):
            await batch.navigate(other, "https://www.amazon.es/dp/B000CRBEJ2")
        assert not other.calls
    asyncio.run(scenario())


def test_ordinary_failure_reopens_only_once(tmp_path, monkeypatch):
    async def scenario():
        batch = fake_batch(tmp_path)
        page = Page(503, "Service unavailable")
        async def no_sleep(*a):
            pass
        monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
        html, meta = await batch.capture(page, "https://www.amazon.es/dp/B000CRBEJ2", tmp_path, 1)
        assert html is None and meta["http_status"] == 503 and len(page.calls) == 2
    asyncio.run(scenario())


def test_parallel_dispatch_overlaps_three_navigation_tasks(tmp_path):
    async def scenario():
        batch = fake_batch(tmp_path)
        active, peak = 0, 0
        all_started = asyncio.Event()
        class Overlap(Page):
            async def goto(self, url, **kwargs):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                if active == 3:
                    all_started.set()
                await asyncio.wait_for(all_started.wait(), timeout=2)
                active -= 1
                return SimpleNamespace(status=200)
        await asyncio.gather(*(batch.navigate(Overlap(), "https://www.amazon.es/dp/B000CRBEJ2") for _ in range(3)))
        assert peak == 3
    asyncio.run(scenario())


def test_existing_detail_parser_preserves_identity_and_dedup(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text("{}")
    plan_path = tmp_path / "plan.json"
    category = {"research_category": "a", "target_unique": 2, "max_single_source_share": 1}
    plan_path.write_text(json.dumps({"target_unique": 2, "source_snapshot": str(source),
                                    "categories": [category], "scheduler": {"fallback_cooldown_seconds": 1800}}))
    monkeypatch.setattr(link_first, "validate_task_plan", lambda value, **_: value)
    parser = module.EvidenceParser(plan_path, tmp_path / "run", tmp_path, sample_size=2)
    fixture = (Path(__file__).parent / "fixtures" / "link_first_detail.html").read_text(encoding="utf-8")
    for n in range(2):
        relative = "detail_evidence/%d/attempt_001.html" % n
        html_path = parser.output / relative
        html_path.parent.mkdir(parents=True)
        html_path.write_text(fixture, encoding="utf-8")
        parser.feed = (fixture, {"requested_url": "https://www.amazon.es/dp/B000CRBEJ2",
                                "final_url": "https://www.amazon.es/dp/B000CRBEJ2", "observed_at": "now",
                                "html_file": relative, "html_sha256": module.file_hash(html_path)})
        link = {"snapshot_item_id": str(n), "research_category": "a", "product_link": parser.feed[1]["requested_url"],
                "ranking_source_url": "source", "category_trail": []}
        parser._detail(None, link, category)
    assert len(parser.records) == 1 and len(parser.state["detail_results"]) == 2
    assert parser.records["B000CRBEJ2"]["asin"] == "B000CRBEJ2"
