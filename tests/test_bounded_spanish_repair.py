import hashlib
import json
from copy import deepcopy
from pathlib import Path

from amazon_es_bestseller.quality.source_fields import _category_leaf_supported, _sf_hash
from amazon_es_bestseller.production.bounded_spanish_repair import plain_bullet, repair_record
import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures/bounded_spanish_repair_cases.json").read_text(encoding="utf-8"))


def category_record():
    row = deepcopy(FIXTURE["category"])
    row["html_file"] = "detail_evidence/item/attempt_001.html"
    row["html_sha256"] = hashlib.sha256(b"saved-source-html").hexdigest()
    row["offline_replay_evidence"] = {"html_sha256": row["html_sha256"]}
    binding = {key: row[key] for key in ("asin", "html_file", "html_sha256", "detail_category_trail")}
    provenance = {"source": "product_breadcrumb", "selector": "#wayfinding-breadcrumbs_feature_div",
                  "evidence_binding": binding, "evidence_hash": _sf_hash(binding),
                  "levels": {key: row[key] for key in ("category_l1", "category_l2", "category_l3", "leaf_category")}}
    row["category_provenance"] = provenance
    return row


def supported(row):
    return _category_leaf_supported(row, row["category_provenance"], [row[k] for k in ("category_l1", "category_l2", "category_l3", "leaf_category")])


def test_real_three_level_breadcrumb_is_not_filled_down():
    assert supported(category_record())


def test_breadcrumb_claim_cannot_excuse_copied_or_changed_levels():
    row = category_record()
    row["category_l3"] = row["leaf_category"] = "invented leaf"
    assert not supported(row)


def test_breadcrumb_hash_and_capture_must_bind_to_same_record():
    for key, value in (("asin", "B000000000"), ("html_sha256", "0" * 64), ("detail_category_trail", ["Jardín", "fake", "leaf"])):
        row = category_record()
        row["category_provenance"]["evidence_binding"][key] = value
        row["category_provenance"]["evidence_hash"] = _sf_hash(row["category_provenance"]["evidence_binding"])
        assert not supported(row)
    row = category_record()
    row["offline_replay_evidence"] = {}
    assert not supported(row)


def repair_input():
    row = deepcopy(FIXTURE["category"])
    page = ('<div id="wayfinding-breadcrumbs_feature_div">' + ''.join(
        '<a>' + value + '</a>' for value in row["detail_category_trail"]) + '</div>').encode('utf-8')
    row.update(html_file="detail_evidence/item/attempt_001.html", html_sha256=hashlib.sha256(page).hexdigest(),
               offline_replay_evidence={"html_sha256": hashlib.sha256(page).hexdigest()},
               current_price_raw="34,76€", original_price_raw="19,76€", current_price=34.76, original_price=19.76,
               title_es_raw="Contenedor de almacenamiento", feature_bullets_raw=[FIXTURE["bullet"], "Tamaño: 10 cm; no impermeable"],
               attributes=[{"section":"technical_details","source":"prodDetails","position":0,"label_raw":"Marca","value_raw":"Keter"},
                           {"section":"technical_details","source":"prodDetails","position":1,"label_raw":"Peso del producto","value_raw":"2 kg"},
                           {"section":"technical_details","source":"prodDetails","position":2,"label_raw":"Peso del producto","value_raw":"3 kg"},
                           {"section":"technical_details","source":"prodDetails","position":3,"label_raw":"Clasificación en los más vendidos de Amazon","value_raw":"nº1"},
                           FIXTURE["source_conflict"]], detail_bullets_raw=["Dimensiones del producto: 20 x 10 x 5 cm"],
               notes="Keep my research notes", specification_es="", specification="")
    return row,page


def test_repairs_preserve_raw_conflicts_bullet_order_and_notes():
    row,page=repair_input()
    snapshot=deepcopy(row)
    fixed,changes=repair_record(row,page)
    assert row == snapshot
    assert fixed["attributes"] == row["attributes"]
    assert fixed["feature_bullets_raw"] == row["feature_bullets_raw"]
    assert fixed["notes"] == row["notes"]
    assert len(fixed["feature_bullets_es"].splitlines()) == 2
    assert "no impermeable" in fixed["feature_bullets_es"]
    assert "Peso del producto: 2 kg" in fixed["product_details_es"]
    assert "Peso del producto: 3 kg" in fixed["product_details_es"]
    assert "Capacidad de peso que puede soportar el producto: 12 litros" in fixed["product_details_es"]
    assert "más vendidos" not in fixed["product_details_es"]
    assert fixed["original_price"] is None and fixed["original_price_raw"] == "19,76€"
    assert fixed["discount_rate"] is None
    assert "20 x 10 x 5 cm" in fixed["specification_es"]
    assert changes and supported(fixed)


def test_html_drift_fails_closed():
    row,page=repair_input()
    with pytest.raises(ValueError,match="HASH_MISMATCH"):
        repair_record(row,page+b"changed")


def test_bullet_cleanup_preserves_numbers_and_negation():
    assert plain_bullet("<b>Nota:</b> No es impermeable. <br> 95 % poliéster, 5 % elastano.") == "Nota: No es impermeable. 95 % poliéster, 5 % elastano."


def test_damaged_source_is_not_guessed_or_excluded():
    row,page=repair_input()
    row["attributes"].append({"section":"technical_details","source":"prodDetails","position":4,"label_raw":"Tipo de altavoz","value_raw":"Port�til"})
    fixed,_=repair_record(row,page)
    assert "Port�til" in fixed["product_details_es"]
    assert fixed["attributes"][-1] == row["attributes"][-1]
