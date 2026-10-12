"""Offline source fixtures: never guess away ambiguity or identity conflicts."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from amazon_es_bestseller.normalization.specification import build_spec_es
from amazon_es_bestseller.quality.source_fields import (
    _measure_signatures,
    audit_source_fields,
)
from amazon_es_bestseller.translation.full_detail import (
    render_bullets_es,
    render_details_es,
)

CASES = json.loads(
    (
        Path(__file__).parent
        / "fixtures/spanish_candidate_program_regressions_20261010.json"
    ).read_text(encoding="utf-8")
)


def issues(row):
    return audit_source_fields([row])["issues"]


def spec(row):
    return build_spec_es(
        row.get("attributes"),
        variant=row.get("selected_variation_raw"),
        title_es=row.get("title_es_raw"),
    )


def test_exact_grinder_equivalent_mass_is_not_a_unit_error():
    assert not [
        i
        for i in issues(CASES["equivalent_grinder"])
        if i["issue_code"]
        in {
            "UNIT_SEMANTICS_AMBIGUOUS",
            "SPEC_UNIT_TYPE_MISMATCH",
            "SOURCE_SEMANTIC_CONFLICT",
        }
    ]


@pytest.mark.parametrize(
    "label,value",
    [
        ("Capacidad de eliminación de humedad", "12 Litros por día"),
        ("Capacidad mínima de peso", "6 Kilogramos"),
        ("Capacidad máxima de peso", "20 kg"),
    ],
)
def test_explicit_rate_and_load_labels_are_not_generic_capacity(label, value):
    row = {
        "asin": "B000000001",
        "title_es_raw": "Deshumidificador o soporte",
        "attributes": [{"label_raw": label, "value_raw": value}],
    }
    assert not [
        i
        for i in issues(row)
        if i["issue_code"] in {"UNIT_SEMANTICS_AMBIGUOUS", "SPEC_UNIT_TYPE_MISMATCH"}
    ]


def test_package_count_has_same_asin_variant_support():
    assert not [
        i
        for i in issues(CASES["package"])
        if i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS"
    ]


def test_rates_keep_numeric_signatures_and_do_not_false_correlate():
    assert (
        _measure_signatures("16 L/d")
        == _measure_signatures("16 liters_per_day")
        == _measure_signatures("16 Litros por día")
    )
    assert not _measure_signatures("16 L/d") & _measure_signatures("20 Litros por día")


def test_measure_source_fragments_survive_unicode_prefixes_and_accents():
    from amazon_es_bestseller.normalization.measure_evidence import measurements

    assert [
        fragment for _, _, fragment in measurements("KNKA – Cómodo, 16 Litros por día")
    ] == ["16 Litros por día"]
    assert [fragment for _, _, fragment in measurements("Capacidad™: 1 L")] == ["1 L"]
    assert [fragment for _, _, fragment in measurements("2 L + 3 L")] == ["2 L", "3 L"]


@pytest.mark.parametrize("case", ["grinder", "dehumidifier", "cleaner"])
def test_cross_field_conflicts_are_specific_and_raw_stays_unchanged(case):
    row = deepcopy(CASES[case])
    before = deepcopy(row)
    findings = [i for i in issues(row) if i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT"]
    assert any(
        i.get("evidence", {}).get("match_kind") == "cross_field_measure_conflict"
        for i in findings
    )
    assert all(i.get("evidence", {}).get("same_asin_record_binding") for i in findings)
    assert row == before


def test_title_capacity_beats_bad_generic_attribute_without_modifying_raw():
    row = deepcopy(CASES["grinder"])
    before = deepcopy(row)
    assert "75 gramos" in spec(row) and "80 g" not in spec(row)
    assert row == before


def test_selected_capacity_beats_title_and_generic_attribute():
    row = {
        "attributes": [{"label_raw": "Capacidad", "value_raw": "20 litros"}],
        "selected_variation_raw": "30L",
        "title_es_raw": "Cubo Capacidad 25 L",
    }
    assert (
        "30L" in spec(row) and "20 litros" not in spec(row) and "25 L" not in spec(row)
    )


def test_selected_count_overrides_generic_count_but_does_not_drop_other_facts():
    row = {
        "attributes": [
            {"label_raw": "Número de productos", "value_raw": "12"},
            {"label_raw": "Potencia", "value_raw": "50 W"},
        ],
        "selected_variation_raw": "6 Count",
        "title_es_raw": "Pack de 4 unidades",
    }
    out = spec(row)
    assert "6 Count" in out and "12" not in out and "50 W" in out


def test_selected_dimensions_override_title_and_generic_table_dimensions():
    row = {
        "attributes": [
            {"label_raw": "Dimensiones del producto", "value_raw": "10 x 10 cm"}
        ],
        "selected_variation_raw": "20 x 30 cm",
        "title_es_raw": "Mantel 40 x 50 cm",
    }
    assert "20 x 30 cm" in spec(row) and "10 x 10 cm" not in spec(row)


def test_single_pack_variant_does_not_fall_back_to_generic_twelve_count():
    row = {
        "attributes": [
            {"label_raw": "Número de productos", "value_raw": "12"},
            {"label_raw": "Capacidad", "value_raw": "118 ml"},
        ],
        "selected_variation_raw": "118 ml (Paquete de 1)",
        "title_es_raw": "Producto",
    }
    assert "12" not in spec(row) and "118 ml" in spec(row)


def test_explicit_load_label_wrong_volume_is_not_accepted_as_a_load():
    row = {
        "asin": "B000000001",
        "attributes": [
            {"label_raw": "Capacidad máxima de peso", "value_raw": "20 litros"}
        ],
    }
    assert any(
        i["issue_code"] in {"SPEC_UNIT_TYPE_MISMATCH", "SOURCE_SEMANTIC_CONFLICT"}
        for i in issues(row)
    )


def test_dimension_does_not_change_capacity_and_mixed_volume_options_remain_raw():
    row = {
        "attributes": [{"label_raw": "Capacidad", "value_raw": "5 litros"}],
        "selected_variation_raw": "20 x 30 cm",
        "title_es_raw": "Cubo",
    }
    assert "5 litros" in spec(row)
    row["selected_variation_raw"] = "2 L + 3 L"
    assert spec(row) == "Capacidad: 5 litros"


def test_slash_volume_alternatives_do_not_select_only_the_last_option():
    row = {
        "attributes": [{"label_raw": "Capacidad", "value_raw": "500 ml"}],
        "title_es_raw": "Sparrow Botella 500ml/750ml/1L/1.2L",
    }
    assert "500 ml" in spec(row) and "1.2L" not in spec(row)
    assert not any(
        i.get("evidence", {}).get("match_kind") == "cross_field_measure_conflict"
        for i in issues({**row, "asin": "B000000001"})
    )


def test_spaced_decimal_comma_is_not_thirty_five_litres():
    row = {
        "asin": "B07MRPFQTQ",
        "title_es_raw": "Le Creuset Taza, 0, 35 L, Cereza",
        "attributes": [{"label_raw": "Tamaño", "value_raw": "350 ml"}],
    }
    assert spec(row) == "Tamaño: 350 ml"
    assert not any(
        i.get("evidence", {}).get("match_kind") == "cross_field_measure_conflict"
        for i in issues(row)
    )


@pytest.mark.parametrize(
    "source, amount",
    [
        ("Krosno Vasos Juego de 6, 430 ml", "430 ml"),
        ("1. 25L Gris Metalizado", "25L"),
        ("Acero Inox 18/10, 800ml", "800ml"),
        ("Vasos (200, 200ml)", "200ml"),
    ],
)
def test_count_and_option_punctuation_is_not_a_decimal_measure(source, amount):
    from amazon_es_bestseller.normalization.measure_evidence import measurements

    assert [fragment for _, _, fragment in measurements(source)] == [amount]


def test_cubic_centimetres_keep_equivalent_volume_signature():
    assert _measure_signatures("250 cc") == _measure_signatures("250 ml")


@pytest.mark.parametrize(
    "value", ["35an x 600L cm", "41.9l x 29.5an cm", "40L x 30A x 25A cm"]
)
def test_named_axes_with_length_unit_are_dimensions_not_litres(value):
    row = {
        "asin": "B000000001",
        "attributes": [{"label_raw": "Tamaño", "value_raw": value}],
    }
    assert not any(i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS" for i in issues(row))


def test_shared_unit_compatibility_list_is_not_a_single_capacity():
    row = {
        "asin": "B089WHTJBG",
        "title_es_raw": "Dispensador para garrafas de 5, 8, 10, 13 y 20 litros",
        "attributes": [{"label_raw": "Capacidad", "value_raw": "18,9 litros"}],
    }
    assert spec(row) == "Capacidad: 18,9 litros"
    assert not any(
        i.get("evidence", {}).get("match_kind") == "cross_field_measure_conflict"
        for i in issues(row)
    )


def test_dimension_axis_L_is_not_litres_and_generic_bullet_is_not_capacity():
    row = {
        "asin": "B000000001",
        "attributes": [{"label_raw": "Capacidad", "value_raw": "30 litros"}],
        "selected_variation_raw": "Juego de 3 (40L x 30A x 25A cm) / Beige Arena",
        "feature_bullets_raw": ["Incluye 2 tuppers herméticos de 0,57L"],
    }
    assert "30 litros" in spec(row) and "40L" not in spec(row)
    assert not any(
        i.get("evidence", {}).get("match_kind") == "cross_field_measure_conflict"
        for i in issues(row)
    )


def test_daily_output_prose_does_not_conflict_with_tank_volume():
    row = {
        "asin": "B000000001",
        "attributes": [{"label_raw": "Capacidad", "value_raw": "2,5 litros"}],
        "feature_bullets_raw": ["Elimina hasta 16 litros de humedad del aire al día"],
    }
    assert not any(
        i.get("evidence", {}).get("match_kind") == "cross_field_measure_conflict"
        for i in issues(row)
    )


def test_title_volume_does_not_leave_a_contradictory_generic_size_in_core_spec():
    out = spec(CASES["cleaner"])
    assert "1 litros" in out or "1 Litro" in out
    assert "1 ml" not in out


def test_foreign_identity_and_true_source_damage_are_not_guessed():
    assert "Port�til" in render_details_es(CASES["damaged"]["attributes"])
    assert any(
        i["issue_code"] == "MISPLACED"
        for i in issues(
            {
                **CASES["damaged"],
                "product_details_es": render_details_es(CASES["damaged"]["attributes"]),
            }
        )
    )
    assert any(
        i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT"
        for i in issues(CASES["source_label_conflict"])
    )


def test_only_identical_language_tokens_are_compacted_in_the_view():
    row = deepcopy(CASES["language"])
    before = deepcopy(row)
    assert render_details_es(row["attributes"]) == "Idioma: Inglés"
    assert "Inglés, Español" in render_details_es(
        [{"label_raw": "Idioma", "value_raw": "Inglés, Español"}]
    )
    assert "Rojo, Rojo" in render_details_es(
        [{"label_raw": "Color", "value_raw": "Rojo, Rojo"}]
    )
    assert row == before


def test_literal_bullet_html_is_presentation_only_and_order_is_preserved():
    bullets = [
        "<b>Nota:</b> No es impermeable.<br>95 % poliéster",
        "Capacidad: 5 litros",
    ]
    before = deepcopy(bullets)
    assert (
        render_bullets_es(bullets)
        == "Nota: No es impermeable. 95 % poliéster\nCapacidad: 5 litros"
    )
    assert bullets == before


def test_unknown_capacity_and_incompatible_rate_remain_reviewable():
    for label, value in [
        ("Capacidad", "400 g"),
        ("Capacidad de caudal de aire", "10 Litros por día"),
    ]:
        assert any(
            i["issue_code"] == "UNIT_SEMANTICS_AMBIGUOUS"
            for i in issues(
                {
                    "asin": "B000000001",
                    "title_es_raw": "Producto",
                    "attributes": [{"label_raw": label, "value_raw": value}],
                }
            )
        )
