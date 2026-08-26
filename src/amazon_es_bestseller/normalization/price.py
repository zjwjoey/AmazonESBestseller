# -*- coding: utf-8 -*-
"""价格规范化（价格定义已冻结，见 README §8 / QA_RULES §16-§18）。

当前市场：amazon.es，货币 EUR。
"""
from __future__ import annotations

import re
from typing import Optional

from .text import dec_comma

CURRENCY = "EUR"

_CURRENCY_NOISE = re.compile(r"[€\s]")


def parse_price(s) -> Optional[float]:
    """价格文本 → 数值；无法解析或 <=0 返回 None（QA_RULES §16 必须 >0）。"""
    if s is None:
        return None
    t = _CURRENCY_NOISE.sub("", str(s).strip().upper()).replace("EUR", "").strip()
    if not t:
        return None
    t = dec_comma(t)
    try:
        f = float(t)
    except (ValueError, TypeError):
        return None
    return f if f > 0 else None


def discount_rate(current, original) -> Optional[float]:
    """仅当 original 与 current 同时存在且 original > current 时计算（QA_RULES §18）。

    formula: (original - current) / original，保留 4 位。
    """
    cur = parse_price(current)
    orig = parse_price(original)
    if cur is None or orig is None:
        return None
    if orig <= cur:
        return None
    return round((orig - cur) / orig, 4)
