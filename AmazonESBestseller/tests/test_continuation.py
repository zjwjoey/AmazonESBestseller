from amazon_es_bestseller.continuation import select_missing_detail_rows


def test_select_missing_detail_rows_keeps_only_unique_rows_without_saved_details():
    rows = [
        {"asin": "B012345678", "product_url": "https://www.amazon.es/dp/B012345678", "details_json": "{}"},
        {"asin": "B012345679", "product_url": "https://www.amazon.es/dp/B012345679", "details_json": ""},
        {"asin": "B012345679", "product_url": "https://www.amazon.es/dp/B012345679", "details_json": ""},
        {"asin": "", "product_url": "https://www.amazon.es/dp/B012345680", "details_json": ""},
    ]

    selected = select_missing_detail_rows(rows)

    assert [row["asin"] for row in selected] == ["B012345679"]
