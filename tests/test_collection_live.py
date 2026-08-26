# -*- coding: utf-8 -*-
"""collection 联网包装测试：默认跳过，仅 RUN_LIVE=1 时执行。

默认测试绝不联网；此文件只在显式 RUN_LIVE 时启动真实浏览器串行采集。
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_LIVE"),
    reason="联网采集需要显式 RUN_LIVE=1",
)


def test_collect_rankings_live(tmp_path):
    from amazon_es_bestseller.access.browser import BrowserSession
    from amazon_es_bestseller.collection.ranking import collect_rankings

    with BrowserSession() as session:
        records = collect_rankings(
            ["https://www.amazon.es/gp/bestsellers/hogar-y-cocina/"],
            session, str(tmp_path))
    assert isinstance(records, list)
    for r in records:
        assert r["asin"]
        assert r["ranking_source_url"].startswith("https://")
    # 原始 HTML 已落盘
    html_dirs = [p for p in tmp_path.glob("runs/*/html/*.html")]
    assert len(html_dirs) >= 1


def test_collect_details_live(tmp_path):
    from amazon_es_bestseller.access.browser import BrowserSession
    from amazon_es_bestseller.collection.detail import collect_details

    with BrowserSession() as session:
        details = collect_details(["B078C6QR1C"], session, str(tmp_path))
    assert isinstance(details, list) and len(details) == 1
    d = details[0]
    assert d["asin"] == "B078C6QR1C"
    assert d["access_state"] in {"NORMAL", "CHALLENGE", "BLOCKED", "RATE_LIMITED",
                                 "NETWORK_ERROR", "UNKNOWN"}
    assert (tmp_path / "html" / "B078C6QR1C.html").exists()
    assert (tmp_path / "details.json").exists()
