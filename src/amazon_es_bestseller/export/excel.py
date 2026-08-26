# -*- coding: utf-8 -*-
"""Excel 工作簿导出（只做呈现，无业务推断，ARCHITECTURE §71）。

从 build_v2_workbook.py + build_selection_workbook.py 抽取样式/布局/统计/下拉，
按 ARCHITECTURE §71 将"业务推断"剥离：本模块只按传入记录排版。

图片按 ASIN 锚定（QA_RULES §51-§52），不按位置；人工字段（选品状态/研究备注）
从前版工作簿按 ASIN 合并保留（QA_RULES §66）。
"""
from __future__ import annotations

import json
from io import BytesIO
from typing import Dict, Iterable, List, Mapping, Optional

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# ---------- 样式常量（V2 头部 + V1 字体/对齐） ----------
HDR_FILL = PatternFill('solid', fgColor='1F4E78')
HDR_FONT = Font(bold=True, color='FFFFFF', size=11)
TITLE_FONT = Font(bold=True, size=14, color='1F4E78')
STAT_LABEL = Font(bold=True, size=10)
F_BODY = Font(size=10)
F_LINK = Font(color='0563C1', underline='single', size=9)
THIN = Side(style='thin', color='D9D9D9')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical='top')
AL_C = Alignment(horizontal='center', vertical='center', wrap_text=True)
AL_L = Alignment(horizontal='left', vertical='center', wrap_text=True)
AL_R = Alignment(horizontal='right', vertical='center', wrap_text=True)

CURRENCY_FMT = '#,##0.00" €"'
PERCENT_FMT = '0%'

#: 选品状态下拉（业务枚举，固定值）
SELECTION_STATUS = "待评估,重点关注,暂不考虑,已研究"

#: 选品清单（Sheet1）列
HEAD1 = ['图片', '序号', '商品名称', '当前价格', '原价', '折扣率', '一级类目', '二级类目', '三级类目',
         '细分类目', '月购买量', '类目排名', '规格', '商品详情摘要', '上架时间', '商品链接', '图片链接',
         'ASIN', '选品状态', '研究备注']
WIDTH1 = [10, 5, 42, 9, 9, 8, 11, 8, 8, 20, 10, 9, 28, 48, 11, 26, 34, 12, 10, 16]

#: 排行榜记录（Sheet2）列
HEAD2 = ['index', 'asin', 'category_l1', 'category_l2', 'category_l3', 'leaf_category',
         'browse_node_id', 'category_rank', 'monthly_bought_raw', 'monthly_bought_min',
         'ranking_source_url', 'collected_at']
WIDTH2 = [8, 14, 14, 10, 10, 18, 14, 12, 14, 12, 40, 20]

#: 后台数据（Sheet3）列
HEAD3 = ['asin', 'parent_asin', 'title_es_raw', 'brand_raw', 'brand', 'current_price',
         'original_price', 'currency', 'discount_rate', 'rating', 'review_count',
         'monthly_bought_raw', 'monthly_bought_min', 'image_url', 'product_url',
         'details_json', 'date_first_available', 'date_first_available_raw',
         'first_seen', 'last_seen', 'bestseller_rank', 'bsr_segments', 'bsr_main_cat',
         'bsr_main_rank', 'bsr_leaf_cat', 'bsr_leaf_rank', '选品状态', '研究备注']

#: 商品名称中文对照（Sheet5）列
HEAD5 = ['序号', 'ASIN', '商品名称(西语)', '商品名称(中文)']

TITLE = 'Amazon.es 畅销商品内部选品数据库 · 选品清单'


def write_header(ws, row: int, headers: Iterable[str], widths: Iterable[int]) -> None:
    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=ci, value=h)
        cell.font = HDR_FONT
        cell.fill = HDR_FILL
        cell.alignment = AL_C
        cell.border = BORDER
    ws.row_dimensions[row].height = 28
    for ci, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(ci)].width = w


def style_data(ws, first_row: int, last_row: int, ncols: int,
               wrap_cols=(), center_cols=(), right_cols=(), link_cols=()) -> None:
    for r in range(first_row, last_row + 1):
        for ci in range(1, ncols + 1):
            c = ws.cell(row=r, column=ci)
            c.font = F_BODY
            c.border = BORDER
            if ci in wrap_cols:
                c.alignment = WRAP
            elif ci in center_cols:
                c.alignment = AL_C
            elif ci in right_cols:
                c.alignment = AL_R
            else:
                c.alignment = AL_L
            if ci in link_cols and c.value:
                c.hyperlink = c.value
                c.font = F_LINK


def to_num(v) -> Optional[float]:
    """西语/欧元文本 → float；空或无法解析 → None。"""
    if v is None or str(v).strip() == '':
        return None
    try:
        return float(str(v).replace(',', '.').replace('€', '').strip())
    except ValueError:
        return None


def compute_stats(records: List[Mapping]) -> dict:
    """纯计数统计（QA_RULES §57），不含任何推断。"""
    l1s = {r.get('采集类目中文') for r in records if r.get('采集类目中文')}
    leafs = {r.get('bsr_leaf_cat') for r in records if r.get('bsr_leaf_cat')}
    return {
        '商品数': len(records),
        '一级类目数': len(l1s),
        '细分类目数': len(leafs),
        '有月购买量商品数': sum(1 for r in records if r.get('monthly_bought_min')),
        '有折扣商品数': sum(1 for r in records if r.get('discount_rate')),
    }


def stats_area(ws, stats: Mapping, ncols: int) -> None:
    c = 1
    for label, val in stats.items():
        lc = ws.cell(row=2, column=c, value=label)
        lc.font = STAT_LABEL
        vc = ws.cell(row=2, column=c + 1, value=val)
        vc.font = Font(size=10, bold=True)
        c += 2
    tc = ws.cell(row=1, column=1, value=TITLE)
    tc.font = TITLE_FONT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)


def add_dropdown(ws, col_letter: str, first_row: int, last_row: int) -> None:
    dv = DataValidation(type='list', formula1='"%s"' % SELECTION_STATUS, allow_blank=True)
    ws.add_data_validation(dv)
    dv.add('%s%d:%s%d' % (col_letter, first_row, col_letter, last_row))


def embed_images_by_asin(ws, images_by_asin: Mapping, row_of_asin: Mapping,
                         col: str = 'A') -> None:
    """按 ASIN 锚定内嵌图片（QA_RULES §51-§52），不按位置。

    images_by_asin: {ASIN: (BytesIO|bytes, width, height)}；row_of_asin: {ASIN: 1-based 行号}。
    """
    for asin, (data, width, height) in images_by_asin.items():
        row = row_of_asin.get(str(asin).upper())
        if row is None:
            continue
        try:
            img = XLImage(data)
        except Exception:
            continue
        img.width = width or 70
        img.height = height or 70
        ws.add_image(img, '%s%d' % (col, row))


def _find_header_row(ws, required_col: str) -> Optional[int]:
    for r in range(1, min(ws.max_row, 10) + 1):
        row_vals = [ws.cell(row=r, column=ci).value for ci in range(1, ws.max_column + 1)]
        if any(v is not None and str(v).strip() == required_col for v in row_vals):
            return r
    return None


def _col_index(header: List[str], name: str) -> Optional[int]:
    for i, h in enumerate(header):
        if h == name:
            return i
    return None


def merge_manual_fields(records: List[Mapping], prev_workbook=None) -> List[Mapping]:
    """从前版工作簿「选品清单」按 ASIN 合并人工字段（QA_RULES §66）。

    只回填前版非空的人工值；不重置、不新建空列。
    """
    out = [dict(r) for r in records]
    if prev_workbook is None or '选品清单' not in getattr(prev_workbook, 'sheetnames', ()):
        return out
    ws = prev_workbook['选品清单']
    hr = _find_header_row(ws, 'ASIN')
    if hr is None:
        return out
    header = [str(c.value).strip() if c.value is not None else '' for c in ws[hr]]
    ci_asin = _col_index(header, 'ASIN')
    ci_status = _col_index(header, '选品状态')
    ci_notes = _col_index(header, '研究备注')
    if ci_asin is None:
        return out
    manual = {}
    for r in ws.iter_rows(min_row=hr + 1, values_only=True):
        if ci_asin >= len(r) or r[ci_asin] is None or str(r[ci_asin]).strip() == '':
            continue
        a = str(r[ci_asin]).strip().upper()
        status = r[ci_status] if ci_status is not None and ci_status < len(r) else None
        notes = r[ci_notes] if ci_notes is not None and ci_notes < len(r) else None
        manual[a] = (status, notes)
    for rec in out:
        a = str(rec.get('asin', '')).strip().upper()
        if a in manual:
            status, notes = manual[a]
            if status not in (None, ''):
                rec['选品状态'] = status
            if notes not in (None, ''):
                rec['研究备注'] = notes
    return out


def _transl_for(translations, asin) -> Mapping:
    tr = (translations or {}).get(asin) or {}
    return tr if isinstance(tr, dict) else {'title_zh': tr}


def export_workbook(records: List[Mapping],
                    translations: Optional[Mapping] = None,
                    images_by_asin: Optional[Mapping] = None,
                    category_planning: Optional[List] = None,
                    prev_workbook=None,
                    out_path=None,
                    collected_at: Optional[str] = None) -> openpyxl.Workbook:
    """构建 5-sheet 工作簿并返回；仅当给 out_path 时保存。

    records: 合并后的商品表记录；translations 按 ASIN → {title_zh,...}；
    images_by_asin 按 ASIN → (data, w, h)；category_planning 为 dict 行或 2D 行列表。
    本函数不修改传入 records（内部拷贝）。
    """
    records = [dict(r) for r in records]
    if prev_workbook is not None:
        records = merge_manual_fields(records, prev_workbook)
    if collected_at is None:
        for r in records:
            if r.get('collected_at'):
                collected_at = r['collected_at']
                break

    # 序号按一级类目分组重排
    seq: Dict[str, int] = {}
    for r in records:
        l1 = r.get('采集类目中文') or ''
        seq[l1] = seq.get(l1, 0) + 1
        r['_seq'] = seq[l1]

    wb = openpyxl.Workbook()

    # ---------- Sheet1 选品清单 ----------
    ws = wb.active
    ws.title = '选品清单'
    write_header(ws, 3, HEAD1, WIDTH1)
    stats = compute_stats(records)
    stats['数据采集时间'] = collected_at or ''
    stats_area(ws, stats, len(HEAD1))
    ws.freeze_panes = 'A4'

    row_of_asin: Dict[str, int] = {}
    for i, r in enumerate(records):
        row = 4 + i
        row_of_asin[str(r.get('asin')).upper()] = row
        vals = [None, r.get('_seq'), r.get('title_es_raw'),
                to_num(r.get('current_price')), to_num(r.get('original_price')),
                to_num(r.get('discount_rate')),
                r.get('采集类目中文'), '', '', r.get('bsr_leaf_cat'),
                r.get('monthly_bought_min') or '', r.get('bsr_leaf_rank'),
                r.get('spec_v2'), r.get('summary_v2'), r.get('date_first_available'),
                r.get('product_url'), r.get('image_url'), r.get('asin'),
                r.get('选品状态') or '待评估', r.get('研究备注')]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=row, column=c, value=v)
            cell.border = BORDER
            if c in (4, 5) and isinstance(v, (int, float)):
                cell.number_format = CURRENCY_FMT
            if c == 6 and isinstance(v, (int, float)):
                cell.number_format = PERCENT_FMT
            if c in (3, 13, 14, 10):
                cell.alignment = WRAP
            if c in (16, 17):
                cell.font = F_LINK
            if c == 2:
                cell.alignment = AL_C
        if r.get('product_url'):
            ws.cell(row=row, column=16).hyperlink = r['product_url']
        if r.get('image_url'):
            ws.cell(row=row, column=17).hyperlink = r['image_url']
        ws.row_dimensions[row].height = 78

    if images_by_asin:
        embed_images_by_asin(ws, images_by_asin, row_of_asin, col='A')
    add_dropdown(ws, 'S', 4, 3 + len(records))
    ws.auto_filter.ref = 'A3:T%d' % (3 + len(records))

    # ---------- Sheet2 排行榜记录（每榜单一行） ----------
    ws2 = wb.create_sheet('排行榜记录')
    write_header(ws2, 1, HEAD2, WIDTH2)
    row2 = 2
    for r in records:
        segs = r.get('bsr_segments') or []
        if not segs:
            continue
        for cat, rank in segs:
            vals = [row2 - 1, r.get('asin'), r.get('采集类目中文'), '', '', cat, '',
                    rank, r.get('monthly_bought_raw') or '', r.get('monthly_bought_min') or '',
                    r.get('ranking_source_url') or '', r.get('collected_at') or '']
            for c, v in enumerate(vals, 1):
                ws2.cell(row=row2, column=c, value=v).border = BORDER
            row2 += 1
    ws2.freeze_panes = 'A2'
    if row2 > 2:
        ws2.auto_filter.ref = 'A1:L%d' % (row2 - 1)

    # ---------- Sheet3 后台数据 ----------
    ws3 = wb.create_sheet('后台数据')
    write_header(ws3, 1, HEAD3, [14] * len(HEAD3))
    for i, r in enumerate(records):
        row = 2 + i
        bsr_json = json.dumps(r.get('bsr_segments') or [], ensure_ascii=False)
        vals = [r.get('asin'), r.get('parent_asin'), r.get('title_es_raw'), r.get('brand_raw'),
                r.get('brand'), to_num(r.get('current_price')), to_num(r.get('original_price')),
                r.get('currency'), r.get('discount_rate'), r.get('rating'), r.get('review_count'),
                r.get('monthly_bought_raw'), r.get('monthly_bought_min'), r.get('image_url'),
                r.get('product_url'), r.get('details_json'), r.get('date_first_available'),
                r.get('date_first_available_raw'), r.get('first_seen'), r.get('last_seen'),
                r.get('bestseller_rank'), bsr_json, r.get('bsr_main_cat'), r.get('bsr_main_rank'),
                r.get('bsr_leaf_cat'), r.get('bsr_leaf_rank'), r.get('选品状态'), r.get('研究备注')]
        for c, v in enumerate(vals, 1):
            cell = ws3.cell(row=row, column=c, value=v)
            cell.border = BORDER
            if c in (6, 7) and isinstance(v, (int, float)):
                cell.number_format = '#,##0.00'
    ws3.freeze_panes = 'B2'
    ws3.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(HEAD3)), len(records) + 1)

    # ---------- Sheet4 类目规划 ----------
    ws4 = wb.create_sheet('类目规划')
    cat_rows = category_planning or []
    cat_header = list(cat_rows[0].keys()) if cat_rows and isinstance(cat_rows[0], dict) \
        else ['#', '中文一级类目', 'Amazon 西语名称', '建议', '我的判断']
    write_header(ws4, 1, cat_header, [20] * len(cat_header))
    for i, r in enumerate(cat_rows):
        if isinstance(r, dict):
            for ci, h in enumerate(cat_header, 1):
                v = r.get(h)
                if v is None:
                    continue
                cell = ws4.cell(row=2 + i, column=ci, value=v)
                cell.border = BORDER
                cell.alignment = WRAP
        else:
            for ci, v in enumerate(r, 1):
                if v is None:
                    continue
                cell = ws4.cell(row=2 + i, column=ci, value=v)
                cell.border = BORDER
                cell.alignment = WRAP
    ws4.freeze_panes = 'A2'

    # ---------- Sheet5 商品名称中文对照 ----------
    ws5 = wb.create_sheet('商品名称中文对照')
    write_header(ws5, 1, HEAD5, [6, 14, 46, 46])
    for i, r in enumerate(records):
        row = 2 + i
        tr = _transl_for(translations, r.get('asin'))
        vals = [r.get('_seq'), r.get('asin'), r.get('title_es_raw'), tr.get('title_zh') or '']
        for c, v in enumerate(vals, 1):
            cell = ws5.cell(row=row, column=c, value=v)
            cell.border = BORDER
            cell.alignment = WRAP
    ws5.freeze_panes = 'A2'
    ws5.auto_filter.ref = 'A1:D%d' % (len(records) + 1)

    if out_path:
        wb.save(out_path)
    return wb
