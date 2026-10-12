import importlib.util
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.monitoring import link_first_reuse as reuse
from amazon_es_bestseller.orchestration.link_first import file_hash
from amazon_es_bestseller.quality.staged_fields import resolve_selected_identity

FIXTURE = Path(__file__).with_name("fixtures").joinpath("link_first_detail.html").read_text(encoding="utf-8")
URL = "https://www.amazon.es/dp/B000CRBEJ2"


def evidence(tmp_path, html=FIXTURE):
    path = tmp_path / "old.html"
    path.write_text(html, encoding="utf-8")
    old = {"asin": "B000CRBEJ2", "requested_url": URL, "final_url": URL,
           "offline_replay_evidence": {"html_path": str(path), "html_sha256": file_hash(path)},
           "observed_at": "2026-10-07T00:00:00Z", "human_notes": "keep me"}
    meta = {"html_sha256": "current", "observed_at": "2026-10-10T00:00:00Z"}
    identity = resolve_selected_identity(html, URL, URL)
    return old, meta, identity


@pytest.fixture
def clean_audits(monkeypatch):
    # Isolate reuse semantics from source-specific review rules; separate test below
    # exercises the real known bad weight/parent fixture without this stub.
    monkeypatch.setattr(reuse, "audit_detail_fields", lambda *a: {"status": "PASS"})
    monkeypatch.setattr(reuse, "audit_source_fields", lambda *a: {"issues": []})


def test_new_identity_uses_full_current_details_and_unknown_attributes(tmp_path):
    _, meta, identity = evidence(tmp_path)
    row = reuse.reconcile_current_detail(FIXTURE, identity, meta)
    assert row["detail_reuse_provenance"]["reason"] == "NEW_ASIN"
    assert any(a["label_raw"] == "Atributo nuevo" for a in row["attributes"])


def test_reuse_reparses_html_not_untrusted_old_json_and_refreshes_price(tmp_path, clean_audits):
    old, meta, identity = evidence(tmp_path)
    old["attributes"] = [{"label_raw": "poison", "value_raw": "wrong"}]
    current_html = FIXTURE.replace("</body>", '<div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">9,99€</span></span></div></body>')
    row = reuse.reconcile_current_detail(current_html, identity, meta, old)
    assert row["detail_reuse_provenance"]["action"] == "REUSE_VALIDATED_STABLE_DETAIL"
    assert row["current_price_raw"] == "9,99€"
    assert row["human_notes"] == "keep me"
    assert all(a["label_raw"] != "poison" for a in row["attributes"])
    assert "bestseller_rank" not in row


@pytest.mark.parametrize("before,after,field", [
    ("Valor desconocido", "Nuevo valor", "attributes"),
    ("Primero", "Nuevo punto", "feature_bullets_raw"),
    ("Mochila 25 L", "Mochila 30 L", "title_es_raw"),
    ("0,28 kg", "0,30 kg", "attributes"),
])
def test_stable_changes_force_full_current_with_no_lost_fields(tmp_path, clean_audits, before, after, field):
    old, meta, identity = evidence(tmp_path)
    row = reuse.reconcile_current_detail(FIXTURE.replace(before, after), identity, meta, old)
    decision = row["detail_reuse_provenance"]
    assert decision["reason"] == "CURRENT_STABLE_CONTENT_CHANGED"
    assert field in decision["changed_stable_fields"]
    assert after in json.dumps(row, ensure_ascii=False)


def test_volatile_bsr_stays_current_never_inherited(tmp_path, clean_audits):
    old_html = FIXTURE.replace('<div id="prodDetails"><table>', '<div id="prodDetails"><table><tr><th>Clasificación en los más vendidos</th><td>1 en Hogar</td></tr>', 1)
    old, meta, identity = evidence(tmp_path, old_html)
    current = old_html.replace("1 en Hogar", "20 en Hogar")
    row = reuse.reconcile_current_detail(current, identity, meta, old)
    assert row["detail_reuse_provenance"]["action"] == "REUSE_VALIDATED_STABLE_DETAIL"
    assert any(a["value_raw"] == "20 en Hogar" for a in row["attributes"])
    assert not any(a["value_raw"] == "1 en Hogar" for a in row["attributes"])


def test_invalid_current_identity_never_reuses(tmp_path, clean_audits):
    old, meta, identity = evidence(tmp_path)
    identity.update(status="BLOCK", asin="")
    row = reuse.reconcile_current_detail(FIXTURE, identity, meta, old)
    assert row["detail_reuse_provenance"]["reason"] == "CURRENT_IDENTITY_UNCONFIRMED"


def test_historical_identity_conflict_and_missing_evidence_fail_closed(tmp_path, clean_audits):
    old, meta, identity = evidence(tmp_path)
    old["asin"] = "B000000001"
    assert reuse.reconcile_current_detail(FIXTURE, identity, meta, old)["detail_reuse_provenance"]["reason"] == "HISTORICAL_IDENTITY_CONFLICT"
    old["asin"] = identity["asin"]
    old["offline_replay_evidence"] = {}
    assert reuse.reconcile_current_detail(FIXTURE, identity, meta, old)["detail_reuse_provenance"]["reason"] == "HISTORICAL_HTML_MISSING"


def test_tampered_old_html_stops_instead_of_silent_cache_miss(tmp_path, clean_audits):
    old, meta, identity = evidence(tmp_path)
    Path(old["offline_replay_evidence"]["html_path"]).write_text("changed")
    with pytest.raises(ValueError, match="HISTORICAL_HTML_DRIFT"):
        reuse.reconcile_current_detail(FIXTURE, identity, meta, old)


def test_real_parent_scope_bad_weight_not_reused(tmp_path):
    old, meta, identity = evidence(tmp_path)
    row = reuse.reconcile_current_detail(FIXTURE, identity, meta, old)
    assert row["detail_reuse_provenance"]["reason"] == "HISTORICAL_FIELD_REVIEW_REQUIRED"
    assert any(a["value_raw"] == "Modelo ABC" for a in row["attributes"])


def test_incremental_entry_rejects_same_or_nested_output(tmp_path):
    path = Path(__file__).parents[1] / "tools" / "incremental_link_collection.py"
    spec = importlib.util.spec_from_file_location("incremental_tool", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    with pytest.raises(ValueError, match="INDEPENDENT_NEW_RUN"):
        tool.freeze_switch(tmp_path, tmp_path / "nested", tmp_path / "b", tmp_path / "s", tmp_path / "p")


def load_tool():
    path = Path(__file__).parents[1] / "tools" / "incremental_link_collection.py"
    spec = importlib.util.spec_from_file_location("incremental_tool", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def prepared_switch(tmp_path, monkeypatch):
    from amazon_es_bestseller.orchestration import link_first as module
    from test_link_first_runner import Session, runner
    old_run = runner(tmp_path, monkeypatch)
    category = old_run.plan["categories"][0]
    session = Session([(200, '<div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/dp/B000CRBEJ2">Product</a></div>')])
    session.load_lazy_ranking_content = lambda: None
    old_run._snapshot(session, category, "primary")
    session.pages = [(200, FIXTURE)]
    old_run._detail(session, old_run.links[0], category)
    old_run.save("RUNNING", phase="FULL")
    old, _, _ = evidence(tmp_path)
    baseline, scope = tmp_path / "baseline.json", tmp_path / "scope.json"
    baseline.write_text(json.dumps([old]), encoding="utf-8")
    scope.write_text(json.dumps({"binding_scope": {"asins": [old["asin"]]}}))
    return module, old_run, baseline, scope, session


def test_switch_preserves_snapshot_results_hashes_and_never_repeats_completed_requests(tmp_path, monkeypatch):
    _, old, baseline, scope, session = prepared_switch(tmp_path, monkeypatch)
    checkpoint_hash = file_hash(old.output / "checkpoints" / "link_first_state.latest.json")
    tool = load_tool()
    new = tool.IncrementalRunner(old.plan_path, tmp_path / "incremental", tmp_path,
                                origin=old.output, baseline=baseline, scope=scope)
    new.guards()
    calls = len(session.calls)
    new._snapshot(session, new.plan["categories"][0], "primary")
    new._detail(session, new.links[0], new.plan["categories"][0])
    assert len(session.calls) == calls
    assert len(new.links) == len(old.links)
    assert new.records == old.records
    assert new.metadata["previous_details_reuse"] is True
    assert file_hash(old.output / "checkpoints" / "link_first_state.latest.json") == checkpoint_hash
    assert all(not any("asin" in k for k in row) for row in new.links)
    baseline.write_text("[]")
    with pytest.raises(ValueError, match="SWITCH_INPUT_DRIFT"):
        new.guards()


def test_switch_rejects_access_hold_before_migration(tmp_path, monkeypatch):
    _, old, baseline, scope, _ = prepared_switch(tmp_path, monkeypatch)
    old.save("ACCESS_STOP", phase="FULL")
    with pytest.raises(ValueError, match="NO_ACCESS_HOLD"):
        load_tool().freeze_switch(old.output, tmp_path / "incremental", baseline, scope, old.plan_path)
    assert not (tmp_path / "incremental" / "incremental_inputs.json").exists()
