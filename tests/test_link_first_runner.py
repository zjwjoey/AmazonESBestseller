import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amazon_es_bestseller.orchestration import link_first as module


FIXTURE = Path(__file__).with_name("fixtures").joinpath("link_first_detail.html").read_text(encoding="utf-8")


def test_detail_entry_reuses_spanish_price_display_and_category_contract(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    link = {"snapshot_item_id": "contract", "product_link": "https://www.amazon.es/dp/B000CRBEJ2",
            "research_category": "a", "ranking_source_url": "https://www.amazon.es/gp/bestsellers/tools/123",
            "ranking_page_number": 1, "bestseller_rank": 1, "category_trail": ["Hogar", "Muebles", "Sillas"]}
    html = FIXTURE.replace('</body>', '''
    <div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">34,76€</span></span>
    <span class="a-text-price" data-a-strike="true"><span class="a-offscreen">19,76€</span></span></div>
    <div id="wayfinding-breadcrumbs_feature_div"><a>Hogar</a><a>Muebles</a><a>Sillas</a></div>
    <div id="detailBullets_feature_div"><ul><li>Dimensiones del producto: 20 x 10 x 5 cm</li></ul></div>
    </body>''')
    run._detail(Session([(200, html)]), link, run.plan['categories'][0])
    row = run.records['B000CRBEJ2']
    assert row['original_price_raw'] == '19,76€'
    assert row['original_price'] is None and row['discount_rate'] is None
    assert 'ASIN:' not in row['product_details_es']
    assert 'Atributo nuevo: Valor desconocido' in row['product_details_es']
    assert row['category_provenance']['source'] == 'product_breadcrumb'
    assert row['category_provenance']['evidence_binding']['html_sha256'] == row['html_sha256']


def runner(tmp_path, monkeypatch, **kwargs):
    snapshot = tmp_path / "source.json"
    snapshot.write_text("{}")
    plan = {"target_unique": 2, "source_snapshot": str(snapshot), "categories": [
        {"research_category": "a", "target_unique": 2, "max_single_source_share": 1,
         "rank_start": 1, "rank_end": 80, "pages_per_url": 2,
         "sources": [{"source_url": "https://www.amazon.es/gp/bestsellers/tools/123", "role": "primary", "pages_per_url": 2}]}],
        "scheduler": {"fallback_cooldown_seconds": 1800}}
    plan_path = tmp_path / "plan.json"
    if not plan_path.exists():
        plan_path.write_text(json.dumps(plan))
    monkeypatch.setattr(module, "validate_task_plan", lambda value, **_: value)
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "code")
    result = module.LinkFirstRunner(plan_path, tmp_path / "run", tmp_path, sample_size=2, **kwargs)
    def unexpected_hold(kind, error):
        raise AssertionError("Unexpected hold in offline test: " + kind + ": " + error)
    monkeypatch.setattr(result, "_manual_hold", unexpected_hold)
    return result


class Session:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.page = SimpleNamespace(content=lambda: self.pages[min(len(self.calls) - 1, len(self.pages) - 1)][1], url="https://www.amazon.es/dp/B000CRBEJ2")

    def goto(self, url):
        self.calls.append(url)
        self.page.url = url
        return self.pages[min(len(self.calls) - 1, len(self.pages) - 1)][0]

    def wait_for_product_page(self):
        pass

    def wait_for_price_text(self):
        pass

    def wait_between_requests(self):
        pass


@pytest.mark.parametrize("status,html", [(403, "Forbidden"), (429, "Too many requests"), (200, "Robot Check"), (200, "access denied"), (200, "CAPTCHA")])
def test_access_stop_never_retries_or_closes_session(tmp_path, monkeypatch, status, html):
    run = runner(tmp_path, monkeypatch)
    session = Session([(status, html)])
    def hold(kind, reason):
        assert kind == "ACCESS_STOP"
        assert len(session.calls) == 1
        assert list((tmp_path / "run" / "evidence").glob("*.html"))
        raise RuntimeError("human needed")
    monkeypatch.setattr(run, "_manual_hold", hold)
    with pytest.raises(RuntimeError, match="human needed"):
        run._capture(session, "https://www.amazon.es/dp/B000CRBEJ2", tmp_path / "run" / "evidence")
    assert len(session.calls) == 1


def test_normal_failure_reopens_at_most_once(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    session = Session([(503, "Temporarily unavailable")])
    html, meta = run._capture(session, "https://www.amazon.es/dp/B000CRBEJ2", tmp_path / "run" / "evidence")
    assert html is None and meta["http_status"] == 503
    assert len(session.calls) == 2


def test_resume_does_not_refetch_success_or_change_snapshot(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    category = run.plan["categories"][0]
    session = Session([(200, '<div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/dp/B000CRBEJ2">Product</a></div>')])
    session.load_lazy_ranking_content = lambda: None
    run._snapshot(session, category, "primary")
    assert len(session.calls) == 2
    session.pages = [(200, FIXTURE)]
    run._detail(session, run.links[0], category)
    assert len(run.records) == len(run.selected) == 1
    resumed = runner(tmp_path, monkeypatch, resume=True)
    before = len(session.calls)
    resumed._snapshot(session, category, "primary")
    resumed._detail(session, resumed.links[0], category)
    assert len(session.calls) == before
    assert all(not any("asin" in key for key in item) for item in resumed.links)


def test_manifest_drift_rejected_before_browser(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    category = run.plan["categories"][0]
    session = Session([(200, '<div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/dp/B000CRBEJ2">Product</a></div>')])
    session.load_lazy_ranking_content = lambda: None
    run._snapshot(session, category, "primary")
    manifest = next((tmp_path / "run" / "ranking_snapshots").glob("*/manifest.json"))
    manifest.write_text("{}")
    with pytest.raises(ValueError, match="SNAPSHOT_MANIFEST_DRIFT"):
        runner(tmp_path, monkeypatch, resume=True)


def test_plan_or_code_drift_before_requests(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "changed")
    with pytest.raises(ValueError, match="RUN_CODE_DRIFT"):
        run.guards()


def test_global_detail_asin_dedup_retains_ranking_contexts(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    category = run.plan["categories"][0]
    session = Session([(200, '<div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/dp/B000CRBEJ2">Product</a></div>')])
    session.load_lazy_ranking_content = lambda: None
    run._snapshot(session, category, "primary")
    session.pages = [(200, FIXTURE)]
    for item in run.links:
        run._detail(session, item, category)
    assert len(run.selected) == len(run.records) == 1
    report = run._final()
    assert report["status"] == "QUOTA_UNIQUE_SHORTFALL"
    assert len(json.loads((run.output / "resolved_ranking_contexts.json").read_text(encoding="utf-8"))) == 2
    assert not report["formal_release"]


def test_cooldown_deadline_is_persisted(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    ticks = [100.0]
    run.now = lambda: ticks[0]
    def advance(n):
        assert run.state["status"] == "COOLDOWN"
        assert run.state["cooldown_until"] == 1900
        ticks[0] += n
    run.sleep = advance
    run._category("a")
    run._category("b")
    assert ticks[0] == 1900 and run.state["last_category"] == "b"


def test_delivery_ack_does_not_exit_when_header_is_still_wrong(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    wrong = '<button id="nav-global-location-popover-link">Enviar a Estados Unidos</button>'
    correct = '<button id="nav-global-location-popover-link">Enviar a Madrid 28001</button>'
    session = Session([(200, wrong)])
    holds = []
    def hold(kind, reason):
        holds.append(kind)
        assert list((run.output / "preflight").glob("delivery_check_*.html"))
        if len(holds) == 2:
            session.page.content = lambda: correct
    monkeypatch.setattr(run, "_manual_hold", hold)
    run.sleep = lambda _: None
    assert run._verify_delivery_location(session, wrong).is_spain is True
    assert holds == ["DELIVERY_LOCATION_HOLD", "DELIVERY_LOCATION_HOLD"]
    assert session.calls == []


def test_delivery_check_detects_challenge_before_location(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    correct = '<button id="nav-global-location-popover-link">Enviar a España</button>'
    session = Session([(200, correct)])
    monkeypatch.setattr(run, "_access_clear_by_human", lambda session, error: correct)
    assert run._verify_delivery_location(session, "Robot Check").is_spain is True
    assert session.calls == []


def test_preflight_migration_preserves_metadata_and_resumes_only_exact_new_code(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    run.save("ERROR", error="SPAIN_DELIVERY_NOT_VERIFIED")
    before = (run.output / "run_metadata.json").read_bytes()
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "fixed-code")
    resumed = module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2,
                                     resume=True, migrate_delivery_preflight=True)
    resumed.guards()
    assert (run.output / "run_metadata.json").read_bytes() == before
    assert (run.output / "code_migrations" / "delivery_preflight_v1.json").exists()
    resumed.save("RUNNING")
    module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2, resume=True).guards()
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "another-change")
    with pytest.raises(ValueError, match="INVALID_PREFLIGHT_CODE_MIGRATION"):
        module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2, resume=True)


def test_preflight_migration_rejects_any_collection_result(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    run.save("ERROR", error="SPAIN_DELIVERY_NOT_VERIFIED", detail_results={"some-item": {}})
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "fixed-code")
    with pytest.raises(ValueError, match="PREFLIGHT_MIGRATION_NOT_ELIGIBLE"):
        module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2,
                               resume=True, migrate_delivery_preflight=True)
    assert not (run.output / "code_migrations" / "delivery_preflight_v1.json").exists()


def test_authorized_delivery_auto_setup_uses_current_page_once(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    wrong = '<button id="nav-global-location-popover-link">Enviar a Estados Unidos</button>'
    correct = '<button id="nav-global-location-popover-link">Enviar a Madrid 28001</button>'
    session = Session([(200, wrong)])
    calls = []
    def setup(code, **kwargs):
        calls.append((code, kwargs))
        session.page.content = lambda: correct
        session.delivery_diagnostics = {"verified": True}
    session.ensure_spain_delivery = setup
    assert run._verify_delivery_location(session, wrong, auto_setup=True).is_spain is True
    assert calls == [("28001", {"use_current_page": True})]
    assert run.state["delivery_auto_attempted"] is True


def test_authorized_delivery_auto_failure_stays_in_manual_hold(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    wrong = '<button id="nav-global-location-popover-link">Enviar a Estados Unidos</button>'
    session = Session([(200, wrong)])
    calls = []
    def setup(*a, **k):
        calls.append(1)
        raise RuntimeError("no zip input")
    session.ensure_spain_delivery = setup
    def hold(kind, error):
        assert kind == "DELIVERY_LOCATION_HOLD" and len(calls) == 1
        raise RuntimeError("human needed")
    monkeypatch.setattr(run, "_manual_hold", hold)
    with pytest.raises(RuntimeError, match="human needed"):
        run._verify_delivery_location(session, wrong, auto_setup=True)
    assert (run.output / "preflight" / "delivery_auto_attempt.json").exists()


def test_delivery_auto_migration_is_chained_and_original_metadata_immutable(tmp_path, monkeypatch):
    run = runner(tmp_path, monkeypatch)
    original = (run.output / "run_metadata.json").read_bytes()
    run.save("ERROR", error="SPAIN_DELIVERY_NOT_VERIFIED")
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "delivery-fix")
    first = module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2,
                                   resume=True, migrate_delivery_preflight=True)
    first.save("ERROR", error="RUN_CODE_DRIFT_REQUIRES_AUDITED_REPARSE")
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "auto-delivery-fix")
    second = module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2,
                                    resume=True, migrate_delivery_auto=True)
    second.guards()
    assert (run.output / "run_metadata.json").read_bytes() == original
    second.save("RUNNING")
    module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2, resume=True).guards()
    second.save("ERROR", error="RUN_CODE_DRIFT_REQUIRES_AUDITED_REPARSE")
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "hecho-selector-fix")
    third = module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2,
                                   resume=True, migrate_delivery_auto_finish=True)
    third.guards()
    assert (run.output / "run_metadata.json").read_bytes() == original
    assert (run.output / "code_migrations" / "delivery_auto_v2.json").exists()
    third.save("RUNNING")
    module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2, resume=True).guards()
    monkeypatch.setattr(module, "runtime_code_hash", lambda _: "unreviewed")
    with pytest.raises(ValueError, match="INVALID_PREFLIGHT_CODE_MIGRATION"):
        module.LinkFirstRunner(run.plan_path, run.output, run.root, sample_size=2, resume=True)
