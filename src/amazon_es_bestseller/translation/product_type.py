# -*- coding: utf-8 -*-
"""商品类型检测（Phase 9 脚手架：只建表+测试，不做完整标题管线）。

QA_RULES §27-§28：中文商品名必须正确回答"这是什么产品"。
有序西语关键词 → 中文商品类型，**更具体的词在前**，保证已知回归
（保温包≠便当盒、滤杯手柄≠压粉器、除垢片≠滤杯手柄、迷你电锯≠链条润滑油、
打草线≠割草机、可重复使用≠一次性）不被宽泛词覆盖。
"""
from __future__ import annotations

from typing import Optional

#: (西语关键词, 中文商品类型)，按优先级有序：更具体/更优先的词在前
PRODUCT_TYPE_RULES = [
    ('bolsa térmica', '保温包'),
    ('hilo de desbrozadora', '打草线'),
    ('desbrozadora', '割草机'),
    ('aceite de cadena', '链条润滑油'),
    ('motosierra', '迷你电锯'),
    ('portafiltro', '滤杯手柄'),
    ('tamper', '压粉器'),
    ('pastillas de limpieza', '除垢片'),
    ('desincrustantes', '除垢片'),
    ('fiambrera', '便当盒'),
    ('reutilizable', '可重复使用'),
]


def detect_product_type(title_es) -> Optional[str]:
    """西语标题 → 中文商品类型；无匹配 → None（不猜测）。"""
    if not title_es:
        return None
    t = str(title_es).lower()
    for es, zh in PRODUCT_TYPE_RULES:
        if es in t:
            return zh
    return None
