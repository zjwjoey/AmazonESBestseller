from amazon_es_bestseller.categories.graph import placement_id_for


def test_placement_id_is_stable_and_marketplace_aware():
    assert placement_id_for("ES", ("root", "x")) == placement_id_for("ES", ("root", "x"))
    assert placement_id_for("ES", ("root", "x")) != placement_id_for("DE", ("root", "x"))
    assert placement_id_for("ES", ("root", "x")) != placement_id_for("ES", ("x", "root"))
