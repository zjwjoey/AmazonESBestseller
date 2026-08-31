from amazon_es_bestseller.qa.reconcile import reconcile_task


def test_reconcile_reports_missing_extra_and_duplicates():
    report = reconcile_task(
        {"exact_asins": ["B000000001", "B000000002"]},
        [{"asin": "B000000001"}, {"asin": "B000000001"}],
        [{"asin": "B000000001"}, {"asin": "B000000003"}],
        workbook_asins=["B000000001"],
    )
    assert report["status"] == "partial"
    assert report["stages"]["items"]["duplicates"] == ["B000000001"]
    assert report["stages"]["products"]["missing"] == ["B000000002"]
    assert report["stages"]["products"]["extra"] == ["B000000003"]


def test_category_quota_reconcile_uses_target_count_without_fake_asin_set():
    report = reconcile_task(
        {"selection_mode": "category_quota", "target_unique": 3},
        [{"asin": "B000000001"}, {"asin": "B000000002"}],
        [{"asin": "B000000001"}],
    )
    assert report["target_mode"] == "category_quota"
    assert report["target_count"] == 3
    assert report["stages"]["items"]["shortfall"] == 1
    assert report["stages"]["products"]["shortfall"] == 2
    assert report["status"] == "partial"
