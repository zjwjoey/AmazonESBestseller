# -*- coding: utf-8 -*-
"""品牌清洗与合理性校验。

从 prep_selection_data.py / prep_v2_selection.py 抽取并增强：
除 ``Visita la tienda de`` 前缀外，新增 ``Marca:`` 前缀与不可见字符剥离。
品牌识别不在本模块职责内（不推断、不取标题首词，QA_RULES §10）。
"""
from __future__ import annotations

import re

from .text import collapse_ws, strip_zero_width

_VISITA_RE = re.compile(r"^Visita\s+la\s+tienda\s+de\s+(.+)$", re.IGNORECASE)
_MARCA_RE = re.compile(r"^Marca:\s*(.*)$", re.IGNORECASE)

#: 显式品牌规范大小写表。仅收录有把握的品牌，不做推断。
BRAND_CANON = {
    "bissell": "BISSELL",
}

#: 疑似品牌误判的西语普通名词/描述词（QA_RULES §10：宁缺毋假）。
#: 证据来源：真实标题首词（_feat_scan.txt）与商品描述类通用名词——
#: 这些词绝不可能是品牌，若出现在品牌字段即为误判。
BRAND_FALSE_POSITIVE = frozenset({
    'limpiador', 'limpiadora', 'barrera', 'brazo', 'sombrerete', 'marca',
    # 真实标题首词证据
    'toallas', 'renovador', 'recambios', 'lote',
    # 商品描述类普通名词/形容词
    'desechable', 'accesorio', 'accesorios',
    'set', 'pack', 'paquete', 'juego', 'juegos',
    'bolsa', 'caja', 'recipiente', 'recipientes',
    'universal', 'portatil', 'profesional',
})

#: 品牌合理性：超过该词数视为标题片段误判（真实品牌最多 3-4 词）。
_BRAND_MAX_WORDS = 4
_BRAND_STOP_RE = re.compile(
    r'\b(' + '|'.join(sorted(BRAND_FALSE_POSITIVE, key=len, reverse=True)) + r')\b',
    re.IGNORECASE)


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


def is_brand_suspicious(b) -> bool:
    """品牌疑似误判（QA_RULES §10 宁缺毋假）。

    命中任一：整词含停用词（西语普通名词/描述词），或超过 _BRAND_MAX_WORDS 词
    （标题片段被误当品牌）。
    """
    if not b:
        return False
    s = str(b).strip()
    if _BRAND_STOP_RE.search(s):
        return True
    if len(s.split()) > _BRAND_MAX_WORDS:
        return True
    return False
