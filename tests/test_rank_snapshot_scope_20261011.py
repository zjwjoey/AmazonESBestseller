from amazon_es_bestseller.quality.source_fields import _sf_rankings


def context(asin, **scope):
    return {"asin": asin, "ranking_contexts": [{
        "ranking_source_url": "https://www.amazon.es/gp/bestsellers/tools/",
        "ranking_page_number": 1, "bestseller_rank": 2,
        "browse_node_id": "123", **scope,
    }]}


def duplicates(rows):
    issues, fields = [], []
    _sf_rankings(rows, issues, fields, ranking_matrix=[])
    return [issue for issue in issues if issue["issue_code"] == "DUPLICATE_RANK_SLOT"]


def test_distinct_recorded_snapshots_do_not_collide():
    assert not duplicates([context("B000000001", snapshot_id="oct6"),
                           context("B000000002", snapshot_id="oct11")])


def test_same_snapshot_collision_stays_blocked():
    assert len(duplicates([context("B000000001", snapshot_id="oct6"),
                           context("B000000002", snapshot_id="oct6")])) == 2


def test_actual_observation_scope_is_preserved():
    assert not duplicates([context("B000000001", collected_at="2026-10-06T12:00:00Z"),
                           context("B000000002", collected_at="2026-10-11T12:00:00Z")])


def test_unknown_snapshot_cannot_escape_a_collision():
    assert len(duplicates([context("B000000001"),
                           context("B000000002", snapshot_id="oct6")])) == 2


def test_repeated_same_asin_same_snapshot_stays_flagged():
    assert duplicates([context("B000000001", snapshot_id="oct6"),
                       context("B000000001", snapshot_id="oct6")])
