# -*- coding: utf-8 -*-
"""月购买量下限解析（QA_RULES §22）：只取下限，不推断总量。"""
from __future__ import annotations

import re
from typing import Optional

_MONTHLY_RE = re.compile(r'^\s*([\d.,]+)\s*(mil|k)?\s*\+', re.IGNORECASE)


def _parse_num_es(t: str) -> float:
    """西语数字（. 千位 / , 小数）→ float。"""
    t = t.strip()
    if '.' in t and ',' in t:
        return float(t.replace('.', '').replace(',', '.'))
    if '.' in t:
        return float(t.replace('.', ''))
    if ',' in t:
        return float(t.replace(',', '.'))
    return float(t)


def parse_monthly_bought(raw) -> Optional[int]:
    """'100+'→100；'1 mil+'→1000；'1.500+'→1500；'1,5 mil+'→1500；无法解析→None。"""
    if not raw:
        return None
    m = _MONTHLY_RE.match(str(raw))
    if not m:
        return None
    try:
        n = _parse_num_es(m.group(1))
    except ValueError:
        return None
    mult = 1000 if m.group(2) else 1
    return int(n * mult)
