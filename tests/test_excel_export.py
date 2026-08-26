# -*- coding: utf-8 -*-
"""export/excel.py 测试：5-sheet 结构、下拉/冻结/超链接/数字格式/图片按 ASIN/人工字段保留。"""
import base64
from io import BytesIO

from amazon_es_bestseller.export.excel import (
    compute_stats,
    embed_images_by_asin,
    export_workbook,
    merge_manual_fields,
    to_num,
)

#: 1x1 透明 PNG（测试内嵌图片用）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_export_5_sheets(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    assert wb.sheetnames == ['选品清单', '排行榜记录', '后台数据', '类目规划', '商品名称中文对照']


def test_sheet1_header_and_rows(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    ws = wb['选品清单']
    header = [ws.cell(row=3, column=c).value for c in range(1, 21)]
    assert header[0] == '图片'
    assert header[17] == 'ASIN'
    assert header[18] == '选品状态'
    asins = [ws.cell(row=r, column=18).value for r in range(4, 7)]
    assert asins == ['B078C6QR1C', 'B075JJRFVV', 'B07RN64P2R']
    statuses = [ws.cell(row=r, column=19).value for r in range(4, 7)]
    assert statuses == ['重点关注', '待评估', '已研究']


def test_no_duplicate_asins_in_backend(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    ws = wb['后台数据']
    asins = [ws.cell(row=r, column=1).value for r in range(2, 5)]
    assert len(asins) == len(set(asins))


def test_hyperlinks_valid(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    ws = wb['选品清单']
    for r in range(4, 7):
        cell = ws.cell(row=r, column=16)
        assert cell.hyperlink is not None
        assert cell.hyperlink.target.startswith("https://www.amazon.es/dp/")
        assert ws.cell(row=r, column=18).value in cell.hyperlink.target


def test_dropdown_on_status_column(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    ws = wb['选品清单']
    dvs = list(ws.data_validations.dataValidation)
    assert len(dvs) == 1
    dv = dvs[0]
    assert dv.type == 'list'
    assert '重点关注' in dv.formula1
    assert str(dv.sqref) == 'S4:S6'


def test_freeze_panes(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    assert wb['选品清单'].freeze_panes == 'A4'
    assert wb['后台数据'].freeze_panes == 'B2'
    assert wb['商品名称中文对照'].freeze_panes == 'A2'


def test_number_formats(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    ws = wb['选品清单']
    assert ws.cell(row=4, column=4).number_format == '#,##0.00" €"'
    assert ws.cell(row=4, column=5).number_format == '#,##0.00" €"'
    assert ws.cell(row=4, column=6).number_format == '0%'
    # 无划线价行 → None，不写占位
    assert ws.cell(row=5, column=5).value is None


def test_images_anchored_by_asin(tmp_path, tiny_records):
    from openpyxl import load_workbook
    imgs = {
        'B078C6QR1C': (BytesIO(PNG_1PX), 70, 70),
        'B075JJRFVV': (BytesIO(PNG_1PX), 70, 70),
        'B07RN64P2R': (BytesIO(PNG_1PX), 70, 70),
    }
    export_workbook(tiny_records, images_by_asin=imgs,
                    out_path=str(tmp_path / "out.xlsx"))
    # 保存后重读（与 build_v2 从文件取图一致）：锚点为 OneCellAnchor
    ws = load_workbook(str(tmp_path / "out.xlsx"))['选品清单']
    assert len(ws._images) == 3
    anchor_rows = sorted(im.anchor._from.row for im in ws._images)
    assert anchor_rows == [3, 4, 5]


def test_embed_images_missing_asin_skipped():
    from openpyxl import Workbook
    ws = Workbook().active
    imgs = {'B0INVALID': (BytesIO(PNG_1PX), 70, 70)}
    embed_images_by_asin(ws, imgs, {'B078C6QR1C': 4})
    assert len(ws._images) == 0


def test_merge_manual_fields_preserves_prev(tmp_path, tiny_records):
    prev_records = tiny_records
    for r in prev_records:
        if r['asin'] == 'B078C6QR1C':
            r['选品状态'] = '已研究'
            r['研究备注'] = '月购看涨（人工）'
    prev = export_workbook(prev_records, out_path=str(tmp_path / "prev.xlsx"))

    new_records = tiny_records
    for r in new_records:
        if r['asin'] == 'B078C6QR1C':
            r['选品状态'] = '待评估'
            r['研究备注'] = ''
    merged = merge_manual_fields(new_records, prev)
    b078 = next(r for r in merged if r['asin'] == 'B078C6QR1C')
    assert b078['选品状态'] == '已研究'
    assert b078['研究备注'] == '月购看涨（人工）'


def test_translations_by_asin(tmp_path, tiny_records):
    translations = {
        'B078C6QR1C': {'title_zh': '玻璃便当盒 4件套'},
        'B075JJRFVV': {'title_zh': '保温午餐包'},
    }
    wb = export_workbook(tiny_records, translations=translations,
                         out_path=str(tmp_path / "out.xlsx"))
    ws = wb['商品名称中文对照']
    assert ws.cell(row=2, column=4).value == '玻璃便当盒 4件套'
    assert ws.cell(row=3, column=4).value == '保温午餐包'
    assert ws.cell(row=4, column=4).value == ''  # 无翻译 → 空


def test_stats_area_and_title(tmp_path, tiny_records):
    wb = export_workbook(tiny_records, out_path=str(tmp_path / "out.xlsx"))
    ws = wb['选品清单']
    assert '选品清单' in str(ws.cell(row=1, column=1).value)
    assert ws.cell(row=2, column=1).value == '商品数'
    assert ws.cell(row=2, column=2).value == 3


def test_compute_stats_pure_count(tiny_records):
    stats = compute_stats(tiny_records)
    assert stats['商品数'] == 3
    assert stats['一级类目数'] == 1      # 全是 家居与厨房
    assert stats['细分类目数'] == 2      # 收纳盒套装 + Fiambreras...
    assert stats['有月购买量商品数'] == 1  # 只有 B07RN64P2R
    assert stats['有折扣商品数'] == 2    # 记录1、3 有折扣


def test_to_num():
    assert to_num('12,62 €') == 12.62
    assert to_num('13,29') == 13.29
    assert to_num('0.0504') == 0.0504
    assert to_num('') is None
    assert to_num(None) is None
    assert to_num('abc') is None


def test_export_does_not_mutate_input(tiny_records):
    snapshot = dict(tiny_records[0])
    export_workbook(tiny_records)
    assert tiny_records[0] == snapshot
    assert '_seq' not in tiny_records[0]
