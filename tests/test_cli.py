# -*- coding: utf-8 -*-
"""cli.py 测试：统一入口（enrich/qa/export 离线主链 + collect 联网拒离线）。

不联网：只测离线子命令与参数纪律；collect 的真实采集不在此覆盖。
"""
import json
import sys
from pathlib import Path

import pytest

from amazon_es_bestseller.cli import main

REPO = Path(__file__).resolve().parent.parent


def test_cli_help_lists_subcommands(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("collect", "enrich", "qa", "export"):
        assert cmd in out


def test_collect_rejects_offline(capsys):
    # --offline 是全局参数，置于子命令前
    with pytest.raises(SystemExit) as ei:
        main(["--offline", "collect", "--urls", "https://www.amazon.es/zgbs/1"])
    assert ei.value.code == 2
    assert "联网" in capsys.readouterr().err


def test_collect_requires_urls(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["collect"])
    assert ei.value.code == 2


def _real_data():
    p = REPO / "product_details.json"
    return p if p.exists() else None


def test_enrich_qa_export_offline_chain(tmp_path, capsys):
    """30 条真实数据走 cli enrich → qa → export（0 P0/P1）。"""
    src = _real_data()
    if src is None:
        pytest.skip("product_details.json 不在仓库")
    prod_out = tmp_path / "products.json"
    qa_out = tmp_path / "qa.json"
    xlsx_out = tmp_path / "选品清单.xlsx"

    assert main(["enrich", "--legacy", str(src), "--out", str(prod_out)]) == 0
    products = json.loads(prod_out.read_text(encoding="utf-8"))
    assert len(products) == 30
    # 构造型 BSR 已丢弃：无任何 detail_bsr_segments
    assert all(not p.get("detail_bsr_segments") for p in products)

    assert main(["qa", "--products", str(prod_out), "--out", str(qa_out)]) == 0
    qa = json.loads(qa_out.read_text(encoding="utf-8"))
    assert qa["summary"]["total_products"] == 30
    assert qa["summary"]["fail_count"] == 0
    assert qa["summary"]["source_conflict_count"] == 0
    # 0 P0/P1（P2 缺失类 WARN 允许）
    p0p1 = [i for r in qa["records"] for i in r["issues"] if i["severity"] in ("P0", "P1")]
    assert not p0p1

    assert main(["export", "--products", str(prod_out), "--out", str(xlsx_out)]) == 0
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_out)
    # B3x 新契约：3 张表
    assert wb.sheetnames == ["类目规划", "西班牙语选品清单", "中文选品清单"]


def test_enrich_missing_input_file(tmp_path):
    with pytest.raises(SystemExit) as ei:
        main(["enrich", "--rankings", str(tmp_path / "nope.json"),
              "--out", str(tmp_path / "x.json")])
    assert "找不到输入文件" in str(ei.value.code)


def _blocked_products():
    """含 1 条 P0（ASIN 非法）+ 1 条合法记录的商品表（测试 QA 导出门禁）。"""
    return [
        {"asin": "B078C6QR1C", "title_es_raw": "Protector",
         "current_price_raw": "12,62 €"},
        {"asin": None, "title_es_raw": "sin asin", "current_price_raw": "1,00 €"},
    ]


def test_export_qa_gate_blocks_on_p0(tmp_path, capsys):
    """QA_RULES §31：存在 P0/P1 → 默认拒绝导出（保留上游错误证据）。"""
    prod_out = tmp_path / "products.json"
    prod_out.write_text(
        json.dumps(_blocked_products()), encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        main(["export", "--products", str(prod_out),
              "--out", str(tmp_path / "out.xlsx")])
    msg = str(ei.value.code)
    assert "拒绝导出" in msg
    assert "ASIN_INVALID" in msg
    assert not (tmp_path / "out.xlsx").exists()     # 未产出 Excel


def test_export_qa_gate_force_bypasses(tmp_path):
    """--force：跳过 QA 门禁强制导出（明确授权，不静默）。"""
    prod_out = tmp_path / "products.json"
    prod_out.write_text(
        json.dumps(_blocked_products()), encoding="utf-8")
    xlsx_out = tmp_path / "out.xlsx"
    assert main(["export", "--products", str(prod_out),
                 "--out", str(xlsx_out), "--force"]) == 0
    assert xlsx_out.exists()
