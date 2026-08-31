# -*- coding: utf-8 -*-
"""Offline pressure smoke for the long-run 4k–5k delivery target."""

from amazon_es_bestseller.export.excel import export_workbook


def test_business_export_handles_5000_unique_asins_offline():
    records = [{
        "asin": f"B{i:09d}",
        "title_es_raw": f"Producto {i}",
        "current_price_raw": "9,99 €",
        "product_url": f"https://www.amazon.es/dp/B{i:09d}",
    } for i in range(1, 5001)]
    wb = export_workbook(records, profile="business")
    assert wb.sheetnames == ["西班牙语选品清单", "中文选品清单"]
    assert wb["西班牙语选品清单"].max_row == 5001
    assert wb["中文选品清单"].max_row == 5001
    assert wb["西班牙语选品清单"].cell(2, 2).value == "B000000001"
    assert wb["中文选品清单"].cell(5001, 3).value == "B000005000"
