# -*- coding: utf-8 -*-
"""品牌清洗。

从 prep_selection_data.py / prep_v2_selection.py 抽取并增强：
除 ``Visita la tienda de`` 前缀外，新增 ``Marca:`` 前缀与不可见字符剥离（QA_RULES §25）。
品牌识别不在本模块职责内（不推断、不取标题首词）。
"""
from __future__ import annotations

import re

from .text import collapse_ws, strip_zero_width

_VISITA_RE = re.compile(r"^Visita\s+la\s+tienda\s+de\s+(.+)$", re.IGNORECASE)
_MARCA_RE = re.compile(r"^Marca:\s*(.*)$", re.IGNORECASE)

#: 显式品牌规范大小写表（QA_RULES §26）。仅收录有把握的品牌，不做推断。
BRAND_CANON = {
    "bissell": "BISSELL",
}


def clean_brand(b) -> str:
    """剥离展示前缀与不可见字符；只做确定性清洗，不推断品牌。"""
    if not b:
        return ""
    s = str(b).strip()
    m = _VISITA_RE.match(s)
    if m:
        s = m.group(1).strip()
    m = _MARCA_RE.match(s)
    if m:
        s = m.group(1).strip()
    return collapse_ws(strip_zero_width(s))


def normalize_brand_case(b) -> str:
    """按显式映射统一品牌大小写；未收录的品牌原样返回。"""
    if not b:
        return ""
    s = str(b).strip()
    return BRAND_CANON.get(s.lower(), s)
