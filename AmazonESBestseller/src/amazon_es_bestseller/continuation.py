import json

from .detail_parser import ProductDetail


def select_missing_detail_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return one target per SKU that has no persisted parsed-detail JSON."""
    seen_asins: set[str] = set()
    selected = []
    for row in rows:
        asin = row.get("asin", "")
        if not asin or not row.get("product_url") or row.get("details_json") or asin in seen_asins:
            continue
        seen_asins.add(asin)
        selected.append(row)
    return selected


def apply_detail_to_row(row: dict[str, str], detail: ProductDetail) -> None:
    row["parent_asin"] = detail.parent_asin or ""
    row["parent_asin_status"] = (
        "self_reported_unconfirmed"
        if detail.parent_asin == row["asin"]
        else "confirmed"
        if detail.parent_asin
        else "not_observed"
    )
    brand = detail.details_json.get("brand")
    if isinstance(brand, str):
        row["brand"] = brand
    row["details_json"] = json.dumps(detail.details_json, ensure_ascii=False, sort_keys=True)
    row["details"] = detail.details or ""
    row["specification"] = detail.specification or ""
    row["date_first_available"] = detail.date_first_available or ""
    row["date_first_available_raw"] = detail.date_first_available_raw or ""
