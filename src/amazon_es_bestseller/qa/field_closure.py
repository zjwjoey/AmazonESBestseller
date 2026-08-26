# -*- coding: utf-8 -*-
"""End-to-end Source → Raw → Canonical → Derived → Display audit.

This module is deliberately read-only.  It diagnoses why an automatic display
field is empty; it never fills a value and never uses a different field as a
guess.  Saved HTML is optional, but when supplied it allows a missing raw value
to be distinguished from a page that did not expose the field.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ..models import normalize_asin
from ..normalization.price import discount_rate, parse_price


PASS = "PASS"
SOURCE_MISSING = "SOURCE_MISSING"
PARSER_MISSED = "PARSER_MISSED"
MAPPING_MISSED = "MAPPING_MISSED"
DERIVED_MISSING = "DERIVED_MISSING"


@dataclass(frozen=True)
class FieldClosureResult:
    asin: str
    field: str
    display_column: str
    source_status: str
    raw_status: str
    canonical_status: str
    derived_status: str
    display_status: str
    classification: str
    severity: str
    source_evidence: Any
    raw_evidence: Any
    canonical_value: Any
    derived_value: Any
    display_value: Any
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


# Field names intentionally correspond to the frozen Chinese 26-column contract.
FIELD_COLUMNS = {
    "asin": "ASIN",
    "parent_asin": "Parent ASIN",
    "title_zh": "商品名称（中文）",
    "brand": "品牌",
    "current_price": "当前售价",
    "original_price": "划线原价",
    "discount_rate": "折扣率",
    "rating": "评分",
    "review_count": "评论数",
    "monthly_bought_min": "月购买量",
    "category_l1": "一级类目",
    "category_l2": "二级类目",
    "category_l3": "三级类目",
    "leaf_category": "细分类目",
    "bestseller_rank": "畅销榜排名",
    "selected_variation_raw": "当前选中规格 / 变体",
    "spec_v2": "核心规格（中文）",
    "product_details_zh": "完整商品详情（中文）",
    "feature_bullets_zh": "商品卖点（中文）",
    "date_first_available": "首次上架日期",
    "seller": "卖家",
    "product_url": "商品链接",
    "image_url": "图片链接",
}

_FIELD_ORDER = tuple(FIELD_COLUMNS)
_HTML_PATTERNS = {
    "parent_asin": (r"parent[-_ ]?asin", r"asin de padre", r"asin padre"),
    "title_zh": (r"id=[\"']producttitle",),
    "brand": (r"bylineinfo", r"\bmarca\b", r"\bbrand\b"),
    "current_price": (r"coreprice", r"pricetopay", r"a-price"),
    "original_price": (r"data-a-strike\s*=", r"priceblock_listprice", r"listprice"),
    "discount_rate": (r"data-a-strike\s*=", r"listprice"),
    "rating": (r"acrpopover", r"averagecustomerreviews", r"estrellas"),
    "review_count": (r"acrcustomerreviewtext", r"opiniones de clientes"),
    "monthly_bought_min": (r"comprados el mes pasado", r"comprado[s]? el último mes"),
    "category_l1": (r"breadcrumb", r"zgbs", r"browse node", r"browse_node"),
    "category_l2": (r"breadcrumb", r"zgbs", r"browse node", r"browse_node"),
    "category_l3": (r"breadcrumb", r"zgbs", r"browse node", r"browse_node"),
    "leaf_category": (r"breadcrumb", r"zgbs", r"browse node", r"browse_node"),
    "bestseller_rank": (r"best sellers rank", r"más vendidos", r"n\.?º\s*[\d.,]+\s+en"),
    "selected_variation_raw": (r"variation", r"twister", r"seleccionado"),
    "spec_v2": (r"productdetails", r"proddetails", r"capacidad", r"dimensiones", r"tamaño"),
    "product_details_zh": (r"productdetails", r"proddetails", r"technical details", r"características"),
    "feature_bullets_zh": (r"feature-bullets", r"about this item", r"acerca de este producto"),
    "date_first_available": (r"fecha de primera disponibilidad", r"date first available"),
    "seller": (r"merchantinfo", r"sellerprofile", r"vendido por", r"sold by"),
    "product_url": (r"/dp/[a-z0-9]{10}",),
    "image_url": (r"landingimage", r"mainimage", r"imageblock", r"\.jpg", r"\.png"),
}


def _has(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _attributes(record: Mapping, detail: Mapping) -> list:
    attrs = record.get("attributes")
    if attrs:
        return attrs if isinstance(attrs, list) else []
    attrs = detail.get("attributes")
    return attrs if isinstance(attrs, list) else []


def _attribute_brand(attrs: Iterable[Mapping]) -> str:
    for item in attrs:
        label = _text(item.get("label_raw")).casefold()
        if label in {"marca", "brand"} and _has(item.get("value_raw")):
            return _text(item["value_raw"])
    return ""


def _find_html(html_dir: Optional[str | Path], asin: str) -> str:
    if not html_dir:
        return ""
    root = Path(html_dir)
    if not root.is_dir():
        return ""
    candidates = [root / f"{asin}.html", root / f"{asin.upper()}.html", root / f"{asin.lower()}.html"]
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    for path in sorted(root.rglob("*.html")):
        if asin.casefold() in path.stem.casefold():
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
    return ""


def _source_evidence(field: str, record: Mapping, detail: Mapping, ranking: Mapping,
                     html: str) -> tuple[bool, Any]:
    evidence: list[str] = []
    if field == "brand":
        if _has(detail.get("brand_raw")) or _has(record.get("brand_raw")):
            evidence.append("brand_raw")
        if _attribute_brand(_attributes(record, detail)):
            evidence.append("attributes:Marca/Brand")
    elif field.startswith("category_") or field == "leaf_category":
        # A single root category is not evidence that deeper levels exist.  A
        # breadcrumb/path or browse node is evidence for parsing missing levels;
        # a ranking URL alone is only context, not category content.
        if _has(ranking.get(field)) or _has(record.get(field)):
            evidence.append(field)
        if _has(ranking.get("category_path_raw")) or _has(ranking.get("browse_node_id")):
            evidence.append("ranking.category_path")
        if html and any(re.search(p, html, re.I) for p in _HTML_PATTERNS.get(field, ())):
            evidence.append("html:category_path")
    elif field == "bestseller_rank":
        if _has(ranking.get("bestseller_rank")) or (_has(record.get("bestseller_rank")) and
                                                    (_has(record.get("ranking_source_url")) or _has(record.get("collected_at")))):
            evidence.append("ranking.bestseller_rank")
    elif field in {"title_zh", "brand", "current_price", "original_price", "rating",
                   "review_count", "monthly_bought_min", "parent_asin", "selected_variation_raw",
                   "date_first_available", "seller", "spec_v2", "product_details_zh",
                   "feature_bullets_zh"}:
        # Individual raw evidence is handled below; this branch intentionally does not
        # treat a derived field itself as source evidence.
        pass
    else:
        if _has(detail.get(field)) or _has(record.get(field)):
            evidence.append(field)
    if html:
        patterns = _HTML_PATTERNS.get(field, ())
        matched = [p for p in patterns if re.search(p, html, re.I)]
        evidence.extend(f"html:{p}" for p in matched)
    return bool(evidence), evidence


def _raw_value(field: str, record: Mapping, detail: Mapping, ranking: Mapping) -> Any:
    if field == "asin":
        return record.get("asin") or detail.get("asin") or ranking.get("asin")
    if field == "brand":
        return detail.get("brand_raw") or record.get("brand_raw") or _attribute_brand(_attributes(record, detail))
    if field == "title_zh":
        return detail.get("title_es_raw") or record.get("title_es_raw")
    if field in {"current_price", "original_price", "rating", "review_count", "monthly_bought_min"}:
        raw_key = {"current_price": "current_price_raw", "original_price": "original_price_raw",
                   "rating": "rating_raw", "review_count": "review_count_raw",
                   "monthly_bought_min": "monthly_bought_raw"}[field]
        return detail.get(raw_key) or record.get(raw_key)
    if field.startswith("category_") or field == "leaf_category":
        return ranking.get(field) if _has(ranking.get(field)) else record.get(field)
    if field == "bestseller_rank":
        if _has(ranking.get("bestseller_rank")):
            return ranking.get("bestseller_rank")
        if _has(record.get("ranking_source_url")) or _has(record.get("collected_at")):
            return record.get("bestseller_rank")
        return None
    if field in {"spec_v2", "product_details_zh"}:
        return _attributes(record, detail) or detail.get("details_json") or record.get("details_json")
    if field == "feature_bullets_zh":
        return detail.get("feature_bullets_raw") or record.get("feature_bullets_raw")
    if field == "date_first_available":
        return detail.get("date_first_available_raw") or record.get("date_first_available_raw")
    if field == "seller":
        return detail.get("seller_raw") or record.get("seller_raw")
    if field == "parent_asin":
        return detail.get("parent_asin") or record.get("parent_asin_raw")
    if field == "selected_variation_raw":
        return detail.get("selected_variation_raw") or record.get("selected_variation_raw")
    return detail.get(field) or record.get(field)


def _canonical_value(field: str, record: Mapping) -> Any:
    if field == "seller":
        return record.get("seller") or record.get("seller_raw")
    return record.get(field)


def _derived_value(field: str, record: Mapping) -> Any:
    if field == "title_zh":
        return record.get("title_zh")
    if field == "spec_v2":
        return record.get("spec_v2") or record.get("specification")
    if field == "product_details_zh":
        return record.get("product_details_zh")
    if field == "feature_bullets_zh":
        return record.get("feature_bullets_zh")
    if field == "discount_rate":
        return record.get("discount_rate")
    return _canonical_value(field, record)


def _display_value(field: str, record: Mapping) -> Any:
    return _canonical_value(field, record)


def _classify(field: str, source: bool, raw: Any, canonical: Any, derived: Any,
              display: Any, raw_evidence: Any) -> tuple[str, str, str]:
    if _has(display):
        return PASS, "INFO", "字段已闭环进入展示层"
    if not source:
        return SOURCE_MISSING, "P2", "当前页面/榜单未发现可靠来源证据"
    if not _has(raw):
        return PARSER_MISSED, "P1", "来源证据存在，但 collector 未保存 raw 字段"
    if not _has(canonical):
        return MAPPING_MISSED, "P1", "raw 证据存在，但未映射到 canonical 字段"
    if not _has(derived) or not _has(display):
        return DERIVED_MISSING, "P1", "raw/canonical 存在，但派生或展示字段为空"
    return PASS, "INFO", "字段已闭环进入展示层"


def _audit_one(field: str, asin: str, record: Mapping, detail: Mapping,
               ranking: Mapping, html: str) -> FieldClosureResult:
    source, source_evidence = _source_evidence(field, record, detail, ranking, html)
    raw = _raw_value(field, record, detail, ranking)
    if _has(raw) and not source:
        source = True
        source_evidence = ["raw_input"]
    canonical = _canonical_value(field, record)
    derived = _derived_value(field, record)
    display = _display_value(field, record)

    # For translated/derived columns the canonical input is the Spanish/raw
    # canonical value, while the target column is represented by ``derived``.
    # This distinction is what separates DERIVED_MISSING from MAPPING_MISSED.
    if field == "title_zh":
        canonical = detail.get("title_es_raw") or record.get("title_es_raw")
    elif field in {"product_details_zh", "feature_bullets_zh", "spec_v2"}:
        canonical = raw

    # Derived fields have source evidence from their input chain, not from their own
    # display value.  Prices require two valid source values before discount is expected.
    if field == "discount_rate":
        cur = parse_price(detail.get("current_price_raw") or record.get("current_price_raw") or record.get("current_price"))
        orig = parse_price(detail.get("original_price_raw") or record.get("original_price_raw") or record.get("original_price"))
        source = cur is not None and orig is not None and orig > cur
        source_evidence = ["current_price", "valid_struck_original_price"] if source else source_evidence
        raw = {"current_price": cur, "original_price": orig} if cur is not None or orig is not None else ""
    if field in {"title_zh", "spec_v2", "product_details_zh", "feature_bullets_zh"}:
        source = _has(raw) or bool(source_evidence)

    classification, severity, message = _classify(field, source, raw, canonical, derived, display, raw_evidence=raw)
    if field == "original_price":
        cur = parse_price(detail.get("current_price_raw") or record.get("current_price_raw") or record.get("current_price"))
        orig = parse_price(raw)
        if cur is not None and orig is not None and orig <= cur:
            classification, severity = "ORIGINAL_PRICE_INVALID", "P1"
            message = "原价小于或等于当前售价，不作为有效划线原价展示"
            canonical = derived = display = None
    return FieldClosureResult(
        asin=asin,
        field=field,
        display_column=FIELD_COLUMNS[field],
        source_status="present" if source else "missing",
        raw_status="present" if _has(raw) else "missing",
        canonical_status="present" if _has(canonical) else "missing",
        derived_status="present" if _has(derived) else "missing",
        display_status="present" if _has(display) else "missing",
        classification=classification,
        severity=severity,
        source_evidence=source_evidence,
        raw_evidence=raw,
        canonical_value=canonical,
        derived_value=derived,
        display_value=display,
        message=message,
    )


def audit_field_closure(products: Iterable[Mapping], details: Optional[Iterable[Mapping]] = None,
                        rankings: Optional[Iterable[Mapping]] = None,
                        html_dir: Optional[str | Path] = None) -> dict:
    """Audit products without mutating any input mapping."""
    products = list(products or [])
    details_by = {normalize_asin(d.get("asin")): d for d in (details or []) if normalize_asin(d.get("asin"))}
    rankings_by = {normalize_asin(r.get("asin")): r for r in (rankings or []) if normalize_asin(r.get("asin"))}
    records: list[dict] = []
    for product in sorted(products, key=lambda p: normalize_asin(p.get("asin"))):
        asin = normalize_asin(product.get("asin"))
        detail = details_by.get(asin, {})
        ranking = rankings_by.get(asin, {})
        html = _find_html(html_dir, asin)
        for field in _FIELD_ORDER:
            records.append(_audit_one(field, asin, product, detail, ranking, html).to_dict())
    counts = {c: sum(1 for r in records if r["classification"] == c)
              for c in (SOURCE_MISSING, PARSER_MISSED, MAPPING_MISSED, DERIVED_MISSING)}
    counts["pass"] = sum(1 for r in records if r["classification"] == PASS)
    field_summary = {}
    for field in _FIELD_ORDER:
        rows = [r for r in records if r["field"] == field]
        field_summary[field] = {c: sum(1 for r in rows if r["classification"] == c)
                                for c in (PASS, SOURCE_MISSING, PARSER_MISSED, MAPPING_MISSED,
                                          DERIVED_MISSING, "ORIGINAL_PRICE_INVALID")}
    return {
        "summary": {"total_skus": len(products), "fields_checked": len(_FIELD_ORDER), **counts},
        "field_summary": field_summary,
        "records": records,
    }


def render_markdown(report: Mapping) -> str:
    summary = report.get("summary", {})
    lines = ["# Field Closure Audit", "", "## Summary", "",
             f"SKU: {summary.get('total_skus', 0)}", "",
             f"SOURCE_MISSING: {summary.get(SOURCE_MISSING, 0)}",
             f"PARSER_MISSED: {summary.get(PARSER_MISSED, 0)}",
             f"MAPPING_MISSED: {summary.get(MAPPING_MISSED, 0)}",
             f"DERIVED_MISSING: {summary.get(DERIVED_MISSING, 0)}",
             f"PASS: {summary.get('pass', 0)}", "", "## By Field", "",
             "| Field | PASS | SOURCE_MISSING | PARSER_MISSED | MAPPING_MISSED | DERIVED_MISSING |",
             "|---|---:|---:|---:|---:|---:|"]
    for field, counts in (report.get("field_summary") or {}).items():
        lines.append("| %s | %d | %d | %d | %d | %d |" % (
            field, counts.get(PASS, 0), counts.get(SOURCE_MISSING, 0),
            counts.get(PARSER_MISSED, 0), counts.get(MAPPING_MISSED, 0),
            counts.get(DERIVED_MISSING, 0)))
    lines += ["", "## Issues", ""]
    issues = [r for r in report.get("records", []) if r.get("classification") != PASS]
    if not issues:
        lines.append("无闭环问题。")
    else:
        for r in issues:
            lines += [f"### {r.get('asin')}", "", f"**{r.get('field')}**（{r.get('display_column')}）",
                      f"- Raw: `{r.get('raw_evidence')}`",
                      f"- Canonical: `{r.get('canonical_value')}`",
                      f"- Excel: `{r.get('display_value')}`",
                      f"- Classification: **{r.get('classification')}** ({r.get('severity')})",
                      f"- {r.get('message')}", ""]
    return "\n".join(lines) + "\n"


def write_report(report: Mapping, out: str | Path, md_out: Optional[str | Path] = None) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = Path(md_out) if md_out else path.with_suffix(".md")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
