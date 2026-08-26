# -*- coding: utf-8 -*-
"""无损全量详情 → 展示渲染（DATA_MODEL §4-§8 / §18-§19；QA_RULES §29 缺失不自动失败）。

西语 = 证据层：``render_details_es`` 原样逐行 ``label: value``（只去重完全相同重复行，
不翻译、不臆造）；``render_bullets_es`` 原样多行卖点。

中文 = 派生层：``render_details_zh`` 标签查 ``LABEL_ES_ZH``（未知标签留西语原文），
值走 ``translate_value`` 确定性词典；``render_bullets_zh`` 逐条卖点做词典关键词翻译，
未覆盖词保留西语原文——绝不生成词典没有依据的“翻译”。
"""
from __future__ import annotations

import re

from .zh import apply_terms, translate_value

#: 属性标签：西语（小写键）→ 中文。来自真实页面采集的标签集 + 通用 Amazon 标签。
LABEL_ES_ZH = {
    "marca": "品牌",
    "forma del producto": "产品形态",
    "forma artículo": "产品形态",
    "aroma": "香型",
    "fragancia": "香型",
    "usos específicos del producto": "产品具体用途",
    "usos específicos para producto": "产品具体用途",
    "usos recomendados para producto": "产品推荐用途",
    "característica del material": "材质特性",
    "características de materiales": "材质特性",
    "características especiales": "特殊功能",
    "función especial": "特殊功能",
    "volumen del producto": "产品容量",
    "volumen artículo": "产品容量",
    "número de unidades": "数量",
    "número de productos": "产品数量",
    "número de artículos": "商品件数",
    "número de paquetes": "包装件数",
    "número de hilos": "支数",
    "recomendación de superficie": "适用表面",
    "tipo de superficie": "适用表面",
    "contiene líquidos": "含液体",
    "sin tipo de material": "材质类型",
    "capacidad": "容量",
    "tamaño": "尺寸",
    "total del paquete según la medida elegida para referenciar precio": "包装总量（按参考价格规格）",
    "núm. de identificación comercial global": "全球商业识别码（GTIN）",
    "fabricante": "制造商",
    "país de origen": "原产国",
    "componentes incluidos": "内含组件",
    "nombre tipo artículo": "商品类型名称",
    "número modelo": "型号",
    "número de modelo": "型号",
    "número pieza": "零件号",
    "número de pieza del fabricante": "制造商零件号",
    "garantía producto": "产品保修",
    "clasificación en los más vendidos de amazon": "Amazon 畅销榜排名",
    "valoración media de los clientes": "客户平均评分",
    "color": "颜色",
    "tipo de tela": "面料类型",
    "tipo tejido": "织物类型",
    "tipo de cierre": "闭合方式",
    "cuidado del producto": "产品护理",
    "instrucciones de cuidado del producto": "产品护理说明",
    "nivel de resistencia al agua": "防水等级",
    "rango de edad (descripción)": "适用年龄（描述）",
    "descripción del rango de edad": "适用年龄描述",
    "material": "材质",
    "material o tela": "材质",
    "descripción de la firmeza del artículo": "硬度描述",
    "tipo de cama o colchones": "床型",
    "dimensiones pantalla artículo": "商品尺寸",
    "upc": "UPC",
    "asin": "ASIN",
}

#: 与既有 Excel 列重复、纯元信息的标签（展示层剔除；原始 attributes 仍在数据层保留）
_META_LABELS = (
    "asin",
    "clasificación en los más vendidos de amazon",
    "valoración media de los clientes",
)


_TRUNC_MARK = "… Ver más"


def _prefer(new: str, cur: str) -> bool:
    """同标签两行值谁该胜出：完整行优先（剔除 ``… Ver más`` 截断行），
    都完整取较长，都截断取较长（保信息量）。"""
    new_trunc = _TRUNC_MARK in new
    cur_trunc = _TRUNC_MARK in cur
    if new_trunc != cur_trunc:
        return not new_trunc   # 完整者胜（截断行可能因多出省略号+Ver más 反而更长）
    return len(new) > len(cur)


def _display_rows(attributes) -> list:
    """展示行：剔除元信息标签；同标签合并——相同值去重，完整行优先，按首次出现顺序。

    productOverview 常带截断后缀 ``… Ver más`` 而 technical_details 是同标签的完整
    文本；同标签时保留完整行。数据层 attributes 不受影响。
    """
    best: dict = {}          # label_lower -> (首次序号, 标签原文, 值)
    order: list = []
    for a in attributes or []:
        label = a.get("label_raw") or ""
        value = a.get("value_raw") or ""
        if not label or not value:
            continue
        if label.lower() in _META_LABELS:
            continue
        key = label.lower()
        v = value.strip()
        if key not in best:
            best[key] = (len(order), label, v)
            order.append(key)
        else:
            idx, _, cur = best[key]
            if _prefer(v, cur):
                best[key] = (idx, label, v)
    return [(best[k][1], best[k][2]) for k in order]


def render_details_es(attributes) -> str:
    """完整商品详情（西语原文）：逐行 ``label: value``（多行，供 Excel WRAP 单元格）。"""
    rows = _display_rows(attributes)
    if not rows:
        return ""
    return "\n".join("%s: %s" % (label, value) for label, value in rows)


def render_details_zh(attributes) -> str:
    """完整商品详情（中文）：标签查字典（未知留西语），值走 translate_value。

    多个西语标签映射同一中文标签（如 Función especial / Características especiales
    → 特殊功能）时，按中文标签合并——完整值优先（剔除 ``… Ver más`` 截断行）。
    西语原文层各标签仍各保留一行。
    """
    rows = _display_rows(attributes)
    if not rows:
        return ""
    best: dict = {}          # 中文标签小写 -> (首次序号, 显示标签, 值)
    order: list = []
    for label, value in rows:
        zh_label = LABEL_ES_ZH.get(label.lower(), label)
        zh_value = translate_value(value)
        key = zh_label.lower()
        if key not in best:
            best[key] = (len(order), zh_label, zh_value)
            order.append(key)
        else:
            idx, _, cur = best[key]
            if _prefer(zh_value, cur):
                best[key] = (idx, zh_label, zh_value)
    return "\n".join("%s：%s" % (best[k][1], best[k][2]) for k in order)


def render_bullets_es(bullets) -> str:
    """商品卖点（西语原文）：原样多行。"""
    return "\n".join(str(b).strip() for b in bullets or [] if str(b).strip())


def _bullet_zh(b) -> str:
    """单条卖点：词典关键词翻译，未覆盖词保留西语原文（不臆造）。"""
    s = apply_terms(str(b).strip())
    s = re.sub(r"(?<=\d)\s+(?=[克升毫升瓦件磅千米])", "", s)
    return s.strip()


def render_bullets_zh(bullets) -> str:
    """商品卖点（中文）：逐条词典关键词翻译，多行。"""
    return "\n".join(_bullet_zh(b) for b in bullets or [] if str(b).strip())
