# -*- coding: utf-8 -*-
"""类目层级解析：只认真实榜单/详情证据（QA_RULES §6/§12-§14）。

**主源 = 榜单节点**（collection/ranking.py 从页面面包屑提取的节点类目路径），
详情页 BSR 段为次源。未知 leaf → None，绝不复制 L3 充数（QA_RULES §73）；
不组合多类目（§14）。所有层级必须来自 Amazon 页面证据，绝不从标题臆造。
"""
from __future__ import annotations

from typing import Optional

#: 已知西语类目 → 中文业务名（仅收录有把握的映射；未知返回 None）
CATEGORY_ZH = {
    "hogar y cocina": "家居与厨房",
    "bricolaje y herramientas": "DIY及工具",
    "café y té": "咖啡与茶",
    "almacenamiento y organización": "收纳与整理",
    "almacenamiento de cocina y despensa": "厨房与食品储藏",
    "aspiración, limpieza y cuidado de suelo y ventanas": "吸尘、清洁及地面和窗户护理",
    "baño": "浴室",
    "chimeneas y accesorios": "壁炉与配件",
    "cortacéspedes y herramientas eléctricas para exteriores": "割草机与户外电动工具",
    "ferretería": "五金工具",
    "fontanería": "管道工具",
    "herramientas manuales y eléctricas": "手动与电动工具",
    "accesorios de aspiradoras para alfombras": "吸尘器配件",
    "accesorios de baño": "浴室配件",
    "accesorios de chimenea": "壁炉配件",
    "accesorios para cafeteras": "咖啡机配件",
    "accesorios para herramientas eléctricas": "电动工具配件",
    "almacenaje de adornos festivos": "节日装饰收纳",
    "almacenamiento de alimentos": "食品收纳",
    "bombas de agua y accesorios": "水泵及配件",
    "juegos de recipientes": "收纳盒套装",
}


def category_levels(trail) -> tuple:
    """榜单节点类目路径 → ``(category_l1, category_l2, category_l3, leaf_category)``。

    ``trail`` 是页面面包屑按根→叶顺序的类目名列表（已剔除根链接
    "Los más vendidos"）。规则（QA_RULES §6/§73）：
      - 空 → 全 None（缺失即 null，不臆造）；
      - 1 级 → L1，leaf=None（单段即主类目，不冒充 leaf）；
      - 2 级 → L1/L2，leaf=L2（最具体即叶）；
      - ≥3 级 → L1/L2/L3 取前三级，leaf=最后一级（leaf==L3 是定义使然，
        非复制充数）；更深未知层级保持 null。
    """
    if not trail:
        return (None, None, None, None)
    l1 = trail[0]
    l2 = trail[1] if len(trail) > 1 else None
    l3 = trail[2] if len(trail) > 2 else None
    leaf = trail[-1] if len(trail) > 1 else None
    return (l1, l2, l3, leaf)


def resolve_leaf_category(segs) -> Optional[str]:
    """BSR 段列表 → leaf_category（详情页次源，V2 语义）。

    仅当存在多段时取最后一段（比主类目更具体）；空或单段 → None（单段即主
    类目，不冒充 leaf，也不复制 L3）。榜单主源由 ``category_levels`` 承担。
    """
    if not segs:
        return None
    return category_levels([cat for cat, _ in segs])[3]


def category_zh(es) -> Optional[str]:
    """西语类目 → 中文（大小写不敏感查找）；未知 → None（不猜测）。"""
    if not es:
        return None
    return CATEGORY_ZH.get(str(es).strip().lower())
