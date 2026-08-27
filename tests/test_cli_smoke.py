# -*- coding: utf-8 -*-
"""CLI smoke/vertical tests: all paths stay offline and use tiny fixtures."""
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.cli import main


def test_reparse_details_prints_its_own_report_and_supports_multiple_dirs(tmp_path, capsys):
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "B000000001.html").write_text(
        '<input id="ASIN" value="B000000001"><div id="productTitle">Caja</div>',
        encoding="utf-8",
    )
    out = tmp_path / "details.json"

    assert main(["reparse-details", "--html-dir", str(first), str(second),
                 "--state", str(state), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "reparse-details" in captured.out
    assert "repair-cache" not in captured.out
    assert "B000000001" in out.read_text(encoding="utf-8")


def test_run_manifest_round_trip_and_stage_updates(tmp_path):
    from amazon_es_bestseller.run_manifest import (
        create_manifest, finalize_manifest, load_manifest, update_manifest,
        write_manifest,
    )

    manifest = create_manifest("run-001", started_at="2026-08-27T00:00:00+08:00",
                              git_commit="abc123", config_hash="cfg")
    manifest = update_manifest(manifest, status="running", ranking_records=5,
                               unique_asins=4, detail_planned=4)
    manifest = finalize_manifest(manifest, status="success", export_status="success",
                                 final_workbook="out.xlsx")
    path = write_manifest(manifest, tmp_path / "run.json")
    loaded = load_manifest(path)
    assert loaded["run_id"] == "run-001"
    assert loaded["ranking_records"] == 5
    assert loaded["detail_planned"] == 4
    assert loaded["status"] == "success"
    assert loaded["final_workbook"] == "out.xlsx"


def test_select_quota_global_uniqueness_and_shortfall(tmp_path):
    rankings = tmp_path / "rankings.json"
    config = tmp_path / "config.json"
    out = tmp_path / "manifest.json"
    rankings.write_text(json.dumps([
        {"asin": "A000000001", "category_group": "hogar"},
        {"asin": "A000000002", "category_group": "hogar"},
        {"asin": "A000000002", "category_group": "diy"},
        {"asin": "A000000003", "category_group": "diy"},
    ]), encoding="utf-8")
    config.write_text(json.dumps([
        {"group": "hogar", "quota": 2}, {"group": "diy", "quota": 1},
    ]), encoding="utf-8")
    assert main(["select-quota", "--rankings", str(rankings), "--config", str(config),
                 "--out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["summary"]["total"] == 3
    assert len({r["asin"] for r in saved["records"]}) == 3

    config.write_text(json.dumps([
        {"group": "hogar", "quota": 2}, {"group": "diy", "quota": 2},
    ]), encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        main(["select-quota", "--rankings", str(rankings), "--config", str(config),
              "--out", str(out)])
    assert "QUOTA_UNIQUE_SHORTFALL" in str(ei.value.code)


def test_translate_ds_isolates_partial_and_failed_records(tmp_path, monkeypatch):
    products = tmp_path / "products.json"
    out = tmp_path / "translations.json"
    products.write_text(json.dumps([
        {"asin": "B000000001", "title_es_raw": "Uno"},
        {"asin": "B000000002", "title_es_raw": "Dos"},
        {"asin": "B000000003", "title_es_raw": "Tres"},
    ]), encoding="utf-8")
    saves = []

    class FakeTranslator:
        def __init__(self, **kwargs):
            self.cache_path = kwargs.get("cache_path")

        def translate_record(self, record):
            status = {"B000000001": "success", "B000000002": "partial",
                      "B000000003": "failed"}[record["asin"]]
            return {"asin": record["asin"], "translation_status": status,
                    "title_zh": record["asin"] if status != "failed" else ""}

        def save_cache(self):
            saves.append(1)

    import amazon_es_bestseller.translation.ds as ds
    monkeypatch.setattr(ds, "DeepSeekTranslator", FakeTranslator)
    assert main(["translate-ds", "--products", str(products), "--out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert {v["translation_status"] for v in saved.values()} == {"success", "partial", "failed"}
    assert len(saves) == 3


def test_enrich_to_export_minimal_offline_vertical_path(tmp_path):
    rankings = tmp_path / "rankings.json"
    details = tmp_path / "details.json"
    translations = tmp_path / "translations.json"
    products = tmp_path / "products.json"
    qa = tmp_path / "qa.json"
    closure = tmp_path / "closure.json"
    workbook = tmp_path / "out.xlsx"
    asin = "B000000001"
    rankings.write_text(json.dumps([{
        "asin": asin, "bestseller_rank": 1,
        "ranking_source_url": "https://www.amazon.es/Best-Sellers-Hogar/zgbs/1",
        "category_l1": "Hogar y cocina", "category_group": "hogar",
    }]), encoding="utf-8")
    details.write_text(json.dumps([{
        "asin": asin, "title_es_raw": "Caja 2 piezas", "current_price_raw": "12,99 €",
        "brand_raw": "Marca", "selected_variation_raw": "Rojo",
        "attributes": [{"label_raw": "Número de piezas", "value_raw": "2"}],
        "feature_bullets_raw": ["Caja reutilizable"], "product_url": f"https://www.amazon.es/dp/{asin}",
        "image_url": "https://example.invalid/x.jpg",
    }]), encoding="utf-8")
    translations.write_text(json.dumps({asin: {"title_zh": "两件装收纳盒"}}), encoding="utf-8")
    assert main(["enrich", "--rankings", str(rankings), "--details", str(details),
                 "--translations", str(translations), "--out", str(products)]) == 0
    assert main(["qa", "--products", str(products), "--out", str(qa)]) == 0
    assert main(["audit-fields", "--products", str(products), "--details", str(details),
                 "--rankings", str(rankings), "--translations", str(translations),
                 "--out", str(closure)]) == 0
    assert main(["export", "--products", str(products), "--out", str(workbook), "--force"]) == 0
    saved = json.loads(products.read_text(encoding="utf-8"))
    assert saved[0]["asin"] == asin
    assert saved[0]["product_details_zh"]
    assert saved[0]["feature_bullets_zh"]
    assert json.loads(qa.read_text(encoding="utf-8"))["records"]
    assert json.loads(closure.read_text(encoding="utf-8"))["summary"]


def _export_gate_product(tmp_path):
    products = tmp_path / "products.json"
    products.write_text(json.dumps([{
        "asin": "B000000001", "title_es_raw": "Caja",
        "product_url": "https://www.amazon.es/dp/B000000001",
        "image_url": "https://example.invalid/x.jpg", "current_price": 1,
        "brand": "KRUPS",
    }]), encoding="utf-8")
    details = tmp_path / "details.json"
    details.write_text("[{\"asin\": \"B000000001\"}]", encoding="utf-8")
    return products, details


@pytest.mark.parametrize("classification", [
    "PARSER_MISSED", "MAPPING_MISSED", "DERIVED_MISSING", "TRANSLATION_INCOMPLETE",
])
def test_export_gate_blocks_closure_p1(tmp_path, monkeypatch, classification):
    products, details = _export_gate_product(tmp_path)
    import amazon_es_bestseller.qa.field_closure as closure
    monkeypatch.setattr(closure, "audit_field_closure", lambda *args, **kwargs: {
        "records": [{"asin": "B000000001", "classification": classification,
                     "severity": "P1", "message": "fixture"}]
    })
    with pytest.raises(SystemExit) as ei:
        main(["export", "--products", str(products), "--details", str(details),
              "--out", str(tmp_path / "blocked.xlsx")])
    assert "拒绝导出" in str(ei.value.code)


def test_export_gate_allows_source_missing_only(tmp_path, monkeypatch):
    products, details = _export_gate_product(tmp_path)
    import amazon_es_bestseller.qa.field_closure as closure
    monkeypatch.setattr(closure, "audit_field_closure", lambda *args, **kwargs: {
        "records": [{"asin": "B000000001", "classification": "SOURCE_MISSING",
                     "severity": "P2", "message": "fixture"}]
    })
    out = tmp_path / "allowed.xlsx"
    assert main(["export", "--products", str(products), "--details", str(details),
                 "--out", str(out)]) == 0
    assert out.exists()


@pytest.mark.parametrize("command", [
    "collect", "select-quota", "translate-ds", "enrich", "repair-cache",
    "reparse-details", "qa", "audit-fields", "export",
])
def test_each_cli_help_is_parseable(command, capsys):
    with pytest.raises(SystemExit) as ei:
        main([command, "--help"])
    assert ei.value.code == 0
    assert command in capsys.readouterr().out
