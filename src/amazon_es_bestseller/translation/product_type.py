# -*- coding: utf-8 -*-
"""商品类型检测：有序西语关键词 → 中文商品类型。

QA_RULES §11 / §16：中文商品名必须正确回答"这是什么产品"；商品类型正确性优先于措辞。

设计：
- 规则按**互斥类目组**组织（`PRODUCT_TYPE_GROUPS`），组间顺序=优先级；
- 组内**更具体的词在前**（first-match wins），保证已知回归
  （保温包≠便当盒、打草线≠割草机、迷你电锯≠链条润滑油、滤杯手柄≠压粉器、
  除垢片≠滤杯手柄、可重复使用≠一次性）不被宽泛词覆盖；
- 同一语义域的"耗材/配件"整组先于"硬件"（`pastillas de limpieza`/`desincrustantes`
  先于 `portafiltro`/`tamper`），实现"含除垢片时绝不判滤杯手柄"的互斥；
- `PRODUCT_TYPE_RULES` 是扁平展开，保持向后兼容；
- 无匹配 → None（宁缺毋假，不猜测）。
"""
from __future__ import annotations

import unicodedata
from typing import Optional

#: 互斥类目组：(西语关键词, 中文商品类型)；组内具体优先，组间顺序=优先级
PRODUCT_TYPE_GROUPS: tuple[tuple[tuple[str, str], ...], ...] = (
    # 保温/便当（保温包≠便当盒）
    (
        ('bolsa térmica', '保温包'),
        ('fiambrera', '便当盒'),
        ('lunch box', '便当盒'),
    ),
    # 打草/割草（打草线≠割草机）
    (
        ('hilo de desbrozadora', '打草线'),
        ('desbrozadora', '割草机'),
    ),
    # 链锯油/电锯（迷你电锯≠链条润滑油）
    (
        ('aceite de cadena', '链条润滑油'),
        ('lubricar cadenas', '链条润滑油'),
        ('motosierra', '迷你电锯'),
    ),
    # 咖啡除垢/滤杯/压粉（除垢片≠滤杯手柄）：耗材组整组先于硬件
    (
        ('pastillas de limpieza', '除垢片'),
        ('desincrustantes', '除垢片'),
        ('portafiltro', '滤杯手柄'),
        ('tamper', '压粉器'),
    ),
    # 可重复/一次性（互斥）
    (
        ('reutilizable', '可重复使用'),
        ('desechable', '一次性'),
        ('descartable', '一次性'),
    ),
)


def _flatten() -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for group in PRODUCT_TYPE_GROUPS:
        rules.extend(group)
    return rules


#: (西语关键词, 中文商品类型) 扁平有序表，按组展开；组内具体优先
PRODUCT_TYPE_RULES: list[tuple[str, str]] = _flatten()


def _fold(text: str) -> str:
    """小写 + 去掉重音/变音，统一西语大小写变体（如 Bolsa Termica == Bolsa Térmica）。"""
    return unicodedata.normalize('NFD', str(text).lower()).encode('ascii', 'ignore').decode('ascii')


def detect_product_type(title_es) -> Optional[str]:
    """西语标题 → 中文商品类型；无匹配 → None（不猜测）。"""
    if not title_es:
        return None
    t = _fold(title_es)
    for es, zh in PRODUCT_TYPE_RULES:
        if _fold(es) in t:
            return zh
    return None
