import json
from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.normalization.measure_evidence import count_signatures
from amazon_es_bestseller.quality.source_fields import audit_source_fields

CASES = json.loads((Path(__file__).parent / "fixtures/source_package_quantity_20261011.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("row", CASES)
def test_real_explicit_package_evidence_removes_only_quantity_false_positive(row):
    before = deepcopy(row)
    audit = audit_source_fields([row])
    assert not any(i["issue_code"] in {"UNIT_SEMANTICS_AMBIGUOUS", "SOURCE_SEMANTIC_CONFLICT"} for i in audit["issues"])
    assert any(f["field"] == "attributes" and f["classification"] == "PASS" for f in audit["field_audits"])
    assert row == before


def test_singular_unidad_and_plural_unidades_share_count_semantics():
    assert count_signatures("16 unidad") == count_signatures("16 unidades") == {"16pcs"}


def test_multiplication_symbol_in_literal_item_count_is_preserved():
    row = deepcopy(CASES[0])
    row["title_es_raw"] = row["title_es_raw"].replace("24 x Pinzas", "24 × Pinzas")
    assert not any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in audit_source_fields([row])["issues"])


def test_piece_count_does_not_corroborate_a_conflicting_net_measure():
    row = {"asin": "B000000001", "title_es_raw": "Aceite 1 unidad, 300 ml",
           "selected_variation_raw": "1 unidad (Paquete de 1)",
           "attributes": [{"label_raw": "Número de unidades", "value_raw": "250 Mililitros"}]}
    audit = audit_source_fields([row])
    assert any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in audit["issues"])
    assert not any(i["evidence"].get("match_kind") == "quantity_cross_field_conflict" for i in audit["issues"])


@pytest.mark.parametrize("title,selected,value", [
    ("Pinzas para bolsas, 24 días de garantía", "Colorido", "24 Piezas"),
    ("24 x Pinzas Bolsas", "25 piezas", "24 Piezas"),
    ("Pack de 24 Latas de 85 g", "85 g (Paquete de 24)", "2050 Gramos"),
    ("Pack de 24 Latas de 85 g", "85 g (Paquete de 12)", "2040 Gramos"),
    ("Pack de 24 Latas de 85 g o Pack de 12 Latas de 85 g", "", "2040 Gramos"),
    ("Mesa de 24 x 85 cm", "", "2040 Gramos"),
])
def test_wrong_total_different_pack_options_and_unrelated_numbers_stay_open(title, selected, value):
    row = {"asin": "B000000001", "title_es_raw": title, "selected_variation_raw": selected,
           "attributes": [{"label_raw": "Tamaño" if "Piezas" in value else "Número de unidades", "value_raw": value}]}
    assert any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in audit_source_fields([row])["issues"])


@pytest.mark.parametrize("label,value", [("Volumen del producto", "5 Kilogramos"), ("Dimensiones del producto", "5 Kilogramos")])
def test_true_typed_fact_error_is_not_explained_as_pack_quantity(label, value):
    row = {"asin": "B000000001", "title_es_raw": "Pack de 5 unidades", "attributes": [{"label_raw": label, "value_raw": value}]}
    assert any(i["issue_code"] == "SPEC_UNIT_TYPE_MISMATCH" for i in audit_source_fields([row])["issues"])
