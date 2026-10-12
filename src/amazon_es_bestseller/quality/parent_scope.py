"""Offline attribute-level scope review; family membership is not scope proof."""

from collections import Counter
import hashlib
import json
import re

from bs4 import BeautifulSoup, SoupStrainer


def text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().strip("\u200e\u200f").strip()


def _table_pairs(block):
    pairs = []
    for tr in block.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 2:
            pairs.append(tuple(text(cell.get_text(" ", strip=True)) for cell in cells))
    return pairs


def _child_widget_proofs(payload, row):
    """Local feature ASIN tags are scope evidence; page-wide tags are not."""
    soup = BeautifulSoup(payload.decode("utf-8"), "lxml")
    child = row["asin"]
    groups = {}
    for section, selector in (("product_overview", "#productOverview_feature_div"),
                              ("technical_details", "#prodDetails")):
        blocks = soup.select(selector)
        if len(blocks) != 1:
            continue
        block = blocks[0]
        pairs = _table_pairs(block)
        identities = {v for k, v in pairs if k.casefold() == "asin"}
        tag = block.get("data-csa-c-asin")
        bound = tag == child if section == "product_overview" else identities == {child}
        if bound and not (identities - {child}) and (not tag or tag == child):
            groups[section] = {"pairs": Counter(pairs), "text_sha256": hashlib.sha256(str(block).encode()).hexdigest()}
    scopes = {}
    proof_hashes = {}
    for field, selector in (("feature_bullets_raw", "#featurebullets_feature_div"),
                            ("detail_bullets_raw", "#detailBulletsWrapper_feature_div")):
        values = row.get(field)
        if not values:
            continue
        blocks = soup.select(selector)
        if len(blocks) != 1 or not isinstance(values, list):
            continue
        block = blocks[0]
        observed = [text(li.get_text(" ", strip=True)) for li in block.select("li") if text(li.get_text(" ", strip=True))]
        if [text(value) for value in values] != observed:
            continue
        if field == "feature_bullets_raw":
            bound = block.get("data-csa-c-asin") == child
        else:
            identities = {text(value.split(":", 1)[1]) for value in observed
                          if ":" in value and text(value.split(":", 1)[0]).casefold() == "asin"}
            bound = identities == {child}
        if bound:
            scopes[field] = "CHILD_ASIN_ANCHORED_WIDGET"
            proof_hashes[field] = hashlib.sha256(str(block).encode()).hexdigest()
    return groups, scopes, proof_hashes


def review_parent_scope(html, row):
    """Confirm exact occurrences in explicitly child-bound local detail sections.

    Canonical URL, parent membership, a current-ASIN script and a hidden input
    do not prove that all overview attributes or bullets apply to the child.
    A feature's own ASIN tag or a local details section's sole ASIN can bind
    that section, but never other page sections or untagged descriptions.
    """
    html_bytes = html if isinstance(html, bytes) else html.encode("utf-8")
    soup = BeautifulSoup(html_bytes.decode("utf-8"), "lxml", parse_only=SoupStrainer("table"))
    child = row["asin"]
    tables = []
    for table in soup.find_all("table"):
        pairs = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) == 2:
                pairs.append(
                    (
                        text(cells[0].get_text(" ", strip=True)),
                        text(cells[1].get_text(" ", strip=True)),
                    )
                )
        identities = {v for k, v in pairs if k.casefold() == "asin"}
        if identities == {child}:
            tables.append(
                {
                    "pairs": Counter(pairs),
                    "text_sha256": hashlib.sha256(
                        table.get_text(" ", strip=True).encode()
                    ).hexdigest(),
                }
            )
    raw_attributes = row.get("attributes") or []
    occurrences = Counter((a.get("section"), text(a["label_raw"]), text(a["value_raw"])) for a in raw_attributes)
    # Parse local widget evidence only when old table-only evidence is not enough.
    groups, widget_scopes, widget_hashes = _child_widget_proofs(html_bytes, row)
    attributes = []
    for index, attribute in enumerate(row.get("attributes") or []):
        pair = (text(attribute["label_raw"]), text(attribute["value_raw"]))
        matches = [t for t in tables if t["pairs"][pair]]
        # Occurrence-safe: a duplicate pair across a child and a generic table
        # cannot be assigned merely by identical text.
        declared_section = attribute.get("section") == "technical_details"
        if (declared_section and len(matches) == 1 and matches[0]["pairs"][pair] == 1
                and occurrences[(attribute.get("section"), *pair)] == 1):
            scope, proof = "CHILD_ASIN_ANCHORED_TABLE", matches[0]["text_sha256"]
        else:
            scope, proof = "SCOPE_NOT_CONFIRMED", None
        group = groups.get(attribute.get("section"))
        if (scope == "SCOPE_NOT_CONFIRMED" and group
                and group["pairs"][pair] == occurrences[(attribute.get("section"), *pair)]
                and group["pairs"][pair] > 0):
            scope, proof = "CHILD_ASIN_ANCHORED_WIDGET", group["text_sha256"]
        attributes.append(
            {
                "index": index,
                "section": attribute.get("section"),
                "label_raw": attribute["label_raw"],
                "value_raw": attribute["value_raw"],
                "scope": scope,
                "table_text_sha256": proof,
            }
        )
    counts = Counter(a["scope"] for a in attributes)
    other_fields = {field: "SCOPE_NOT_CONFIRMED" for field in (
        "feature_bullets_raw", "detail_bullets_raw", "product_description_raw",
        "aplus_text_raw", "a_plus_text_raw", "description_raw") if row.get(field)}
    other_fields.update(widget_scopes)
    all_attributes = bool(attributes) and not counts["SCOPE_NOT_CONFIRMED"]
    all_scope = all_attributes and all(value != "SCOPE_NOT_CONFIRMED" for value in other_fields.values())
    binding_source = {"asin": child, "parent_asin": row.get("parent_asin"), "attributes": raw_attributes,
                      **{field: row[field] for field in other_fields}}
    return {
        "asin": child,
        "parent_asin": row.get("parent_asin"),
        "html_sha256": hashlib.sha256(html_bytes).hexdigest(),
        "scope_input_sha256": hashlib.sha256(json.dumps(binding_source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "attribute_scope_counts": dict(counts),
        "attributes": attributes,
        "feature_bullets_scope": widget_scopes.get("feature_bullets_raw", "SCOPE_NOT_CONFIRMED")
        if row.get("feature_bullets_raw")
        else "SOURCE_MISSING",
        "other_field_scopes": other_fields,
        "widget_proof_hashes": widget_hashes,
        "all_attribute_scope_confirmed": all_attributes,
        "all_child_scope_confirmed": all_scope,
        "decision": "CHILD_SCOPE_CONFIRMED" if all_scope else "PARTIAL_CHILD_SCOPE_EVIDENCE"
        if counts["CHILD_ASIN_ANCHORED_TABLE"]
        else "CHILD_SCOPE_EVIDENCE_INSUFFICIENT",
    }
