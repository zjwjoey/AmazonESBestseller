# -*- coding: utf-8 -*-
"""QA 纯校验函数（docs/QA_RULES.md，30 规则版；严重度 P0-P3 见 §1）。

每个校验函数返回 ``(QAStatus, list[QaIssue])``：
  - PASS / 无 issue：记录无此维度问题；
  - WARN：仅 P2/P3 issue（缺失、占位、演示层问题）；
  - FAIL：含 P0/P1 issue（身份、单位错配、品牌误判等）。

issue code 是**实现契约**：全集由 test_regressions.py 的
test_regression_all_issue_codes_implemented 钉死，不再是文档编号目录。
补充码可扩展：RATING_INVALID / REVIEW_COUNT_INVALID / MONTHLY_BOUGHT_UNPARSEABLE。
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

from ..models import QAStatus, QaIssue, normalize_asin
from ..normalization.brand import is_brand_suspicious
from ..normalization.monthly_bought import parse_monthly_bought
from ..normalization.price import parse_price
from ..normalization.specification import (
    classify_value_unit,
    is_suspicious_dimension,
    package_count,
)
from ..normalization.text import dec_comma
from ..translation.product_type import detect_product_type

_ASIN_RE = re.compile(r'^[A-Z0-9]{10}$')

#: 中文标题允许保留的拉丁词（型号/接口/标准/生态名）
TITLE_WHITELIST = {
    'USB', 'E27', 'SDS', 'HEPA', 'HSS', 'PTFE', 'ABS', 'BPA',
    'Dyson', 'Dedica', 'Nespresso', 'Original', 'Dolce', 'Gusto',
}

_CAPACITY_KEYS_QA = (
    'capacidad', 'capacidad_de_salida', 'volumen_de_almacenamiento',
    'volumen_del_tanque', 'volumen_liquido',
)
_DIM_KEYS_QA = (
    'dimensiones_del_articulo_largo_x_ancho_x_alto', 'dimensiones_del_producto',
    'dimensiones_articulo', 'dimensiones_del_articulo_l_x_a',
    'dimensiones_del_articulo_ancho_x_alto',
)
_VARIANT_CAP_RE = re.compile(r'^([\d.,]+)\s*[mM]?[lL]\s*$')


def _worst_status(issues: List[QaIssue]) -> QAStatus:
    """issue 列表 → 状态：任一 P0/P1 → FAIL；有 P2/P3 → WARN；无 → PASS。"""
    if not issues:
        return QAStatus.PASS
    if any(i.severity in ('P0', 'P1') for i in issues):
        return QAStatus.FAIL
    return QAStatus.WARN


def _issue(code, severity, field, message) -> QaIssue:
    return QaIssue(code, severity, field, message)


def validate_asin(asin) -> Tuple[QAStatus, List[QaIssue]]:
    """ASIN 格式（QA_RULES §2）：缺失或不合规 → FAIL ASIN_INVALID P0。"""
    a = normalize_asin(asin)
    if not _ASIN_RE.match(a):
        return QAStatus.FAIL, [_issue(
            'ASIN_INVALID', 'P0', 'asin',
            'ASIN 缺失或格式非法: %r' % (asin,))]
    return QAStatus.PASS, []


def _asin_from_url(url) -> Optional[str]:
    if not url:
        return None
    m = re.search(r'/(?:dp|gp/product|product)/([A-Z0-9]{10})', str(url), re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r'/([A-Z0-9]{10})(?:[/?#]|$)', str(url))
    if m:
        return m.group(1).upper()
    return None


def validate_url_asin(asin, url) -> Tuple[QAStatus, List[QaIssue]]:
    """URL 携带同一 ASIN（QA_RULES §2）：指向不同 ASIN → FAIL URL_ASIN_MISMATCH P0。"""
    a = normalize_asin(asin)
    u = _asin_from_url(url)
    if u and a and u != a:
        return QAStatus.FAIL, [_issue(
            'URL_ASIN_MISMATCH', 'P0', 'product_url',
            'URL 指向的 ASIN %s 与记录 %s 不一致' % (u, a))]
    return QAStatus.PASS, []


def validate_image_asin(asin, image_asin=None) -> Tuple[QAStatus, List[QaIssue]]:
    """图片归属 ASIN（QA_RULES §21）：已核对的 image_asin 与记录不符 → FAIL P0。"""
    a = normalize_asin(asin)
    ia = normalize_asin(image_asin)
    if a and ia and ia != a:
        return QAStatus.FAIL, [_issue(
            'IMAGE_ASIN_MISMATCH', 'P0', 'image_url',
            '图片归属 ASIN %s 与记录 %s 不一致' % (ia, a))]
    return QAStatus.PASS, []


def validate_price(current_price, original_price=None, currency=None,
                   discount_rate=None) -> Tuple[QAStatus, List[QaIssue]]:
    """价格有效性（QA_RULES §7）。"""
    issues: List[QaIssue] = []
    cur = parse_price(current_price)
    if current_price not in (None, '') and cur is None:
        issues.append(_issue(
            'PRICE_INVALID', 'P1', 'current_price',
            '现价无法解析为 >0 的有效金额: %r' % (current_price,)))
    if currency and str(currency).strip().upper() != 'EUR':
        issues.append(_issue('PRICE_INVALID', 'P1', 'currency', '货币非 EUR'))
    # 折扣必须有原价证据且原价 > 现价（QA_RULES §7，禁止重建）
    if discount_rate not in (None, '', 0, '0', '0.0'):
        orig = parse_price(original_price)
        if orig is None or (cur is not None and orig <= cur):
            issues.append(_issue(
                'PRICE_INVALID', 'P1', 'discount_rate',
                '折扣缺少原价证据或原价≤现价'))
    return _worst_status(issues), issues


def _parse_rating_num(s: str) -> Optional[float]:
    """评分数值（西语小数逗号；"4.5" 点号亦接受）。"""
    t = s.strip()
    if ',' in t:
        return float(t.replace(',', '.'))
    return float(t)


def validate_rating(rating_raw) -> Tuple[QAStatus, List[QaIssue]]:
    """评分范围 0-5（QA_RULES §8）：越界/无法解析 → FAIL RATING_INVALID P1。"""
    if rating_raw in (None, ''):
        return QAStatus.PASS, []
    m = re.match(r'^([\d.,]+)', str(rating_raw).strip())
    if not m:
        return QAStatus.FAIL, [_issue(
            'RATING_INVALID', 'P1', 'rating', '评分文本无法解析: %r' % (rating_raw,))]
    try:
        val = _parse_rating_num(m.group(1))
    except ValueError:
        return QAStatus.FAIL, [_issue(
            'RATING_INVALID', 'P1', 'rating', '评分文本无法解析: %r' % (rating_raw,))]
    if not (0 <= val <= 5):
        return QAStatus.FAIL, [_issue(
            'RATING_INVALID', 'P1', 'rating', '评分越界 0-5: %r' % (rating_raw,))]
    return QAStatus.PASS, []


def _to_int_spanish(s: str) -> Optional[int]:
    """西语整数（. 千位分隔）→ int；"3.873"→3873、"1.500"→1500。"""
    t = s.strip()
    if not t:
        return None
    try:
        if '.' in t:
            return int(t.replace('.', ''))
        return int(t)
    except ValueError:
        return None


def validate_review_count(raw) -> Tuple[QAStatus, List[QaIssue]]:
    """评论数数值化（QA_RULES §8）：西语千分位点须当千位，非小数。"""
    if raw in (None, ''):
        return QAStatus.PASS, []
    m = re.match(r'^\(?\s*([\d.,]+)', str(raw).strip())
    if not m:
        return QAStatus.FAIL, [_issue(
            'REVIEW_COUNT_INVALID', 'P1', 'review_count',
            '评论数无法解析: %r' % (raw,))]
    n = _to_int_spanish(m.group(1))
    if n is None:
        return QAStatus.FAIL, [_issue(
            'REVIEW_COUNT_INVALID', 'P1', 'review_count',
            '评论数无法解析: %r' % (raw,))]
    return QAStatus.PASS, []


def validate_brand(brand, brand_raw=None) -> Tuple[QAStatus, List[QaIssue]]:
    """品牌证据（QA_RULES §10：宁缺毋假，缺失优于误判）。"""
    b = (brand or '').strip()
    if not b:
        return QAStatus.WARN, [_issue(
            'BRAND_MISSING', 'P2', 'brand', '品牌缺失（缺失优于误判）')]
    if is_brand_suspicious(b):
        return QAStatus.FAIL, [_issue(
            'BRAND_FALSE_POSITIVE', 'P1', 'brand',
            '品牌为西语普通名词或标题片段，疑似误判: %r' % (b,))]
    rb = (brand_raw or '').strip()
    if rb and ('Marca:' in rb or 'Visita la tienda de' in rb):
        return QAStatus.FAIL, [_issue(
            'BRAND_FALSE_POSITIVE', 'P1', 'brand_raw',
            '品牌原始文本未清理显示前缀: %r' % (rb,))]
    return QAStatus.PASS, []


def validate_spec(record) -> Tuple[QAStatus, List[QaIssue]]:
    """规格单位/占位/件数（QA_RULES §12/§13）。"""
    issues: List[QaIssue] = []
    details = record.get('details_json')
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except Exception:
            details = None
    if isinstance(details, dict):
        for k in _CAPACITY_KEYS_QA:
            v = details.get(k)
            if v and classify_value_unit(v) in ('dimension', 'weight'):
                issues.append(_issue(
                    'SPEC_UNIT_MISMATCH', 'P1', 'spec',
                    '容量字段填入了非容量单位: %s = %s' % (k, v)))
        for k in _DIM_KEYS_QA:
            v = details.get(k)
            if not v:
                continue
            if classify_value_unit(v) in ('capacity', 'weight'):
                issues.append(_issue(
                    'SPEC_UNIT_MISMATCH', 'P1', 'spec',
                    '尺寸字段填入了非尺寸单位: %s = %s' % (k, v)))
            elif is_suspicious_dimension(v):
                issues.append(_issue(
                    'SPEC_SUSPICIOUS_VALUE', 'P2', 'spec',
                    '尺寸疑似占位值: %s' % (v,)))
        # 件数冲突（§12）：标题显式 N 件套 vs 泛型 package 数量 1
        title_es = record.get('title_es_raw') or ''
        m = re.search(r'(\d+)\s*(?:piezas?|unidades?|uds\.?|artículos?)\b', title_es, re.I)
        if m and int(m.group(1)) > 1:
            pc = package_count(details)
            if pc and float(pc) == 1:
                issues.append(_issue(
                    'SPEC_QUANTITY_CONFLICT', 'P1', 'spec',
                    '标题 %s 件套被泛型 package 数量 1 覆盖' % m.group(1)))
    spec_str = record.get('spec_v2') or record.get('specification') or ''
    # 变体冲突（§19/§12）：选中变体容量应反映到规格输出
    variant = record.get('selected_variation_raw')
    if variant:
        vm = _VARIANT_CAP_RE.match(str(variant).strip())
        if vm:
            spec_digits = re.sub(r'[升毫升]', '', spec_str)
            if dec_comma(vm.group(1)) not in spec_digits:
                issues.append(_issue(
                    'SPEC_VARIANT_MISMATCH', 'P1', 'spec',
                    '选中变体 %s 未反映到规格输出: %s' % (variant, spec_str)))
    if '1×1×1' in spec_str or '1x1x1' in spec_str:
        issues.append(_issue(
            'SPEC_SUSPICIOUS_VALUE', 'P2', 'spec', '规格含占位尺寸 1×1×1'))
    return _worst_status(issues), issues


def validate_rank_separation(record) -> Tuple[QAStatus, List[QaIssue]]:
    """榜单排名与详情 BSR 隔离（QA_RULES §5/§12）：绝不混用。

    混用判定：bestseller_rank **缺少独立榜单来源**（ranking_source_url /
    collected_at）且数值恰好出现在 detail_bsr_segments —— 这才可疑为来自
    详情 BSR 的旧构造模式。有榜单来源上下文时，同一商品在两个不同榜单
    上下文中排名数值碰巧相等（如既是某子类榜单第 1、详情 BSR 也是第 1）
    是合法现象，不做数值比较。
    """
    br = record.get('bestseller_rank')
    if br in (None, ''):
        return QAStatus.PASS, []
    try:
        br_int = int(float(str(br).strip().replace(' ', '').replace('.', '')))
    except (ValueError, TypeError):
        return QAStatus.PASS, []

    segs = record.get('detail_bsr_segments')
    ranks: List[int] = []
    if isinstance(segs, list):
        for item in segs:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                try:
                    ranks.append(int(item[1]))
                except (ValueError, TypeError):
                    pass
            else:
                try:
                    ranks.append(int(item))
                except (ValueError, TypeError):
                    pass
    elif segs is None:
        raw = record.get('detail_bsr_raw')
        if isinstance(raw, str):
            for n in re.findall(r'([\d.,]+)', raw):
                v = _to_int_spanish(n)
                if v is not None:
                    ranks.append(v)
        elif isinstance(raw, (int, float)):
            ranks.append(int(raw))

    has_source = bool(record.get('ranking_source_url') or record.get('collected_at'))
    if not has_source and br_int in ranks:
        return QAStatus.FAIL, [_issue(
            'RANK_BSR_MIXED', 'P0', 'bestseller_rank',
            '榜单排名 %s 无独立榜单来源且与详情 BSR 数值重合，疑似污染' % br_int)]
    # 排名存在但来源上下文缺失 → WARN（不臆造来源）
    if not has_source:
        return QAStatus.WARN, [_issue(
            'RANK_SOURCE_MISSING', 'P2', 'bestseller_rank',
            '排名缺少来源上下文（ranking_source_url / collected_at）')]
    return QAStatus.PASS, []


def validate_category(record) -> Tuple[QAStatus, List[QaIssue]]:
    """类目层级（QA_RULES §6/§13）。

    复制充数只指路径相邻槽位同名（L1=L2 或 L2=L3）；leaf==L3 是节点路径
    恰为 3 级时的定义恒等（B1），不判重复。未知 deeper 层级必须为 null。
    """
    issues: List[QaIssue] = []
    l1 = record.get('category_l1')
    l2 = record.get('category_l2')
    l3 = record.get('category_l3')
    leaf = record.get('leaf_category')
    for a, b, fld in ((l1, l2, 'category_l2'), (l2, l3, 'category_l3')):
        if a not in (None, '') and b not in (None, '') and a == b:
            issues.append(_issue(
                'CATEGORY_DUPLICATED_LEVEL', 'P1', fld,
                '类目层级重复复制: %s' % (b,)))
    if leaf not in (None, '') and not (record.get('browse_node_id')
                                       or record.get('ranking_source_url')):
        issues.append(_issue(
            'CATEGORY_UNVERIFIED_LEAF', 'P2', 'leaf_category',
            'leaf 类目缺少榜单证据，无法核实'))
    return _worst_status(issues), issues


def validate_bilingual_match(record) -> Tuple[QAStatus, List[QaIssue]]:
    """中西对照：残留拉丁文本与品牌重复（QA_RULES §3/§16）。"""
    title_zh = record.get('title_zh') or record.get('title_zh_cn') or ''
    if not title_zh:
        return QAStatus.PASS, []
    issues: List[QaIssue] = []
    residue = []
    for tok in re.findall(r'[A-Za-z][A-Za-z0-9]+', str(title_zh)):
        if tok in TITLE_WHITELIST:
            continue
        if tok.isupper() or any(ch.isdigit() for ch in tok):
            continue  # 全大写缩写 / 型号
        residue.append(tok)
    if residue:
        issues.append(_issue(
            'TITLE_UNTRANSLATED_TEXT', 'P3', 'title_zh',
            '中文标题残留未翻译拉丁文本: %s' % ','.join(residue)))
    brand = record.get('brand')
    if brand and str(title_zh).strip().startswith(str(brand).strip()):
        issues.append(_issue(
            'TITLE_BRAND_DUPLICATION', 'P3', 'title_zh',
            '中文标题重复品牌 %s（品牌已单独存列）' % brand))
    return _worst_status(issues), issues


def validate_source_conflict(record) -> Tuple[QAStatus, List[QaIssue]]:
    """标题 vs 细节证据商品类型冲突（QA_RULES §20）。"""
    title_es = record.get('title_es_raw')
    pt_title = detect_product_type(title_es) if title_es else None
    if not pt_title:
        return QAStatus.PASS, []
    # 记录的商品类型（业务派生字段）与标题证据矛盾 → P0
    recorded_pt = record.get('product_type')
    if recorded_pt and recorded_pt != pt_title:
        return QAStatus.FAIL, [_issue(
            'TITLE_PRODUCT_TYPE_MISMATCH', 'P0', 'product_type',
            '记录的商品类型 %s 与标题证据 %s 不一致' % (recorded_pt, pt_title))]
    evidence = (record.get('details_json') or record.get('summary_v2')
                or record.get('spec_v2') or '')
    if isinstance(evidence, dict):
        evidence = json.dumps(evidence, ensure_ascii=False)
    pt_ev = detect_product_type(evidence)
    if pt_ev and pt_ev != pt_title:
        return QAStatus.SOURCE_CONFLICT, [_issue(
            'SOURCE_CONFLICT', 'P0', 'product_type',
            '标题商品类型 %s 与细节证据 %s 冲突，需人工裁决' % (pt_title, pt_ev))]
    return QAStatus.PASS, []


def validate_monthly_bought(record) -> Tuple[QAStatus, List[QaIssue]]:
    """月购下限与原始文本一致（QA_RULES §9）。"""
    raw = record.get('monthly_bought_raw')
    mn = record.get('monthly_bought_min')
    if raw in (None, ''):
        return QAStatus.PASS, []
    parsed = parse_monthly_bought(raw)
    if parsed is None:
        return QAStatus.FAIL, [_issue(
            'MONTHLY_BOUGHT_UNPARSEABLE', 'P2', 'monthly_bought_min',
            '月购文本存在但无法解析: %r' % (raw,))]
    if mn not in (None, '') and int(mn) != parsed:
        return QAStatus.FAIL, [_issue(
            'MONTHLY_BOUGHT_UNPARSEABLE', 'P2', 'monthly_bought_min',
            '月购下限 %s 与原始文本解析 %s 不一致' % (mn, parsed))]
    return QAStatus.PASS, []
