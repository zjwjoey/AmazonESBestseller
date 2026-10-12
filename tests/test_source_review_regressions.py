"""Real 2026-10-10 source diagnoses; source evidence must remain immutable."""
import json
import hashlib
import importlib.util
import socket
from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.collection.detail import parse_detail_page
from amazon_es_bestseller.quality import detail_identity
from amazon_es_bestseller.quality.source_fields import audit_source_fields

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "source_review_20261010.json").read_text(encoding="utf-8"))


def audited(row):
    return audit_source_fields([{**row, "product_url": f"https://www.amazon.es/dp/{row['asin']}"}])


def test_bidi_identity_is_exact_without_mutating_raw():
    row = deepcopy(CASES["bidi_asin"])
    before = deepcopy(row)
    result = detail_identity.review_visible_asin_identity(row)
    assert result["status"] == "EXACT_ASIN"
    assert not result["conflicts"]
    assert len(result["normalized_only"]) == 2
    assert row == before


def test_parent_difference_requires_bound_html_and_stays_reviewable():
    row = deepcopy(CASES["parent_detail"])
    signals = detail_identity.extract_detail_identity_signals(CASES["parent_html"])
    result = detail_identity.review_visible_asin_identity(row, signals)
    assert result["status"] == "PARENT_DETAIL_REVIEW_REQUIRED"
    assert not result["conflicts"]
    assert result["parent_related_values"] == ["B0D7MQ7BFB"]
    assert detail_identity.review_visible_asin_identity(row)["status"] != "EXACT_ASIN"


@pytest.mark.parametrize("html", [
    "<input id='ASIN' value='B000000099'><script>{\"parentAsin\":\"B0D7MQ7BFB\"}</script>",
    CASES["parent_html"] + "<link rel='canonical' href='https://www.amazon.es/dp/B000000099'>",
])
def test_unrelated_strong_signal_is_not_hidden_by_matching_child(html):
    signals = detail_identity.extract_detail_identity_signals(html)
    result = detail_identity.review_visible_asin_identity(CASES["parent_detail"], signals)
    assert result["status"] == "IDENTITY_CONFLICT"


def test_parent_claim_without_html_proof_does_not_pass_source_gate():
    report = audited(CASES["parent_detail"])
    assert report["sku_status"]["B000CRBEJ2"] != "SOURCE_READY"


def test_unit_amount_is_not_piece_count_and_real_conflict_is_retained():
    row = deepcopy(CASES["nori"])
    before = deepcopy(row)
    report = audited(row)
    conflicts = [i for i in report["issues"] if i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT"]
    assert conflicts and any(i["evidence"].get("match_kind") == "quantity_cross_field_conflict" for i in conflicts)
    assert not any(i["issue_code"] == "SPEC_UNIT_TYPE_MISMATCH" for i in report["issues"])
    assert row == before


def test_net_amount_corroborated_by_title_is_not_a_count_unit_error():
    row = deepcopy(CASES["nori"])
    row["attributes"][0]["value_raw"] = "25.0 Gramos"
    report = audited(row)
    assert not any(i["issue_code"] in {"SPEC_UNIT_TYPE_MISMATCH", "SOURCE_SEMANTIC_CONFLICT", "UNIT_SEMANTICS_AMBIGUOUS"} for i in report["issues"])


def test_weight_based_solid_refill_capacity_has_explicit_context():
    report = audited(CASES["refill"])
    assert not any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in report["issues"])
    assert any(i["evidence"].get("match_kind") == "solid_refill_amount_correlated" for i in report["field_audits"])


def test_unknown_capacity_and_source_weight_volume_conflict_remain_reviewable():
    unknown = {**CASES["refill"], "title_es_raw": "Producto 280 g", "selected_variation_raw": "280g"}
    assert any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in audited(unknown)["issues"])
    assert any(i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT" for i in audited(CASES["bin"])["issues"])


def test_new_price_is_not_original_price_and_recommendations_are_excluded():
    html = (FIXTURES / "detail_new_price_B0DCGHLHMM.html").read_text(encoding="utf-8")
    parsed = parse_detail_page(html, "B0DCGHLHMM")
    assert parsed["current_price_raw"] == "5,99€"
    assert parsed["original_price_raw"] == ""


def test_new_price_label_does_not_hide_a_separate_list_price():
    html = (FIXTURES / "detail_new_price_B0DCGHLHMM.html").read_text(encoding="utf-8")
    html = html.replace("<div id=\"recommendations\">", "<div id='corePriceDisplay_desktop_feature_div'><span class='apex-basisprice-feature'>Precio recomendado: <span class='a-text-price' data-a-strike='true'><span class='a-offscreen'>7,99€</span></span></span></div><div id=\"recommendations\">")
    assert parse_detail_page(html, "B0DCGHLHMM")["original_price_raw"] == "7,99€"


def test_net_quantity_equivalent_units_are_not_cross_field_conflicts():
    row = {"asin": "B000000001", "title_es_raw": "1 x 1 kg", "selected_variation_raw": "1 kg (Paquete de 1)",
           "attributes": [{"label_raw": "Unidades", "value_raw": "1000 Gramos"}]}
    assert not any(i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT" for i in audited(row)["issues"])


def test_multi_pack_net_amount_with_exact_total_proof_is_not_ambiguous():
    row = {"asin": "B000000001", "title_es_raw": "10 x 25 g", "selected_variation_raw": "25 g (Paquete de 10)",
           "attributes": [{"label_raw": "Unidades", "value_raw": "250 Gramos"}]}
    report = audited(row)
    assert not any(i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT" for i in report["issues"])
    assert not any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in report["issues"])
    proof = next(f["evidence"] for f in report["field_audits"] if f["evidence"].get("match_kind") == "unit_count_package_total_correlated")
    assert proof["package_proof"]["count"] == 10
    assert proof["package_proof"]["total"] == "250"
    assert proof["same_asin_record_binding"]


def replay_module():
    spec = importlib.util.spec_from_file_location("offline_replay_review_test", FIXTURES.parents[1] / "tools/offline_issue_regression_5480.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validated_price_refresh_preserves_all_nonprice_source_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    module = replay_module()
    path = tmp_path / "B0DCGHLHMM.html"
    html = (FIXTURES / "detail_new_price_B0DCGHLHMM.html").read_bytes()
    path.write_bytes(html)
    before = {"asin": "B0DCGHLHMM", "current_price_raw": "5,99€", "original_price_raw": "5,99€",
              "attributes": [{"label_raw": "Material", "value_raw": "Unknown kept"}], "feature_bullets_raw": ["Evidence"], "notes": "Human-owned"}
    previous = {**deepcopy(before), "offline_replay_status": "REPARSED_IDENTITY_MATCH", "offline_replay_evidence": {
        "html_path": str(path), "html_sha256": hashlib.sha256(html).hexdigest(), "meta_sha256": None,
        "original_record_sha256": hashlib.sha256(json.dumps(before, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}}
    rebuilt, log = module.replay_validated_one(before["asin"], before, previous)
    assert rebuilt["original_price_raw"] == ""
    assert log["price_changed_from_previous_replay"] is True
    for field in ("attributes", "feature_bullets_raw", "notes", "current_price_raw"):
        assert rebuilt[field] == before[field] == previous[field]
    path.write_bytes(html + b"DRIFT")
    with pytest.raises(ValueError, match="REUSE_HTML_OR_META_DRIFT"):
        module.replay_validated_one(before["asin"], before, previous)


def test_typed_identity_signals_with_wrong_html_binding_do_not_pass():
    row = deepcopy(CASES["parent_detail"])
    row["detail_identity_signals"] = detail_identity.extract_detail_identity_signals(CASES["parent_html"])
    row["offline_replay_evidence"] = {"html_sha256": "0" * 64}
    assert audited(row)["sku_status"][row["asin"]] != "SOURCE_READY"
