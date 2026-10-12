"""Lossless initial-translation input boundaries, separate from display text."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from .dictionary_service import is_identity_attribute


def detail_translation_input(record: dict) -> dict:
    source = record.get("translation_source_record") or record
    attrs = next((source[k] for k in ("eligibleattributes", "canonicalstructuredsource", "attributes", "detail_attributes_raw")
                  if isinstance(source.get(k), list)), None)
    bullets = source.get("detail_bullets_raw")
    if attrs is None and not isinstance(bullets, list):
        return {"available": False, "items": [], "entries": [], "covered_count": 0}
    items, entries = [], []
    exact_pairs = {}
    for index, attr in enumerate(attrs or []):
        locator = {"source": "attributes", "index": index}
        if not isinstance(attr, dict):
            entries.append({"locator": locator, "raw": attr, "disposition": "REVIEW_INVALID_STRUCTURE"})
            continue
        label = attr.get("label_raw", attr.get("label", attr.get("name")))
        value = attr.get("value_raw", attr.get("value"))
        entry = {"locator": locator, "raw": deepcopy(attr)}
        if value is None or not str(value).strip():
            entry["disposition"] = "SOURCE_MISSING"
        else:
            entry.update(disposition="SOURCE_PRESERVED" if is_identity_attribute(label) else "INITIAL_TRANSLATION",
                         item_index=len(items))
            item = deepcopy(attr)
            item.update(label_raw=label, value_raw=value, source_locator=locator)
            items.append(item)
            exact_pairs[(str(label).strip(), str(value).strip())] = len(items) - 1
        entries.append(entry)
    for index, bullet in enumerate(bullets or []):
        locator = {"source": "detail_bullets_raw", "index": index}
        raw = str(bullet) if bullet is not None else ""
        parts = re.split(r"[:：]", raw, maxsplit=1)
        label, value = ((parts[0].strip(), parts[1].strip())
                        if len(parts) == 2 and parts[0].strip() and parts[1].strip() else (None, raw.strip()))
        entry = {"locator": locator, "raw": deepcopy(bullet)}
        pair = (str(label).strip(), value)
        if not value:
            entry["disposition"] = "SOURCE_MISSING"
        elif label is not None and pair in exact_pairs:
            # Raw evidence remains, with an explicit reference. No value-only
            # deduplication or collapsing of repeated attribute keys.
            entry.update(disposition="EXACT_ATTRIBUTE_REFERENCE", item_index=exact_pairs[pair])
        else:
            entry.update(disposition="SOURCE_PRESERVED" if is_identity_attribute(label) else "INITIAL_TRANSLATION",
                         item_index=len(items))
            items.append({"label_raw": label, "value_raw": value, "source_locator": locator})
        entries.append(entry)
    return {"available": True, "version": "source-coverage-v1", "items": items, "entries": entries,
            "covered_count": len(entries), "source_count": len(attrs or []) + len(bullets or []),
            "source_sha256": hashlib.sha256(json.dumps([attrs, bullets], ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}
