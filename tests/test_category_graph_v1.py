from pathlib import Path

from amazon_es_bestseller.categories.crawler import CategoryCrawler, CategoryCrawlerState
from amazon_es_bestseller.categories.graph import CategoryPlacementGraph, placement_id_for
from amazon_es_bestseller.categories.models import AmazonCategoryPlacement, PlacementStatus


def _placement(marketplace, path, names, parent=None):
    return AmazonCategoryPlacement(
        placement_id=placement_id_for(marketplace, path), marketplace=marketplace,
        category_id=path[-1], category_name=names[-1], canonical_url="https://example.test",
        parent_placement_id=parent, parent_category_id=path[-2] if len(path) > 1 else None,
        depth=len(path) - 1, category_path=tuple(path), category_name_path=tuple(names),
        first_seen_at="2026-10-04T00:00:00+00:00", last_seen_at="2026-10-04T00:00:00+00:00",
    )


def test_same_category_id_can_have_distinct_placements():
    root = _placement("ES", ("root",), ("Root",))
    left = _placement("ES", ("root", "left"), ("Root", "Left"), root.placement_id)
    right = _placement("ES", ("root", "right"), ("Root", "Right"), root.placement_id)
    leaf_left = _placement("ES", ("root", "left", "shared"), ("Root", "Left", "Shared"), left.placement_id)
    leaf_right = _placement("ES", ("root", "right", "shared"), ("Root", "Right", "Shared"), right.placement_id)
    graph = CategoryPlacementGraph([root, left, right, leaf_left, leaf_right])
    assert leaf_left.placement_id != leaf_right.placement_id
    assert len(graph.by_category_id("shared")) == 2
    assert graph.validate() == []


def test_category_crawler_persists_and_resumes(tmp_path: Path):
    state = CategoryCrawlerState(tmp_path / "category_state.json")
    calls = []

    def fetch(parent):
        calls.append(parent.category_id)
        if parent.category_id == "root":
            return {"children": [{"category_id": "child", "category_name": "Child"}]}
        return {"children": []}

    crawler = CategoryCrawler(state, fetch)
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://example.test/root")
    result = crawler.run()
    assert result["tree_valid"] is True
    assert calls == ["root", "child"]
    assert all(row.status == PlacementStatus.DONE for row in state.graph.placements.values())

    resumed = CategoryCrawlerState(tmp_path / "category_state.json")
    assert len(resumed.graph.placements) == 2
    assert all(row.status == PlacementStatus.DONE for row in resumed.graph.placements.values())
