# -*- coding: utf-8 -*-
"""西语日期与 Excel 序列号规范化。

从 prep_selection_data.py 与 prep_v2_selection.py（两份逐字相同）抽取。
"""
from __future__ import annotations

import datetime
import re
from typing import Optional

MONTHS_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}

_ES_DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s+(\d{4})\s*$")


def excel_serial_to_dt(v) -> Optional[datetime.datetime]:
    """Excel 序列号 → datetime（序列号为字符串/数字均可）。"""
    if v is None:
        return None
    try:
        f = float(str(v).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None
    try:
        return datetime.datetime(1899, 12, 30) + datetime.timedelta(days=f)
    except (OverflowError, ValueError):
        return None


def parse_es_date(s) -> Optional[datetime.date]:
    """西语日期 ``'28 octubre 2023'`` → date；无法解析返回 None。"""
    if not s:
        return None
    m = _ES_DATE_RE.match(str(s).strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    if mo not in MONTHS_ES:
        return None
    try:
        return datetime.date(y, MONTHS_ES[mo], d)
    except ValueError:
        return None


def norm_dt(v) -> str:
    """first_seen/last_seen 可能是字符串或数字，统一为字符串。"""
    if v is None or str(v).strip() == "":
        return ""
    s = str(v).strip()
    if re.match(r"^\d+(\.\d+)?$", s):
        dt = excel_serial_to_dt(s)
        return dt.isoformat(sep=" ") if dt else s
    return s
