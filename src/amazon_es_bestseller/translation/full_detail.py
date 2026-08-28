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
import unicodedata

from ..normalization.text import strip_zero_width
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
    "opiniones de los clientes",
)


_TRUNC_MARK = "… Ver más"

#: Amazon 折叠控件的按钮文字（展开/收起），本身不是商品属性内容
_VER_MAS_RE = re.compile(r"\s*(?:…|\.\.\.)?\s*Ver\s+(?:más|mas|menos)\s*$", re.I)

#: 零信息占位值：Amazon 明确写"未知/不适用"，展示层删除，数据层保留
_PLACEHOLDER_VALUES = frozenset({
    "desconocido", "desconocida", "no aplicable", "n/a", "na",
    "sin especificar", "no disponible", "not applicable", "unknown",
})


def expand_collapsed_value(value) -> tuple[str, bool]:
    """折叠属性值 → ``(完整文本, 是否仍为截断)``。

    Amazon 的折叠控件会把同一段文字渲染两遍（展开版 + 折叠版）并跟一个
    "Ver más" 按钮，形如 ``"<全文> <全文> Ver más"``。完整文本就在其中，
    去掉重复即可还原（真实证据 B00EOOQD0O / B09YRD4GDR 等 5 处）。

    若去掉按钮文字后剩下的内容无法还原出完整版本（后半段不是前半段的前缀），
    说明页面只给了截断文本：返回可见文本并标记为截断，绝不冒充完整。
    """
    text = str(value or "").strip()
    stripped = _VER_MAS_RE.sub("", text).strip()
    if stripped == text:
        return text, False          # 没有折叠控件，原样返回
    n = len(stripped)
    for cut in range(n // 2, n):
        head, tail = stripped[:cut].strip(), stripped[cut:].strip()
        if tail and head.startswith(tail):
            return head, False      # 重复渲染，完整版本可还原
    return stripped, True           # 只有截断版本


def _is_placeholder(value: str) -> bool:
    return value.strip().casefold().rstrip(".") in _PLACEHOLDER_VALUES


def truncated_detail_labels(attributes) -> list:
    """仍然只有截断文本的属性标签（供 QA/审计标记 DETAIL_TRUNCATED）。"""
    out = []
    for a in attributes or []:
        label = strip_zero_width(a.get("label_raw") or "").strip()
        value = strip_zero_width(a.get("value_raw") or "")
        if not label or not value:
            continue
        _, truncated = expand_collapsed_value(value)
        if truncated and label not in out:
            out.append(label)
    return out


def clean_display_zh(text: str) -> str:
    """Remove known Amazon/MT presentation artefacts from Chinese display text.

    This operates only on the derived display layer.  Raw Spanish attributes and
    the original DS response remain untouched for auditability.  Unknown source
    values are not guessed; only explicit placeholders and machine-generated
    count wording are normalized.
    """
    if text is None:
        return ""
    out = []
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Amazon's expandable-value markers are UI chrome, not product data.
        line = re.sub(r"(?:…|\.\.\.)?\s*(?:Ver\s+m[aá]s|查看更多)", "", line,
                      flags=re.I)
        # Placeholder metadata has no selection value.  Drop the whole row,
        # including its label, when Amazon supplied an unknown update date.
        low = line.casefold()
        if (("软件更新保证" in line and ("未知" in line or "不详" in line))
                or ("actualizaciones de software" in low
                    and any(x in low for x in ("desconocido", "unknown", "未知")))):
            continue
        # French placeholder occasionally survives the Spanish→Chinese pass.
        if "voir descriptif" in low:
            continue
        # Translate/count-normalize machine output such as ``10.0 计数`` or
        # ``10.0 Conteo``.  A decimal .0 is not meaningful for item counts.
        def _count(m):
            n = m.group(1).replace(",", ".")
            try:
                f = float(n)
                n = str(int(f)) if f.is_integer() else str(f).rstrip("0").rstrip(".")
            except ValueError:
                pass
            return n + "件"
        line = re.sub(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(?:计数|conteo|count)\b",
                      _count, line, flags=re.I)
        # Existing translations often use a decimal before a Chinese unit.
        line = re.sub(r"(?<!\w)(\d+)\.0\s*(?=[件个只套粒片])", r"\1", line)
        # ``未知修饰符`` is an explicit parser/translation placeholder.  Keep
        # a real numeric value (e.g. ``产品体积：10``), but drop an empty row.
        line = re.sub(r"\s*(?:未知修饰符|modificador desconocido)\b", "", line,
                      flags=re.I)
        line = re.sub(r"\s{2,}", " ", line).strip(" ：:;；,，")
        if not line:
            continue
        # If cleanup left only a label, it carries no usable value.
        if re.fullmatch(r"[^：:]{1,40}[：:]?", line):
            if "：" in line or ":" in line:
                continue
        out.append(line)
    return "\n".join(out)


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
        # Amazon 的 technical_details 表在文本前插入 LEFT-TO-RIGHT MARK 等
        # 不可见字符。数据层保留原文，展示层必须剥离——否则不可见字符会被
        # 直接写进 Excel 单元格（真实证据 B00889569A / B0BVMKDD7T）。
        label = strip_zero_width(a.get("label_raw") or "")
        value = strip_zero_width(a.get("value_raw") or "")
        if not label or not value:
            continue
        if label.lower() in _META_LABELS:
            continue
        # 折叠控件：还原完整文本，去掉 "Ver más" 按钮文字
        value, _ = expand_collapsed_value(value)
        # 零信息占位（"desconocido" / "No aplicable"）不进展示层
        if not value or _is_placeholder(value):
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


def detail_bullets_to_attributes(detail_bullets) -> list:
    """Parse Amazon's visible ``detailBullets`` metadata into raw attributes.

    Some modern/marketplace pages omit both Product Overview and Technical
    Details tables but still expose key/value metadata (dimensions, model,
    manufacturer, origin) in the visible detail bullets.  Preserve those
    explicit pairs as a fallback; ranking/review rows remain filtered at the
    display layer just like table metadata.
    """
    rows = []
    for value in detail_bullets or []:
        text = str(value or "").replace("\u200f", "").replace("\u200e", "").strip()
        if not text or ":" not in text:
            continue
        label, raw_value = text.split(":", 1)
        label = re.sub(r"\s+", " ", label).strip()
        raw_value = re.sub(r"\s+", " ", raw_value).strip()
        if label and raw_value:
            rows.append({"section": "additional_information", "label_raw": label,
                         "value_raw": raw_value, "position": len(rows),
                         "source": "detailBullets"})
    return rows


def _humanize_es_label(label: str) -> str:
    """Turn legacy internal snake_case labels into readable Spanish labels."""
    text = str(label or "").replace("_", " ").strip()
    if not text:
        return text
    key = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    for candidate in LABEL_ES_ZH:
        ckey = unicodedata.normalize("NFKD", candidate).encode("ascii", "ignore").decode().casefold()
        if ckey == key:
            return candidate[:1].upper() + candidate[1:]
    return text[:1].upper() + text[1:]


def render_details_es(attributes) -> str:
    """完整商品详情（西语原文）：逐行 ``label: value``（多行，供 Excel WRAP 单元格）。"""
    rows = _display_rows(attributes)
    if not rows:
        return ""
    return "\n".join("%s: %s" % (_humanize_es_label(label), value) for label, value in rows)


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
        label = _humanize_es_label(label)
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
    return clean_display_zh("\n".join("%s：%s" % (best[k][1], best[k][2]) for k in order))


def render_bullets_es(bullets) -> str:
    """商品卖点（西语原文）：原样多行。"""
    return "\n".join(str(b).strip() for b in bullets or [] if str(b).strip())


def _bullet_zh(b) -> str:
    """单条卖点：词典关键词翻译，未覆盖词保留西语原文（不臆造）。"""
    s = apply_terms(str(b).strip())
    s = re.sub(r"(?<=\d)\s+(?=[克升毫升瓦件磅千米])", "", s)
    return clean_display_zh(s)


def render_bullets_zh(bullets) -> str:
    """商品卖点（中文）：逐条词典关键词翻译，多行。"""
    return "\n".join(_bullet_zh(b) for b in bullets or [] if str(b).strip())
