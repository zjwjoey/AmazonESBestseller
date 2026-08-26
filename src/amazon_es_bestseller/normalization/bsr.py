# -*- coding: utf-8 -*-
"""BSR（Best Seller Rank）段解析。

规范 = V2 list 形式（D9）：extract_bsr_segments 返回 ``[(cat, rank), ...]``，
贪婪 bug 已修（类目名懒匹配，在下一个 ``nº`` / ``(`` / 行尾前停止）。
V1 的 4 元组由 ``bsr_main_and_leaf()`` 包装提供。

bestseller_rank（榜单）与 detail BSR（详情页）绝不混用（QA_RULES §9/§72）：
详情页 BSR 走 ``detail_bsr_segments()``，另存 ``detail_bsr_raw``。
"""
from __future__ import annotations

import re

#: 类目名懒匹配：在下一个 'nº'、'(' 或行尾前停止（原贪婪匹配会把多段拼成一坨）
_BSR_SEG_RE = re.compile(
    r"n\.?º?\s*([\d.,]+)\s+en\s+([^()\n]+?)(?=\s*n[.]?\s*º|\s*\(|\s*$)"
)


def extract_bsr_segments(s) -> list[tuple[str, str]]:
    """榜单 BSR 文本 → ``[(cat, rank), ...]``（无则 []）。"""
    if not s:
        return []
    segs = []
    for m in _BSR_SEG_RE.finditer(str(s)):
        rank = m.group(1).replace(".", "").replace(",", "")
        cat = re.sub(r"\s*(Ver el|Ver los|Ver).*$", "", m.group(2)).strip()
        if cat:
            segs.append((cat, rank))
    return segs


def bsr_main_and_leaf(segs) -> tuple:
    """V1 兼容：list → ``(main_cat, main_rank, leaf_cat, leaf_rank)`` 4 元组。

    主类目 = 首段；leaf 仅当存在多段（比主类目更具体）时才取末段。
    """
    if not segs:
        return (None, None, None, None)
    main_cat, main_rank = segs[0]
    if len(segs) > 1:
        leaf_cat, leaf_rank = segs[-1]
    else:
        leaf_cat, leaf_rank = None, None
    return main_cat, main_rank, leaf_cat, leaf_rank


def detail_bsr_segments(s) -> list[tuple[str, str]]:
    """详情页 BSR 文本 → 段列表；兼容裸数值（QA_RULES §72 例：180285）。

    裸数值没有类目上下文，类目记为 ''，仅用于 QA 层与 bestseller_rank 比对。
    """
    segs = extract_bsr_segments(s)
    if segs:
        return segs
    t = str(s or "").strip().replace(".", "").replace(",", "")
    if t.isdigit():
        return [("", t)]
    return []
