import json
from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.quality.source_fields import audit_source_fields

CASES = json.loads((Path(__file__).parent / "fixtures/source_option_unit_20261011.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("row", CASES[:5])
def test_generic_amazon_option_is_not_a_dimension_only_field(row):
    before = deepcopy(row)
    report = audit_source_fields([row])
    assert not any(i["issue_code"] in {"SPEC_UNIT_TYPE_MISMATCH", "UNIT_SEMANTICS_AMBIGUOUS"} for i in report["issues"])
    assert row == before


def test_negative_60_vs_50_label_count_conflict_remains_reviewable():
    report = audit_source_fields([CASES[5]])
    assert any(i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT" and
               i["evidence"].get("match_kind") == "explicit_item_count_conflict" for i in report["issues"])


def test_true_dimensions_and_liquid_volume_are_not_relaxed():
    for label, value in (("Dimensiones del producto", "5 Kilogramos"), ("Volumen del producto", "5 Kilogramos")):
        report = audit_source_fields([{"asin": "B000000001", "attributes": [{"label_raw": label, "value_raw": value}]}])
        assert any(i["issue_code"] == "SPEC_UNIT_TYPE_MISMATCH" for i in report["issues"])


def test_generic_option_with_no_corroboration_stays_reviewable():
    report = audit_source_fields([{"asin": "B000000001", "title_es_raw": "Etiquetas 60 piezas", "attributes": [
        {"label_raw": "Talla / Dimensiones", "value_raw": "75 piezas"}]}])
    assert any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in report["issues"])


@pytest.mark.parametrize("value", ["Estándar", "A4", "1 Unidad (Paquete de 1)"])
def test_generic_option_without_a_measure_unit_does_not_invent_a_unit_warning(value):
    row = {"asin": "B000000001", "attributes": [{"label_raw": "Talla / Dimensiones", "value_raw": value}]}
    report = audit_source_fields([row])
    assert not any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in report["issues"])


@pytest.mark.parametrize("value,conflict", [("192", False), ("50", True)])
def test_multiplicative_piece_option_is_compared_as_total_not_inner_count(value, conflict):
    row = {"asin": "B08XYZ4WFN", "selected_variation_raw": "DISNEY Frozen - Maxifloor Puzzle 4x48 Piezas",
           "attributes": [{"label_raw": "Número de piezas", "value_raw": value}]}
    report = audit_source_fields([row])
    assert any(i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT" for i in report["issues"]) is conflict
