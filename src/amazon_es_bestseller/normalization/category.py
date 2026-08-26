# -*- coding: utf-8 -*-
"""类目层级解析：只认真实 BSR 榜单证据（QA_RULES §12-§14）。

未知 leaf → None，绝不复制 L3 充数（QA_RULES §73）；不组合多类目（§14）。
"""
from __future__ import annotations

from typing import Optional

#: 已知西语类目 → 中文业务名（仅收录有把握的映射；未知返回 None）
CATEGORY_ZH = {
    "hogar y cocina": "家居与厨房",
    "juegos de recipientes": "收纳盒套装",
}


def resolve_leaf_category(segs) -> Optional[str]:
    """BSR 段列表 → leaf_category。

    仅当存在多段时取最后一段（比主类目更具体）；空或单段 → None（V2 语义，
    单段即主类目，不冒充 leaf，也不复制 L3）。
    """
    if not segs:
        return None
    if len(segs) > 1:
        return segs[-1][0]
    return None


def category_zh(es) -> Optional[str]:
    """西语类目 → 中文（大小写不敏感查找）；未知 → None（不猜测）。"""
    if not es:
        return None
    return CATEGORY_ZH.get(str(es).strip().lower())
