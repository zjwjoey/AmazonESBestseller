"""Saved-source pack labels must not be confused with physical dimensions."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.quality.source_fields import audit_source_fields

CASES = json.loads((Path(__file__).parent / "fixtures/source_explicit_pack_names_20261011.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("source", CASES, ids=lambda r: r["asin"])
def test_explicit_package_names_match_exact_same_source_counts(source):
    row = {k: v for k, v in source.items() if k not in {"value", "label"}}
    row["attributes"] = [{"section": "technical_details", "position": 0, "source": "prodDetails",
                          "label_raw": source.get("label", "Nombre del tamaño del paquete"), "value_raw": source["value"]}]
    before = deepcopy(row)
    audit = audit_source_fields([row])
    assert not any(i["issue_code"] in {"UNIT_SEMANTICS_AMBIGUOUS", "SOURCE_SEMANTIC_CONFLICT"} for i in audit["issues"])
    assert row == before


@pytest.mark.parametrize("title,selected,value", [
    ("Pack de 6", "1 unidad (Paquete de 1)", "Paquete de 6 unidades"),
    ("Pack de 6", "2 unidades (Paquete de 6)", "Paquete de 6 unidades"),
    ("6 Rollos", "4 Rollos", "Paquete de 6 unidades"),
    ("6 años de garantía", "", "Paquete de 6 unidades"),
    ("Dispensador para 6 rollos", "", "Paquete de 6 unidades"),
    ("Organizador compatible con 12 rotuladores", "", "Paquete de 12 unidades"),
    ("2 unidades, Pack de 6 accesorios", "", "Paquete de 12 unidades"),
    ("Pack de 2", "0.5 unidad (Paquete de 2)", "Paquete de 2 unidades"),
    ("Pack de 6 o Pack de 12", "", "Paquete de 6 unidades"),
    ("Pack de 6", "", "Paquete de 6 unidades o 12 unidades"),
    ("Pack de 6", "", "6 Pack x 12"),
    ("4 x 50 cm", "", "4 unidades"),
    ("60 Sticks x 14g", "30 Sticks x 14g", "840 Gramos"),
])
def test_conflicting_or_nonpackage_evidence_remains_unresolved(title, selected, value):
    row = {"asin": "B000000001", "title_es_raw": title, "selected_variation_raw": selected,
           "attributes": [{"label_raw": "Tamaño", "value_raw": value}]}
    assert any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in audit_source_fields([row])["issues"])


def test_inline_pack_count_is_not_multiplied_by_itself():
    row = {"asin": "B000000001", "title_es_raw": "Pack de 6 unidades",
           "attributes": [{"label_raw": "Tamaño", "value_raw": "Paquete de 6 unidades"}]}
    assert not any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in audit_source_fields([row])["issues"])
