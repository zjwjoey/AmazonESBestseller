import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.protection import ProtectedText
from amazon_es_bestseller.translation.validators import _unit_keys, _units, validate_translation


FIXTURE = Path(__file__).parent / "fixtures/translation/mixed_axis_units_validator.json"


def saved_case():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_saved_page_mixed_axis_units_are_kept_in_axis_order():
    case = saved_case()
    source_units = _unit_keys(_units(case["source"]))
    correct_units = _unit_keys(_units(case["correct_translation"]))
    first_axis_wrong_units = _unit_keys(_units(case["first_axis_wrong_translation"]))
    swapped_units = _unit_keys(_units(case["swapped_axes_translation"]))

    assert source_units == correct_units
    assert source_units != first_axis_wrong_units
    assert source_units != swapped_units


@pytest.mark.parametrize("result_key", [
    "first_axis_wrong_translation",
    "swapped_axes_translation",
])
def test_saved_page_mixed_axis_unit_errors_are_reported(result_key):
    case = saved_case()
    issues = validate_translation(
        ProtectedText(text=case["source"]),
        case[result_key],
        case["source"],
        field="specification",
    )
    assert any(issue["code"] == "UNIT_MISMATCH" for issue in issues)


@pytest.mark.parametrize(("source", "translated"), [
    ("10 cm x 20 cm", "10×20厘米"),
    ("10 x 20 cm", "10厘米×20厘米"),
    ("45 l. x 32 an. x 20 al. centímetros", "45×32×20厘米"),
])
def test_same_axis_units_shared_trailing_unit_and_axis_abbreviations_remain_valid(
        source, translated):
    issues = validate_translation(
        ProtectedText(text=source), translated, source, field="specification")
    assert not any(issue["code"] == "UNIT_MISMATCH" for issue in issues)


def test_kg_and_liter_swap_is_still_rejected():
    source = "1 kg y 3 L"
    issues = validate_translation(
        ProtectedText(text=source), "1升和3千克", source, field="product_details")
    assert any(issue["code"] == "UNIT_MISMATCH" for issue in issues)
