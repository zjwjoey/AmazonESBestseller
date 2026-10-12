from amazon_es_bestseller.normalization.measure_scope import package_quantity_proof


def test_exact_box_count_matches_explicit_selected_outer_pack():
    row = {"asin": "B0017YOQ44", "title_es_raw": "STABILO - marcador fluorescente - BOSS ORIGINAL",
           "selected_variation_raw": "Amarillo / 1 unidad (Paquete de 10)"}
    proof = package_quantity_proof(row, "Caja de 10 unidades")
    assert proof and proof["signature"] == "10pcs"


def test_different_box_count_remains_unproved():
    row = {"selected_variation_raw": "1 unidad (Paquete de 5)"}
    assert package_quantity_proof(row, "Caja de 10 unidades") is None


def test_box_count_is_not_weight_and_cannot_use_unrelated_title_number():
    assert package_quantity_proof({"title_es_raw": "Marcador modelo 10"}, "Caja de 10 unidades") is None
    assert package_quantity_proof({"selected_variation_raw": "1 unidad (Paquete de 10)"}, "Caja de 10 gramos") is None
