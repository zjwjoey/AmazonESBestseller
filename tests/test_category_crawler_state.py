from amazon_es_bestseller.categories.crawler import CategoryCrawler, CategoryCrawlerState
from amazon_es_bestseller.categories.models import PlacementStatus


class AccessBlocked(RuntimeError):
    kind = "CAPTCHA"


def test_access_block_is_persisted_as_blocked(tmp_path):
    state = CategoryCrawlerState(tmp_path / "state.json")
    crawler = CategoryCrawler(state, lambda _row: (_ for _ in ()).throw(AccessBlocked("captcha")))
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://www.amazon.es/root")
    result = crawler.run()
    row = next(iter(state.graph.placements.values()))
    assert row.status is PlacementStatus.BLOCKED
    assert result["crawl_complete"] is False
    assert result["latest_authoritative_category_graph"] is None
    resumed = CategoryCrawlerState(tmp_path / "state.json")
    assert next(iter(resumed.graph.placements.values())).status is PlacementStatus.BLOCKED


def test_access_block_stops_before_next_pending_placement(tmp_path):
    state = CategoryCrawlerState(tmp_path / "state.json")
    calls = []

    def fetch(row):
        calls.append(row.category_id)
        if row.category_id == "root":
            return {"children": [
                {"category_id": "blocked-child", "category_name": "Blocked"},
                {"category_id": "unvisited-child", "category_name": "Unvisited"},
            ]}
        if row.category_id == "blocked-child":
            raise AccessBlocked("captcha")
        raise AssertionError("访问受限后不应继续请求其他 placement")

    crawler = CategoryCrawler(state, fetch)
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://www.amazon.es/root")
    result = crawler.run()

    assert calls == ["root", "blocked-child"]
    assert result["crawl_complete"] is False
    statuses = {row.category_id: row.status for row in state.graph.placements.values()}
    assert statuses["blocked-child"] is PlacementStatus.BLOCKED
    assert statuses["unvisited-child"] is PlacementStatus.PENDING


def test_valid_tree_publishes_authoritative_graph(tmp_path):
    state = CategoryCrawlerState(tmp_path / "state.json")
    crawler = CategoryCrawler(state, lambda _row: {"children": []})
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://www.amazon.es/root")
    result = crawler.run()
    path = result["latest_authoritative_category_graph"]
    assert result["tree_valid"] is True
    assert path is not None and path.exists()


def test_incomplete_tree_never_publishes_authoritative_graph(tmp_path):
    state = CategoryCrawlerState(tmp_path / "state.json")
    crawler = CategoryCrawler(state, lambda _row: {"children": []})
    crawler.seed(marketplace="ES", category_id="root", category_name="Root",
                 canonical_url="https://www.amazon.es/root")
    result = crawler.run(max_placements=0)
    assert result["tree_valid"] is True
    assert result["crawl_complete"] is False
    assert result["latest_authoritative_category_graph"] is None
    assert not (tmp_path / "latest_authoritative_category_graph.json").exists()
