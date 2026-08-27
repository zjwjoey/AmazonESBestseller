# -*- coding: utf-8 -*-
"""规格（简式中文）构建与单位校验。

从 prep_v2_selection.py 的 build_spec_v2/_dim_zh/_cap_zh/package_count 抽取，
并按 QA_RULES §36-§45 增强：
  - 尺寸/容量/重量单位类别校验（cm 不能进容量、kg 不能进体积）
  - 占位尺寸 1×1×1cm 拒绝（§45）
  - 件数优先级：选中变体 > 标题显式件数 > numero_de_sets > 技术字段 max（§37-§38）
  - 容量优先级：选中变体（如 30L）> 技术容量字段（§43-§44）
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .text import dec_comma, strip_zero_width

# ---------- 全量详情 attributes → 规格 dict 适配（无损模型 → 旧式 spec 输入） ----------
#: 归一化 label → spec snake_case key 的同义别名（件数/格数多写法聚合）
_SPEC_ALIASES = {
    'numero_de_pieza': 'numero_de_piezas',
    'numero_de_productos': 'numero_de_piezas',
    'numero_de_unidades': 'numero_de_piezas',
    'numero_de_paquetes': 'numero_de_piezas',
    'numero_de_compartimentos': 'cantidad_de_compartimentos',
    'cantidad_de_compartimentos': 'cantidad_de_compartimentos',
}


def _normalize_spec_label(raw) -> str:
    """西语 label → 无重音小写下划线形式（"Número de artículos"→"numero_de_articulos"）。

    去重音（á→a/ñ→n）、括号仅作分隔、空格/斜杠/连字符 → 下划线。归一化后大多
    直接命中 build_spec_v2 的现成 key（numero_de_articulos / tamano / capacidad /
    dimensiones_del_producto / tension / potencia ...）。
    """
    s = unicodedata.normalize('NFD', str(raw or '').lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = s.replace('(', ' ').replace(')', ' ')
    s = re.sub(r'[\s/\-]+', '_', s)
    return re.sub(r'_+', '_', s).strip('_')


def attributes_to_spec_dict(attributes) -> dict:
    """无损全量详情 attributes → 规格 snake_case dict（build_spec_v2 输入）。

    新模型 attributes = [{section, label_raw, value_raw, ...}]（DATA_MODEL §4）；
    build_spec_v2 接受旧式 label→value dict。取第一个非空值，同义别名聚合。
    """
    out: dict = {}
    for a in attributes or []:
        key = _normalize_spec_label(a.get('label_raw'))
        key = _SPEC_ALIASES.get(key, key)
        val = (a.get('value_raw') or '').strip()
        if key and val and key not in out:
            out[key] = val
    return out

# ---------- 容量 ----------
_CAP_TERMS = [
    ('Centímetros cúbicos', '立方厘米'), ('centímetros cúbicos', '立方厘米'),
    ('Litros', '升'), ('litros', '升'), ('Litro', '升'), ('litro', '升'),
    ('Mililitros', '毫升'), ('mililitros', '毫升'), ('Mililitro', '毫升'), ('mililitro', '毫升'),
]
_SHORT_ML_RE = re.compile(r'^([\d.,]+)\s*[mM][lL]\s*$')
_SHORT_L_RE = re.compile(r'^([\d.,]+)\s*[lL]\s*$')


def cap_zh(v) -> str:
    """容量值 → 中文短式（'9 litros'→'9升'，'30L'→'30升'，'300 ml'→'300毫升'）。"""
    if not v:
        return ''
    s = dec_comma(str(v).strip())
    m = _SHORT_ML_RE.match(s)
    if m:
        return '%s毫升' % m.group(1)
    m = _SHORT_L_RE.match(s)
    if m:
        return '%s升' % m.group(1)
    for es, zh in sorted(_CAP_TERMS, key=lambda t: -len(t[0])):
        s = re.sub(r'\b' + re.escape(es) + r'\b', zh, s)
    return re.sub(r'\s+', '', s)


# ---------- 尺寸（V2 逐字移植 + §40 二维简式） ----------
_DIM_RE1 = re.compile(r'^([\d.]+)\s*l\.\s*x\s*([\d.]+)\s*an\.\s*x\s*([\d.]+)\s*al\.\s*(centímetros|milímetros|metros)?', re.I)
_DIM_RE2 = re.compile(r'^([\d.]+)\s*l\.\s*x\s*([\d.]+)\s*an\.\s*(centímetros|milímetros|metros)?', re.I)
_DIM_RE4 = re.compile(r'^([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)\s*(cm|mm|m)?', re.I)
_DIM_RE5 = re.compile(r'^([\d.]+)\s*an\.\s*x\s*([\d.]+)\s*al\.\s*(centímetros|milímetros|metros)?', re.I)
_DIM_RE6 = re.compile(r'^([\d.]+)\s*l\.\s*x\s*([\d.]+)\s*al\.\s*(centímetros|milímetros|metros)?', re.I)
# §40：10×15cm 二维简式（历史回归：10×15cm → 10×10mm 必须永不重现）
_DIM_RE2D = re.compile(r'^([\d.]+)\s*x\s*([\d.]+)\s*(centímetros|milímetros|metros|cm|mm|m)?', re.I)
_DIM_FRAGMENT_RE = re.compile(
    r'((?:[\d.,]+)\s*(?:l\.)?\s*x\s*(?:[\d.,]+)\s*(?:an\.)?\s*x\s*'
    r'(?:[\d.,]+)\s*(?:al\.)?\s*(?:centímetros|milímetros|metros|cm|mm|m)?|'
    r'(?:[\d.,]+)\s*x\s*(?:[\d.,]+)\s*'
    r'(?:centímetros|milímetros|metros|cm|mm|m)?)', re.I)
_UNIT_CN = {'centímetros': '厘米', 'milímetros': '毫米', 'metros': '米', 'cm': '厘米', 'mm': '毫米', 'm': '米', '': ''}


def dim_zh(v) -> Optional[str]:
    """尺寸值 → 简式中文（D2/QA_RULES §40：'10×15 cm'→'10×15厘米'）。"""
    if not v:
        return None
    s = strip_zero_width(dec_comma(str(v).strip())).replace(' ', '').replace('×', 'x')
    m = (_DIM_RE1.match(s) or _DIM_RE2.match(s) or _DIM_RE4.match(s)
         or _DIM_RE5.match(s) or _DIM_RE6.match(s) or _DIM_RE2D.match(s))
    if not m:
        return None
    unit = ''
    for p in m.groups():
        if p and p.lower() in _UNIT_CN:
            unit = _UNIT_CN[p.lower()]
    nums = [g for g in m.groups() if g and g.lower() not in _UNIT_CN]
    return '×'.join(nums) + unit


def is_suspicious_dimension(v) -> bool:
    """占位/无效尺寸检测（QA_RULES §45）：1×1×1cm 类占位拒绝。"""
    if not v:
        return False
    s = strip_zero_width(dec_comma(str(v).strip())).replace(' ', '').replace('×', 'x').lower()
    return bool(re.match(r'^1x1x1(?:cm|centímetros|centimetros|mm|m)?$', s))


# ---------- 单位类别 ----------
def classify_value_unit(s) -> Optional[str]:
    """值 → 单位类别：'capacity' | 'dimension' | 'weight' | None（无法判断）。

    注意：短单位（cm/ml/g/l）与数字紧邻时没有词边界（如 "30cm"），
    需用"数字紧邻单位"模式匹配（QA_RULES §41-§42）。
    """
    if not s:
        return None
    t = re.sub(r'\s+', ' ', str(s).strip()).lower()
    # 西语尺寸简写（l.=largo, an.=ancho, al.=alto）→ dimension
    if re.search(r'\b(?:l|an|al)\.', t):
        return 'dimension'
    # 容量词（长词优先，如 centímetros cúbicos 含 centímetros）+ 数字紧邻短单位
    if re.search(r'\b(?:centímetros?\s+cúbicos?|centimetros?\s+cubicos?|litros?|mililitros?)\b', t):
        return 'capacity'
    if re.search(r'\d\s*(?:l|ml|cc)\b', t):
        return 'capacity'
    # 尺寸词 + 数字紧邻短单位（30cm / 10 x 15 cm）
    if re.search(r'\b(?:centímetros?|milímetros?|metros?)\b', t):
        return 'dimension'
    if re.search(r'\d\s*(?:cm|mm|m)\b', t):
        return 'dimension'
    # 重量词 + 数字紧邻短单位（992g / 2 kg）
    if re.search(r'\b(?:kilogramos?|gramos?|miligramos?|libras?|onzas?)\b', t):
        return 'weight'
    if re.search(r'\d\s*(?:kg|g|lb)\b', t):
        return 'weight'
    return None


def validate_spec_units(field_class: str, raw_value) -> bool:
    """字段应属单位类别 vs 值实际单位类别（QA_RULES §41-§42）。

    capacity 字段不可填 cm/g；dimension 字段不可填 kg/L。无法判断 → 通过。
    """
    actual = classify_value_unit(raw_value)
    if actual is None:
        return True
    return actual == field_class


# ---------- 件数 ----------
_PACKAGE_KEYS = (
    'numero_de_articulos', 'numero_de_piezas',
    'cantidad_de_articulos_en_el_paquete',
    'total_del_paquete_segun_la_medida_elegida_para_referenciar_precio',
)
_COUNT_BEFORE_RE = re.compile(
    r'(\d+)\s*(?:piezas?|unidades?|uds\.?|artículos?|paquetes?|juegos?|pack)\b', re.I)
# 真实锚点："Set 4 Estándar" / "SET 4 PORTAEMBUTIDOS" / "Pack de 2" / "paquete de 6"
# （"set N" 与 "set de N" 两种写法都匹配）
_COUNT_AFTER_RE = re.compile(
    r'\b(?:pack|paquete|juego|set)\s+(?:de\s+)?(\d+)', re.I)


def package_count(d) -> Optional[str]:
    """件数：取多个计数字段的**最大值**（V2 语义；V1 first-match 已弃，QA_RULES §38）。"""
    best = None
    for k in _PACKAGE_KEYS:
        v = d.get(k)
        if v:
            m = re.match(r'([\d.,]+)', str(v).replace(' ', ''))
            if m:
                val = float(m.group(1).replace(',', '.'))
                if best is None or val > best:
                    best = val
    if best is None:
        return None
    return ('%d' % best) if best == int(best) else ('%.2f' % best)


def set_count(d) -> Optional[int]:
    """numero_de_sets → 整数件套数；无/不可解析 → None。"""
    nset = d.get('numero_de_sets')
    if not nset:
        return None
    m = re.match(r'([\d.]+)', str(nset).replace(' ', ''))
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


def _count_from_text(s) -> Optional[int]:
    if not s:
        return None
    t = str(s)
    m = _COUNT_BEFORE_RE.search(t)
    if m:
        return int(m.group(1))
    m = _COUNT_AFTER_RE.search(t)
    if m:
        return int(m.group(1))
    return None


def resolve_package_count(d, variant=None, title_es=None) -> Optional[int]:
    """件数解析优先级（QA_RULES §37-§38 / AGENTS §5）：

    选中变体 > 标题显式件数 > tamano（可靠规格详情，如 "Set 4 Estándar"）
    > numero_de_sets > 技术字段 max。
    泛型数量 1（numero_de_sets=1 / package=1）不产生件数展示。
    """
    if not d:
        return None
    n = _count_from_text(variant)
    if n:
        return n
    n = _count_from_text(title_es)
    if n:
        return n
    n = _count_from_text(d.get('tamano'))
    if n:
        return n
    n = set_count(d)
    if n and n > 1:
        return n
    pc = package_count(d)
    if pc:
        try:
            f = float(pc)
        except ValueError:
            return None
        if f == int(f) and f > 1:
            return int(f)
    return None


# ---------- 字段挑选 ----------
_CAPACITY_KEYS = (
    'capacidad', 'capacidad_de_salida', 'volumen_de_almacenamiento',
    'volumen_del_tanque', 'volumen_liquido',
)
_DIM_KEYS = (
    'dimensiones_del_articulo_largo_x_ancho_x_alto', 'dimensiones_del_producto',
    'dimensiones_articulo', 'dimensiones_del_articulo_l_x_a',
    'dimensiones_del_articulo_ancho_x_alto',
    'dimensiones_del_articulo_profundidad_x_ancho_x_alto',
)
#: 变体容量：30L / 300 ml / 30 l
_VARIANT_CAP_RE = re.compile(r'^([\d.,]+)\s*[mM]?[lL]\s*$')


def _pick_capacity(d, variant=None):
    """容量优先级：选中变体（如 30L）> 技术容量字段（QA_RULES §43-§44）。"""
    if variant:
        m = _VARIANT_CAP_RE.match(str(variant).strip())
        if m:
            return str(variant).strip()
    for k in _CAPACITY_KEYS:
        v = d.get(k)
        if v:
            return v
    return None


def _pick_dimension(d):
    for k in _DIM_KEYS:
        v = d.get(k)
        if v:
            return v
    # Amazon often prefixes a concrete size with text, e.g.
    # ``Tamaño: Cama 90 x 190 x 40 cm``.  Extract only the explicit numeric
    # dimension fragment; never infer a dimension from unrelated title text.
    size = d.get('tamano')
    if size:
        m = _DIM_FRAGMENT_RE.search(str(size))
        if m:
            return m.group(1).strip()
    return None


def _is_set_evidence(d, variant=None, title_es=None) -> bool:
    """计数来源是否为"套件"语义（Set/Pack/Juego/Paquete），决定 件套 vs 件。"""
    for src in (variant, title_es, d.get('tamano') if isinstance(d, dict) else None):
        if src and re.search(r'\b(?:pack|paquete|juego|set)\b', str(src), re.I):
            return True
    return False


def build_spec_v2(d, variant=None, title_es=None) -> str:
    """简短规格（QA_RULES §36）：只回答"客户买的是哪个规格版本"。"""
    d = d if isinstance(d, dict) else {}
    parts = []
    n = resolve_package_count(d, variant, title_es)
    if n:
        nset = set_count(d)
        if nset == n or _is_set_evidence(d, variant, title_es):
            parts.append('%d件套' % n)
        else:
            parts.append('%d件' % n)
    cap = _pick_capacity(d, variant)
    if cap and classify_value_unit(cap) in (None, 'capacity'):
        cz = cap_zh(cap)
        if cz:
            parts.append(cz)
    dim = _pick_dimension(d)
    if dim and not is_suspicious_dimension(dim):
        dz = dim_zh(dim)
        if dz:
            parts.append(dz)
    pot = d.get('potencia')
    if pot:
        parts.append(cap_zh(str(pot).replace('vatios', '瓦').replace('watios', '瓦')))
    volt = d.get('voltaje') or d.get('tension')
    if volt:
        parts.append(cap_zh(str(volt).replace('Voltios', 'V').replace('voltios', 'V')))
    comp = d.get('cantidad_de_compartimentos')
    if comp:
        m = re.match(r'(\d+)', str(comp))
        if m:
            parts.append('%s格' % m.group(1))
    pcs = d.get('numero_de_piezas')
    if pcs and not n:
        m = re.match(r'(\d+)', str(pcs))
        if m and int(m.group(1)) > 1:
            parts.append('%s只' % m.group(1))
    # When Amazon exposes no structured attribute table, retain explicit
    # numeric evidence from the exact title rather than leaving the derived
    # specification silently blank.  This is intentionally conservative:
    # only dimensions/capacity/power/voltage/package-count patterns qualify.
    if not parts and title_es:
        text = str(title_es)
        m = re.search(r'(?<!\w)(\d+(?:[.,]\d+)?\s*(?:x|×)\s*\d+(?:[.,]\d+)?(?:\s*(?:x|×)\s*\d+(?:[.,]\d+)?)?\s*(?:mm|cm|m))(?!\w)', text, re.I)
        if m:
            parts.append(dim_zh(m.group(1).replace(',', '.')) or m.group(1))
        else:
            m = re.search(r'(?<!\w)(\d+(?:[.,]\d+)?)\s*(ml|mililitros?|l|litros?|g|kg|w|vatios?|v|voltios?)(?!\w)', text, re.I)
            if m:
                parts.append(cap_zh((m.group(1) + ' ' + m.group(2)).replace(',', '.')) or m.group(0))
        if not parts:
            m = re.search(r'(?<!\w)(\d+)\s*(?:piezas?|unidades?|uds?)(?!\w)', text, re.I)
            if m and int(m.group(1)) > 1:
                parts.append('%s只' % m.group(1))
    return ' / '.join(parts)


# ---------- 西语规格展示 ----------
# 仅保留页面明确给出的“版本选择”相关字段；不把品牌、材质等完整详情
# 塞进核心规格列。标签和值均保留西语原文，便于回溯页面证据。
_ES_CORE_KEYS = {
    'capacidad', 'capacidad_de_salida', 'volumen_de_almacenamiento',
    'volumen_del_tanque', 'volumen_liquido', 'tamano', 'talla',
    'talla_dimensiones', 'numero_de_articulos', 'numero_de_piezas',
    'numero_de_unidades', 'numero_de_etiquetas', 'numero_de_productos',
    'numero_de_paquetes', 'numero_de_sets',
    'total_del_paquete_segun_la_medida_elegida_para_referenciar_precio',
    'cantidad_de_compartimentos', 'potencia', 'voltaje', 'tension',
}

_ES_CORE_GROUPS = {
    'capacity': 'capacity', 'capacidad_de_salida': 'capacity',
    'volumen_de_almacenamiento': 'capacity', 'volumen_del_tanque': 'capacity',
    'volumen_liquido': 'capacity', 'tamano': 'size', 'talla': 'size',
    'talla_dimensiones': 'size', 'numero_de_articulos': 'count',
    'numero_de_piezas': 'count', 'numero_de_unidades': 'count',
    'numero_de_etiquetas': 'count', 'numero_de_productos': 'count',
    'numero_de_sets': 'count', 'cantidad_de_compartimentos': 'compartments',
    'potencia': 'power', 'voltaje': 'voltage', 'tension': 'voltage',
}


def _is_es_core_key(key: str) -> bool:
    return key in _ES_CORE_KEYS or key.startswith('dimensiones_')


def _es_core_group(key: str) -> str | None:
    if key.startswith('dimensiones_'):
        return 'dimension'
    return _ES_CORE_GROUPS.get(key)


def _is_generic_one_count(key: str, value: str) -> bool:
    if not (key.startswith('numero_de_') or key.startswith('total_del_paquete')):
        return False
    return bool(re.match(r'^1(?:[.,]0)?(?:\s+conteo)?$', value.strip(), re.I))


def build_spec_es(attributes=None, details=None, variant=None) -> str:
    """Build a compact Spanish core-spec display from explicit page evidence.

    ``attributes`` is preferred because it retains the original Spanish label.
    ``details`` is a compatibility fallback for legacy normalized records.  A
    selected variation is only used when no recognized attribute is available;
    no value is inferred from rank, price, or a translated Chinese summary.
    """
    parts = []
    seen = set()
    seen_groups = set()
    for attr in attributes or []:
        if not isinstance(attr, dict):
            continue
        label = str(attr.get('label_raw') or '').strip()
        value = str(attr.get('value_raw') or '').strip()
        key = _normalize_spec_label(label)
        group = _es_core_group(key)
        if not label or not value or not _is_es_core_key(key) or group is None:
            continue
        if group in seen_groups:
            continue
        if _is_generic_one_count(key, value):
            continue
        dedupe = (key, value.casefold())
        if dedupe in seen:
            continue
        seen.add(dedupe)
        seen_groups.add(group)
        parts.append('%s: %s' % (label, value))

    if not parts and isinstance(details, dict):
        for key, value in details.items():
            key_norm = _normalize_spec_label(key)
            value = str(value or '').strip()
            group = _es_core_group(key_norm)
            if (not value or not _is_es_core_key(key_norm) or group is None
                    or group in seen_groups or _is_generic_one_count(key_norm, value)):
                continue
            seen_groups.add(group)
            parts.append('%s: %s' % (key, value))

    if parts:
        return ' / '.join(parts)
    return str(variant or '').strip()
