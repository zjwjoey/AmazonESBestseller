"""Archived 1890-pool regressions: scopes are not interchangeable numbers."""

from copy import deepcopy

import pytest

from amazon_es_bestseller.quality.source_fields import audit_source_fields


def finding(asin, title, variant, label, value, bullets=()):
    row = {
        "asin": asin,
        "product_url": f"https://www.amazon.es/dp/{asin}",
        "title_es_raw": title,
        "selected_variation_raw": variant,
        "attributes": [
            {
                "section": "product_overview",
                "label_raw": label,
                "value_raw": value,
                "position": 0,
                "source": "productOverview",
            }
        ],
        "feature_bullets_raw": list(bullets),
    }
    original = deepcopy(row)
    audit = audit_source_fields([row])
    assert row == original
    return [i for i in audit["issues"] if i["issue_code"] == "SOURCE_SEMANTIC_CONFLICT"]


@pytest.mark.parametrize(
    "asin,title,variant,value,bullets",
    [
        (
            "B0DQ1MZVSX",
            "Cubo de Basura Reciclaje 3 Compartimentos 3x15L",
            "3 X 15L",
            "45 litros",
            (),
        ),
        (
            "B0G46PL7RN",
            "Cubo de Basura de Cocina, 2 x 30 L",
            "Plateado",
            "60 litros",
            (),
        ),
        (
            "B0DK9HXNMP",
            "Fairy MaxPower Líquido Lavavajillas 8 x 450ml",
            "3.6 l (Paquete de 1)",
            "3.6 l",
            (),
        ),
        (
            "B01MUH4GT0",
            "Cubo de Reciclaje Doble: 2 Compartimentos de 15 L",
            "",
            "30 litros",
            ("Cada cubo posee una capacidad de 15 l.",),
        ),
        (
            "B0DN23H45W",
            "Philips filtro capacidad de filtración de 1200 L",
            "",
            "1200 litros",
            (
                "MAYOR CAPACIDAD DE FILTRACIÓN: Con una capacidad de 1.200L, o más de 7 meses de uso.",
            ),
        ),
        (
            "B09GKKFLDT",
            "Dispensador compatible con 1.5L, 5.7L, 10L, 11.3L, 15L, 18.9L",
            "",
            "10 litros",
            (
                "Compatible con garrafas disponible para una capacidad de 1.5L, 5.7L, 10L, 11.3L, 15L, 18.9L.",
            ),
        ),
    ],
)
def test_explicit_same_source_scope_equivalences_are_not_conflicts(
    asin, title, variant, value, bullets
):
    assert not finding(asin, title, variant, "Capacidad", value, bullets)


@pytest.mark.parametrize(
    "value,variant",
    [("35 litros", "25L"), ("18 litros", "1,8L"), ("60 litros", "3 x 15L")],
)
def test_non_equivalent_capacity_stays_flagged(value, variant):
    assert finding("B00URUR7XG", "Cubo de basura", variant, "Capacidad", value)


def test_source_wrong_label_is_not_rewritten_or_exempted():
    assert finding(
        "B00XAEBRPK",
        "Colon detergente en polvo 7.037 kg",
        "7.04 kg (Paquete de 1)",
        "Volumen Líquido",
        "7 Kilogramos",
    )


def test_liquid_volume_label_synonyms_classify_the_same_source_anomaly():
    assert finding(
        "B00XAEBRPK",
        "Colon detergente en polvo 7.037 kg",
        "7.04 kg (Paquete de 1)",
        "Volumen de líquido",
        "7 Kilogramos",
    )


def test_compartment_arithmetic_does_not_excuse_unrelated_bullet():
    assert finding(
        "B01MUH4GT0",
        "Cubo 2 x 15 L",
        "",
        "Capacidad",
        "30 litros",
        ("Capacidad: 20 litros",),
    )
