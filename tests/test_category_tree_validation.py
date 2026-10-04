from amazon_es_bestseller.categories.graph import CategoryPlacementGraph, placement_id_for
from amazon_es_bestseller.categories.models import AmazonCategoryPlacement


def test_missing_parent_and_path_mismatch_are_reported():
    row = AmazonCategoryPlacement(
        placement_id=placement_id_for("ES", ("root", "child")), marketplace="ES",
        category_id="child", category_name="Child", canonical_url="", source_url="",
        parent_placement_id="pl_missing", parent_category_id="root", depth=3,
        category_path=("root", "child"), category_name_path=("Root", "Child"),
        first_seen_at="", last_seen_at="")
    errors = CategoryPlacementGraph([row]).validate()
    assert any("missing parent" in error for error in errors)
    assert any("depth/path mismatch" in error for error in errors)
