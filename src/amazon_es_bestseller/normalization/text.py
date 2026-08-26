# -*- coding: utf-8 -*-
"""文本级规范化工具（西语数字/不可见字符/空白）。

从历史脚本（prep_selection_data.py / prep_v2_selection.py / make_translations.py
各自重复的 _dec_comma、clean 逻辑）抽取并统一。
"""
from __future__ import annotations

import re

#: 不可见 Unicode 噪声（QA_RULES §25）：零宽字符、双向控制、BOM、不间断/窄空格
_INVISIBLE_CHARS = (
    "​"  # ZERO WIDTH SPACE
    "‌"  # ZERO WIDTH NON-JOINER
    "‍"  # ZERO WIDTH JOINER
    "‎"  # LEFT-TO-RIGHT MARK
    "‏"  # RIGHT-TO-LEFT MARK
    "⁠"  # WORD JOINER
    "﻿"  # ZERO WIDTH NO-BREAK SPACE / BOM
    "‪"  # LEFT-TO-RIGHT EMBEDDING
    "‫"  # RIGHT-TO-LEFT EMBEDDING
    "‬"  # POP DIRECTIONAL FORMATTING
    "‭"  # LEFT-TO-RIGHT OVERRIDE
    "‮"  # RIGHT-TO-LEFT OVERRIDE
    " "  # NARROW NO-BREAK SPACE
    " "  # NO-BREAK SPACE
)
_INVISIBLE_RE = re.compile("[" + _INVISIBLE_CHARS + "]+")


def dec_comma(s: str) -> str:
    """西语小数点逗号 → 点（仅在两个数字之间）。"""
    return re.sub(r"(?<=\d),(?=\d)", ".", str(s))


def strip_zero_width(s: str) -> str:
    """剥离零宽字符/双向控制/BOM/不间断空格等不可见噪声。"""
    return _INVISIBLE_RE.sub("", str(s))


def collapse_ws(s: str) -> str:
    """连续空白折叠为单个空格并去首尾（对齐 extract_details.js 的 clean()）。"""
    return re.sub(r"\s+", " ", str(s)).strip()


def as_clean_str(v) -> str:
    """值 → 去空白字符串；None → ''。"""
    if v is None:
        return ""
    return str(v).strip()
