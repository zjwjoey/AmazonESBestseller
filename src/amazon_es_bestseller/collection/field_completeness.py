"""Field-level source availability and extraction completeness."""
from __future__ import annotations

from collections import Counter
import re

from bs4 import BeautifulSoup

from ..identity import asin_from_url
from ..models import is_valid_asin
from .detail import (
    _attribute_label_key, _collect_attributes, _feature_bullets_raw,
    _page_asin_candidates, _product_description_raw,
    _selected_variation, _text,
)

EXTRACTED = "EXTRACTED"
SOURCE_ABSENT = "SOURCE_ABSENT"
PARSER_MISSED = "PARSER_MISSED"
LOW_QUALITY = "LOW_QUALITY"
UNKNOWN = "UNKNOWN"
KEY_FIELDS = ("feature_bullets_raw", "attributes", "product_description_raw")
FIELDS = ("asin", "title_es_raw", *KEY_FIELDS, "selected_variation_raw",
          "brand_raw", "seller_raw", "date_first_available_raw")
_COUNT_NAMES = {
    EXTRACTED: "extracted_count", SOURCE_ABSENT: "source_absent_count",
    PARSER_MISSED: "parser_missed_count", LOW_QUALITY: "low_quality_count",
    UNKNOWN: "unknown_count",
}


def _is_substantive(value, title=""):
    if isinstance(value, (list, dict)):
        return bool(value)
    text = " ".join(str(value or "").split()).casefold()
    return (any(char.isalpha() for char in text)
            and text not in {"descripción del producto", "acerca de este producto", "n/a", "sin descripción"}
            and (not title or text != " ".join(title.split()).casefold()))


def _state(value, available, *, title=""):
    if _is_substantive(value, title):
        return EXTRACTED
    if value:
        return LOW_QUALITY
    if available is None:
        return UNKNOWN
    return PARSER_MISSED if _is_substantive(available, title) else SOURCE_ABSENT


def check_detail_fields(asin, record, checkpoint, html, meta):
    """Check evidence without treating a success flag as page identity."""
    record = record or {}
    meta = meta or {}
    dom = BeautifulSoup(html, "lxml") if html else None
    title = _text(dom, "#productTitle") if dom else ""
    page_asins = _page_asin_candidates(dom) if dom else set()
    replay = (checkpoint.get("source") == "saved_html_replay"
              and meta.get("status_code") in {None, 200}
              and meta.get("access_state") in {None, "NORMAL"})
    normal = bool(dom and (page_asins or dom.select_one("#productTitle"))) and (
        ((meta.get("status_code") == 200 and meta.get("access_state") == "NORMAL") or replay)
        and not record.get("is_captcha"))
    final_asin = asin_from_url(meta.get("final_url"))
    route_matches = final_asin == asin or (replay and not meta.get("final_url"))
    identity = (is_valid_asin(asin) and page_asins == {asin} and route_matches
                and all(record.get(k) == asin for k in ("asin", "requested_asin", "resolved_asin"))
                and record.get("identity_status") == "MATCH")
    anomalies = []
    if not normal:
        anomalies.append({"field": "asin", "code": "DETAIL_NOT_NORMAL_PRODUCT_PAGE"})
    elif not identity:
        anomalies.append({"field": "asin", "code": "DETAIL_IDENTITY_UNCONFIRMED"})
    field_source = dict.fromkeys(FIELDS)
    if normal and dom:
        field_source.update({
            "asin": asin if identity else None,
            "title_es_raw": title,
            "feature_bullets_raw": _feature_bullets_raw(dom),
            "attributes": _collect_attributes(dom),
            "product_description_raw": _product_description_raw(dom, asin),
            "selected_variation_raw": _selected_variation(dom),
            "brand_raw": _text(dom, "#bylineInfo") or _text(dom, "#productOverview_feature_div .po-brand"),
            "seller_raw": _text(dom, "#sellerProfileTriggerId"),
        })
    fields = {}
    for key, source in field_source.items():
        value = record.get(key) if normal else None
        state = (_state(value, source, title=title if key == "product_description_raw" else "")
                 if key != "asin" else (EXTRACTED if identity and normal else UNKNOWN))
        fields[key] = {"state": state, "eligible": bool(normal and dom),
                       "source_present": _is_substantive(source) or state == EXTRACTED}
        if state in {PARSER_MISSED, LOW_QUALITY}:
            anomalies.append({"field": key, "code": state})
    if normal and (not _is_substantive(title) or not _is_substantive(record.get("title_es_raw"))
                   or " ".join(title.split()) != " ".join(record["title_es_raw"].split())):
        anomalies.append({"field": "title_es_raw", "code": "DETAIL_TITLE_MISSING_OR_UNBOUND"})
    for key in ("attributes",):
        values = {}
        for attribute in record.get(key) or ():
            label = _attribute_label_key(attribute.get("label_raw", ""))
            value = " ".join(attribute.get("value_raw", "").split()).casefold()
            if label and value:
                values.setdefault(label, set()).add(value)
        for label, variants in values.items():
            if len(variants) > 1 and re.search(r"capacidad|cantidad|material|color|tamaño|dimensi|modelo", label):
                anomalies.append({"field": key, "code": "ATTRIBUTE_VARIANT_OR_ROLE_CONFLICT", "label": label})
    valid = normal and identity and not any(a["code"] == "DETAIL_TITLE_MISSING_OR_UNBOUND" for a in anomalies)
    return valid, fields, anomalies


class FieldCompletenessMonitor:
    def __init__(self, thresholds):
        self.thresholds = thresholds

    def summarize(self, rows):
        totals = {key: Counter() for key in FIELDS}
        for row in rows:
            for key, item in row.get("fields", {}).items():
                if key not in totals or not item["eligible"]:
                    continue
                totals[key]["eligible_count"] += 1
                totals[key]["source_present_count"] += bool(item["source_present"])
                totals[key][_COUNT_NAMES[item["state"]]] += 1
        output = {}
        divide = lambda x, y: x / y if y else None
        for key, count in totals.items():
            eligible, available = count["eligible_count"], count["source_present_count"]
            extracted = count["extracted_count"]
            output[key] = dict(
                eligible_count=eligible, source_present_count=available,
                **{name: count[name] for name in _COUNT_NAMES.values()},
                source_coverage_rate=divide(available, eligible),
                effective_extraction_rate=divide(extracted, available),
                parser_missed_rate=divide(count["parser_missed_count"], available),
                effective_data_coverage_rate=divide(extracted, eligible),
            )
        return output

    def signals(self, batch, cumulative):
        limits = self.thresholds
        signals = []
        for scope, summary in (("batch", batch), ("task", cumulative)):
            for key in KEY_FIELDS:
                row = summary[key]
                if row["source_present_count"] < limits["minimum_source_samples"]:
                    continue
                rate = row["parser_missed_rate"]
                level = ("PAUSE" if rate >= limits["parser_missed_pause"]
                         else "WARN" if rate >= limits["parser_missed_warn"] else None)
                if level:
                    signals.append({"level": level, "scope": scope, "field": key, "rate": rate})
            title = summary["title_es_raw"]
            eligible = title["eligible_count"]
            missing = eligible - title["extracted_count"]
            threshold = limits["batch_title_missing_rate" if scope == "batch" else "task_title_missing_rate"]
            if eligible and missing / eligible >= threshold and (
                    scope == "task" or missing >= limits["batch_title_missing_minimum"]):
                signals.append({"level": "PAUSE", "scope": scope, "field": "title_es_raw",
                                "rate": missing / eligible})
        return signals
