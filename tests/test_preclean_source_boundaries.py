import json
from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.preclean import audit_records, numeric_profile, parse_details
from amazon_es_bestseller.translation.production import build_production_input, records_for_preclean


FIXTURE = json.loads((Path(__file__).parent / "fixtures/preclean_source_boundaries.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("sample", FIXTURE["numeric"], ids=lambda x: x["asin"])
def test_saved_numeric_samples_do_not_compare_capacity_with_dimensions_or_dosage(sample):
    assert numeric_profile(sample["source_text"])["issues"] == []
    source_field = "product_description_raw" if sample["field"] == "product_description" else sample["field"]
    row = audit_records(records_for_preclean(build_production_input([
        {"asin": sample["asin"], source_field: sample["source_text"]}
    ])))["translation_input_records"][0]
    assert "CONTRADICTORY_CAPACITY" not in row["fields"][sample["field"]]["issues"]


@pytest.mark.parametrize("text,label", [
    ("Capacidad: 900 ml / 600 ml", None),
    ("900 ml / 600 ml", "Capacidad"),
    ("Capacidad: 900 ml / Peso: 1 kg / Capacidad: 600 ml", None),
])
def test_real_same_semantic_capacity_conflict_remains_blocked(text, label):
    assert "CONTRADICTORY_CAPACITY" in numeric_profile(text, label=label)["issues"]
    raw = {"asin": "B000000020", "product_details": [{"label_raw": label, "value_raw": text}]} if label else {
        "asin": "B000000020", "specification_es": text}
    fields = audit_records([raw])["translation_input_records"][0]["fields"]
    assert not fields["product_details" if label else "specification_es"]["translate_allowed"]


@pytest.mark.parametrize("text", [
    "Capacidad: 500 ml / 0,5 L",
    "Contenido de la caja: usar 5 ml por cada 40 L; después 5 ml por cada 80 L",
    "Capacidad: 600 ml; dosis: 5 ml / 40 L",
    "Capacidad: 600 ml / Dimensiones: 5l. x 5an. x 20al. cm",
])
def test_capacity_scopes_accept_equivalence_and_exclude_other_facts(text):
    assert "CONTRADICTORY_CAPACITY" not in numeric_profile(text)["issues"]


@pytest.mark.parametrize("sample", FIXTURE["structured"], ids=lambda x: x["asin"])
def test_production_preclean_uses_items_and_preserves_source_boundaries(sample):
    original = {"asin": sample["asin"], "attributes": sample["attributes"],
                "detail_bullets_raw": sample["detail_bullets_raw"],
                "product_details_es": "Dimensiones del producto: 10 mm\nDimensiones del producto: 1 cm"}
    snapshot = deepcopy(original)
    production_input = build_production_input([original])
    rows = records_for_preclean(production_input)
    audit = audit_records(rows)
    row = audit["translation_input_records"][0]
    field = row["fields"]["product_details"]
    assert "CONFLICTING_DUPLICATE" not in field["issues"]
    assert [(x["section"], x["source"], x["position"]) for x in row["details_structured"]] == [
        (x["section"], x["source"], x["position"]) for x in sample["attributes"]]
    assert row["detail_source_evidence"]["detail_bullets_raw"] == sample["detail_bullets_raw"]
    if sample["asin"] == "B07CTK4TH4":
        assert "CROSS_SOURCE_DETAIL_DIFFERENCE" not in field["issues"]
        assert field["translate_allowed"]
    if sample["asin"] == "B00083CZCK":
        assert "CROSS_SOURCE_DETAIL_DIFFERENCE" in field["issues"]
        assert not field["translate_allowed"]
        assert audit["review_queue"]
    assert original == snapshot
    assert production_input["records"][0]["source_record"]["product_details"] == original["product_details_es"]


def test_structured_duplicate_conflicts_keep_their_own_surface_and_legacy_still_blocks():
    items = [{"section": "technical_details", "source": "prodDetails", "position": n,
              "label_raw": "Capacidad", "value_raw": value} for n, value in enumerate(("900 ml", "600 ml"))]
    parsed = parse_details(items)
    assert "CONFLICTING_DUPLICATE" in parsed["issues"]
    assert parsed["status"] == "NEEDS_REVIEW"
    legacy = parse_details("Modelo: X1\nModelo: X2")
    assert legacy["status"] == "NEEDS_REVIEW"


def test_preclean_does_not_reflow_owner_excluded_attributes():
    excluded = {"label_raw": "Capacidad", "value_raw": "2 kg", "section": "technical_details", "position": 0}
    eligible = {"label_raw": "Material", "value_raw": "Acero", "section": "technical_details", "position": 1}
    raw = {"asin": "B000000020", "attributes": [excluded, eligible], "eligibleattributes": [eligible],
           "owner_attribute_exclusions": {"schema_version": "owner-current-source-attribute-exclusion-v1"},
           "product_details_es": "Capacidad: 2 kg\nMaterial: Acero"}
    row = records_for_preclean(build_production_input([raw]))[0]
    assert row["product_details"] == [eligible]
    assert row["attributes"] == [excluded, eligible]


def test_different_capacity_subjects_are_not_compared():
    assert "CONTRADICTORY_CAPACITY" not in numeric_profile(
        "Capacidad del depósito: 600 ml / Volumen del paquete: 10 L")["issues"]


def test_cross_section_same_label_conflict_is_reviewed_with_both_surfaces():
    items = [{"label_raw": "Capacidad", "value_raw": value, "section": section,
              "source": "prodDetails", "position": index} for index, (value, section) in enumerate([
                  ("900 ml", "product_overview"), ("600 ml", "technical_details")])]
    parsed = parse_details(items)
    assert "CROSS_SOURCE_DETAIL_DIFFERENCE" in parsed["issues"]
    assert parsed["status"] == "NEEDS_REVIEW"
    assert len(parsed["rows"]) == 2
