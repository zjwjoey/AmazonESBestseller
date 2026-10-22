"""Second-pass offline review: real records must underpin successful outcomes."""
import json

import pytest

from amazon_es_bestseller.access.detector import AccessStopError
from amazon_es_bestseller.collection.checkpoints import write_checkpoint
from amazon_es_bestseller.collection.detail import (
    audit_saved_detail_cache, collect_details, parse_detail_page, reparse_saved_details,
)
from amazon_es_bestseller.collection.detail_executor import execute_detail_plan
from amazon_es_bestseller.collection.planning import DetailState
from amazon_es_bestseller.translation.full_detail import render_details_es
from test_detail_extraction_regressions import ASIN, Session, page, plan, save_html


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr("amazon_es_bestseller.collection.detail.time.sleep", lambda _: None)


def test_reparse_respects_each_plan_rows_evidence_directory(tmp_path):
    rows = []
    for i, asin in enumerate([ASIN, "B000000002"]):
        evidence = tmp_path / str(i)
        save_html(evidence, page(asin, title=f"Product {i}"), asin)
        rows.append({"snapshot_id": "regression", "ranking_asin": asin,
                     "detail_action": "REPARSE_SAVED_HTML", "saved_html_dir": str(evidence / "html")})
    result = execute_detail_plan({"snapshot_id": "regression", "records": rows}, None,
                                 str(tmp_path / "out"), offline=True)
    assert result["completed_count"] == 2
    assert result["execution_status"] == "COMPLETE"
    assert result["state_record_count"] == 2


def test_flag_only_current_checkpoint_cannot_use_old_state_to_claim_success(tmp_path):
    (tmp_path / "details.json").write_text(json.dumps([
        {"asin": ASIN, "title_es_raw": "Old snapshot product"}]), encoding="utf-8")

    def collector(*args, **kwargs):
        write_checkpoint(tmp_path / "checkpoints", ASIN, {
            "status": "success", "snapshot_id": "regression", "action": "FETCH_NEW"})
        return []

    result = execute_detail_plan(plan("FETCH_NEW"), None, str(tmp_path), collector=collector)
    assert result["completed_count"] == 0
    assert result["execution_status"] == "INCOMPLETE"
    assert result["state_record_count"] == 1  # keep historical data, not current success


def test_http_stop_is_saved_before_gate_so_cache_cannot_become_normal(tmp_path):
    class BlockedSession(Session):
        def goto(self, url):
            super().goto(url)
            return 403

    session = BlockedSession(page())  # status alone is sufficient to stop
    with pytest.raises(AccessStopError):
        collect_details([ASIN], session, str(tmp_path))
    meta = tmp_path / "html" / (ASIN + ".meta.json")
    assert json.loads(meta.read_text(encoding="utf-8"))["status_code"] == 403
    next_session = Session()
    with pytest.raises(AccessStopError):
        collect_details([ASIN], next_session, str(tmp_path))
    assert next_session.calls == []


def test_audit_corrupt_html_reports_it_and_continues_without_modifying_evidence(tmp_path):
    bad = save_html(tmp_path, "bad")
    bad.write_bytes(b"\xff\xfeinvalid")
    save_html(tmp_path, page("B000000002"), "B000000002")
    result = audit_saved_detail_cache(tmp_path / "html")
    assert result["summary"]["INVALID_OR_EMPTY"] == 1
    assert result["summary"]["VALID_PRODUCT_PAGE"] == 1
    assert result["records"][0]["error"]
    assert bad.read_bytes() == b"\xff\xfeinvalid"


@pytest.mark.parametrize("metadata", ["[]", "{broken", '{"status_code": "bad"}'])
def test_corrupt_metadata_cannot_be_discarded_to_make_a_valid_cache(tmp_path, metadata):
    save_html(tmp_path, page())
    meta = tmp_path / "html" / (ASIN + ".meta.json")
    meta.write_text(metadata, encoding="utf-8")
    state = DetailState(tmp_path / "state.json")
    assert reparse_saved_details(tmp_path / "html", state) == []
    result = audit_saved_detail_cache(tmp_path / "html")
    assert result["summary"]["INVALID_OR_EMPTY"] == 1
    session = Session()
    assert collect_details([ASIN], session, str(tmp_path)) == []
    assert session.calls == []
    assert meta.read_text(encoding="utf-8") == metadata


@pytest.mark.parametrize("content", ["{broken", "[]", '{"B000000001": null}',
    '{"B000000001": {"asin": "B000000002", "title_es_raw": "Wrong identity"}}'])
def test_invalid_authoritative_state_cannot_be_overwritten_or_trigger_network(tmp_path, content):
    state_path = tmp_path / "state" / "details_state.json"
    state_path.parent.mkdir()
    state_path.write_text(content, encoding="utf-8")
    session = Session()
    with pytest.raises(ValueError, match="DETAIL_STATE"):
        execute_detail_plan(plan("FETCH_NEW"), session, str(tmp_path))
    assert state_path.read_text(encoding="utf-8") == content
    assert session.calls == []


def test_explicit_th_brand_is_mapped_and_hidden_brand_is_not_used():
    html = page(body='''<div id="productOverview_feature_div"><table>
      <tr hidden><th>Marca</th><td>Hidden brand</td></tr>
      <tr><th>Marca</th><td>Visible brand</td></tr></table></div>''')
    assert parse_detail_page(html, ASIN)["brand_raw"] == "Visible brand"


def test_hidden_original_price_cannot_hide_visible_original_price():
    html = page(body='''<div id="corePrice_feature_div" hidden>
      <span class="a-text-price" data-a-strike="true"><span class="a-offscreen">99,00 €</span></span></div>
      <div id="apex_price"><span class="a-text-price" data-a-strike="true">
      <span class="a-offscreen">19,99 €</span></span></div>''')
    assert parse_detail_page(html, ASIN)["original_price_raw"] == "19,99 €"


def test_original_price_never_borrows_recommended_product():
    html = page(body='''<div id="desktop-dp-sims"><div id="corePrice_feature_div">
      <span class="a-text-price" data-a-strike="true"><span class="a-offscreen">99,00 €</span></span>
      </div></div>''')
    assert parse_detail_page(html, ASIN)["original_price_raw"] == ""


def test_nontruncated_prefix_attributes_are_distinct_facts():
    rows = [{"label_raw": "Modelo", "value_raw": value} for value in ["X", "X (Pro)"]]
    assert render_details_es(rows).splitlines() == ["Modelo: X", "Modelo: X (Pro)"]


def test_conflicting_strong_page_identities_are_not_accepted_as_a_match(tmp_path):
    html = page(body='<link rel="canonical" href="https://www.amazon.es/dp/B000000002">')
    session = Session(html)
    assert collect_details([ASIN], session, str(tmp_path)) == []
    checkpoint = json.loads((tmp_path / "checkpoints" / (ASIN + ".json")).read_text(encoding="utf-8"))
    assert checkpoint["status"] == "asin_mismatch"
    assert session.delays == 1


@pytest.mark.parametrize("record_fields", [{"title_es_raw": "   "},
    {"identity_status_code": "IDENTITY_MISMATCH"}, {"final_access_state": "BLOCKED"}])
def test_incomplete_or_contradictory_cached_record_cannot_pass(tmp_path, record_fields):
    (tmp_path / "details.json").write_text(json.dumps([
        {"asin": ASIN, "title_es_raw": "Old product", **record_fields}]), encoding="utf-8")
    result = execute_detail_plan(plan("REUSE_VALID_CACHE"), None, str(tmp_path), offline=True)
    assert result["execution_status"] == "INCOMPLETE"


@pytest.mark.parametrize("status", ["ACCESS_STOP", "DETAIL_INCOMPLETE", "FAILED"])
def test_category_summary_cannot_report_pass_from_ranking_quota_alone(tmp_path, status):
    import csv
    from amazon_es_bestseller.collection.task import _write_summary
    rows = [{"asin": ASIN, "research_category": "A", "ranking_source_url": "saved-source"}]
    _write_summary(tmp_path, [{"research_category": "A", "target_unique": 1}],
                   rows, [], {"A": rows}, {"A": {"status": status}})
    with (tmp_path / "category_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.DictReader(handle))["状态"] == status


def test_complete_category_summary_requires_actual_selected_product_details(tmp_path):
    import csv
    from amazon_es_bestseller.collection.task import _write_summary
    rows = [{"asin": ASIN, "research_category": "A", "ranking_source_url": "saved-source"}]
    for details, expected in [([], "DETAIL_INCOMPLETE"), ([{"asin": ASIN}], "PASS")]:
        _write_summary(tmp_path, [{"research_category": "A", "target_unique": 1}],
                       rows, details, {"A": rows}, {"A": {"status": "COMPLETE"}})
        with (tmp_path / "category_summary.csv").open(encoding="utf-8-sig", newline="") as handle:
            assert next(csv.DictReader(handle))["状态"] == expected
