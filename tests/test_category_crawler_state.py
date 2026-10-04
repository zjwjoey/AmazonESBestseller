from amazon_es_bestseller.categories.crawler import CategoryCrawler, CategoryCrawlerState
from amazon_es_bestseller.categories.models import PlacementStatus


class AccessBlocked(RuntimeError):
    kind = "CAPTCHA"


def test_access_block_is_persisted_as_blocked(tmp_path):
    state = CategoryCrawlerState(tmp_path / "state.json")
    crawler = CategoryCrawler(state, lambda _row: (_ for _ in ()).throw(AccessBlocked("captcha")))
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://www.amazon.es/root")
    crawler.run()
    row = next(iter(state.graph.placements.values()))
    assert row.status is PlacementStatus.BLOCKED
    resumed = CategoryCrawlerState(tmp_path / "state.json")
    assert next(iter(resumed.graph.placements.values())).status is PlacementStatus.BLOCKED


def test_valid_tree_publishes_authoritative_graph(tmp_path):
    state = CategoryCrawlerState(tmp_path / "state.json")
    crawler = CategoryCrawler(state, lambda _row: {"children": []})
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://www.amazon.es/root")
    result = crawler.run()
    path = result["latest_authoritative_category_graph"]
    assert result["tree_valid"] is True
    assert path is not None and path.exists()
