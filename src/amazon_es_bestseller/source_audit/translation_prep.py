"""Prepare semantic units and possible reuse only. No provider/service imports."""
from __future__ import annotations

import re
from collections import Counter

from ..multisource.storage import digest
from ..translation.dictionary_service import DictionaryService, is_identity_attribute, normalize_key
from ..translation.field_contract import canonical_translation_unit_field
from . import CONTRACT_VERSION
from .contracts import PROTECTED_FIELDS, file_sha

NUMERIC_VALUE = re.compile(r"[\d\s.,+×xX*/%°º²³µμ\-–]+(?:mm|cm|m|ml|l|kg|g|mg|w|v|hz|mah|ah|db|gb|tb)?", re.I)
TOKENS = re.compile(r"\b\d+(?:[.,]\d+)*(?:\s*(?:ml|mm|cm|kg|mg|mAh|Ah|Hz|kHz|MHz|GHz|GB|TB|MB|W|V|L|g|m|%|°C))?\b|\b[A-Z][A-Z0-9_-]{1,}\b")


def dictionary_evidence(service):
    """Bind dictionary versions to real bytes; reject normalized collisions."""
    hashes, collisions = {}, []
    for path in sorted(service.directory.glob("*.json")):
        hashes[path.name] = file_sha(path)
    for name in service.dictionary_counts():
        seen = {}
        for key, value in getattr(service, name).items():
            normalized = normalize_key(key)
            if normalized in seen and seen[normalized] != value:
                collisions.append({"dictionary": name, "normalized_key": normalized})
            seen[normalized] = value
    return {"version": digest(hashes), "sha256": hashes,
            "counts": service.dictionary_counts(), "normalized_collisions": collisions}


def prepare(products, *, input_manifest_sha256, cache_memory=None):
    service = DictionaryService()
    glossary = dictionary_evidence(service)
    ambiguous_dictionaries = {row["dictionary"] for row in glossary["normalized_collisions"]}
    units, ready_products, skipped_products = [], [], []
    duplicates = Counter()
    cache_memory = cache_memory or {}
    for product in products:
        if product["preparation_status"] not in {"READY", "PARTIAL_READY"}:
            skipped_products.append(product["candidate_id"])
            continue
        record, fields = product["raw_record"], product["field_statuses"]
        ready_product = {
            "candidate_id": product["candidate_id"], "selection_id": product["selection_id"],
            "asin": product["asin"], "preparation_status": product["preparation_status"],
            "raw_record": record, "field_statuses": fields,
            "passthrough_fields": {k: record[k] for k in PROTECTED_FIELDS if k in record},
            "source_evidence_reference": product["source_evidence_reference"], "translation_unit_ids": [],
        }

        def add(field, text, *, section=None, position=None, label=None, role="text", source=None):
            if not isinstance(text, str) or not text.strip():
                return
            semantic = ("attribute_label" if role == "label" else
                        canonical_translation_unit_field("product_details", label=label) if role == "value" else
                        canonical_translation_unit_field("category_l1" if field == "category_path" else field))
            # Category meanings depend on the complete Amazon parent path.
            context = product["translation_source"].get("category_path", [])[:position] if field == "category_path" else []
            reuse_key = digest({"source_text_sha256": digest(text.encode("utf8")),
                                "field_type": semantic, "section": section, "category_parent_path": context,
                                "glossary_version": glossary["version"], "contract_version": CONTRACT_VERSION,
                                "source_language": "es", "target_language": "zh"})
            pure = role == "value" and (is_identity_attribute(label) or bool(NUMERIC_VALUE.fullmatch(text.strip())))
            dictionary, hint = None, None
            if role == "label":
                dictionary, hint = "attribute_labels", service.lookup_attribute_label(text)
            elif role == "value" and not pure:
                normalized_label = normalize_key(label)
                dictionary = {"color": "colors", "material": "materials", "tipo de paquete": "packaging",
                              "tipo de embalaje": "packaging"}.get(normalized_label)
                if dictionary:
                    hint = service.lookup_normalized(text, dictionary)
            elif field == "selected_variation_raw":
                dictionary, hint = "packaging", service.lookup_normalized(text, "packaging")
            # Category dictionary is reviewed as a hint only: old flat mappings
            # are not path-aware approval and must not silently mark resolved.
            category_hint = service.lookup_category(text) if field == "category_path" else None
            if dictionary in ambiguous_dictionaries:
                hint = None
            cache = cache_memory.get(reuse_key)
            verified_cache = bool(isinstance(cache, dict) and cache.get("semantic_reuse_key") == reuse_key
                                  and cache.get("status") == "success" and cache.get("qa_status") == "pass"
                                  and cache.get("source_text_sha256") == digest(text.encode("utf8"))
                                  and cache.get("translation_text")
                                  and cache.get("translation_sha256") == digest(str(cache["translation_text"]).encode("utf8")))
            state = "PASSTHROUGH" if pure else "CACHE_REUSE_AVAILABLE" if verified_cache else "DICTIONARY_REUSE_AVAILABLE" if hint else "READY"
            unit = {
                "translation_unit_id": "tu_" + digest([input_manifest_sha256, product["candidate_id"], product["selection_id"], field, section, position, role, digest(text.encode("utf8"))]),
                "candidate_id": product["candidate_id"], "selection_id": product["selection_id"], "asin": product["asin"],
                "field_name": field, "attribute_section": section, "attribute_position": position,
                "attribute_label": label, "attribute_role": role, "attribute_source": source,
                "source_text_es": text, "source_text_sha256": digest(text.encode("utf8")),
                "translation_field_type": semantic, "glossary_version": glossary["version"],
                "protected_terms": list(dict.fromkeys(TOKENS.findall(text)
                    + [term for term in service.protected_terms if term in text]
                    + [str(record[k]) for k in ("brand_raw", "model", "model_number") if record.get(k) and str(record[k]) in text])),
                "source_evidence_reference": {**product["source_evidence_reference"],
                    **({"derivation": product["specification_derivation"]} if field == "specification_es" and product.get("specification_derivation") else {})},
                "semantic_reuse_key": reuse_key,
                "cache_identity_components": {"asin": product["asin"], "candidate_id": product["candidate_id"],
                    "source_hash": digest(text.encode("utf8")), "semantic_field": semantic,
                    "schema_version": CONTRACT_VERSION, "glossary_version": glossary["version"]},
                "category_parent_path": context, "preparation_status": state,
                "model_translation_required": not pure and not verified_cache and hint is None,
                "dictionary_hint": hint, "category_dictionary_hint_unapproved": category_hint,
                "cache_translation_hint": cache.get("translation_text") if verified_cache else None,
                "translation_result": None,
            }
            units.append(unit); ready_product["translation_unit_ids"].append(unit["translation_unit_id"])
            if not pure:
                duplicates[reuse_key] += 1

        for field, value in product["translation_source"].items():
            if fields.get(field) != "READY":
                continue
            if field == "attributes":
                for index, attr in enumerate(value):
                    section, position, label = attr["section"], attr["position"], attr["label_raw"]
                    # Source index differentiates repeated positions/labels;
                    # position itself is retained exactly, never overwritten.
                    for role, text in (("label", label), ("value", attr["value_raw"])):
                        add(field, text, section=section, position=index, label=label, role=role, source=attr["source"])
                        units[-1]["attribute_position"] = position
                        units[-1]["attribute_source_index"] = index
            elif isinstance(value, list):
                for index, text in enumerate(value):
                    add(field, text, position=index)
            else:
                add(field, value)
        ready_products.append(ready_product)
    ids = [u["translation_unit_id"] for u in units]
    if len(set(ids)) != len(ids):
        raise ValueError("DUPLICATE_TRANSLATION_UNIT_ID")
    counts = Counter(u["preparation_status"] for u in units)
    return {
        "products": ready_products, "units": units, "glossary": glossary,
        "summary": {"input_products": len(products), "translation_ready_products": len(ready_products),
                    "excluded_products": len(skipped_products), "translation_unit_total": len(units),
                    "actual_model_units_before_dedup": sum(u["model_translation_required"] for u in units),
                    "unit_status_counts": dict(counts),
                    "dictionary_reusable_units": counts["DICTIONARY_REUSE_AVAILABLE"],
                    "cache_reusable_units": counts["CACHE_REUSE_AVAILABLE"],
                    "passthrough_units": counts["PASSTHROUGH"],
                    "duplicate_semantic_units": sum(count - 1 for count in duplicates.values()),
                    "unique_model_requests_if_later_approved": len({u["semantic_reuse_key"] for u in units if u["model_translation_required"]}),
                    "amazon_requests": 0, "translation_api_requests": 0, "production_apply": False},
    }


def validate_preparation(result):
    """Validate field contract and exact ordered label/value pairing offline."""
    required = {"translation_unit_id", "candidate_id", "selection_id", "asin", "field_name",
                "attribute_section", "attribute_position", "attribute_label", "source_text_es",
                "source_text_sha256", "translation_field_type", "glossary_version", "protected_terms",
                "source_evidence_reference", "preparation_status", "semantic_reuse_key"}
    by_id = {}
    products = {p["candidate_id"]: p for p in result["products"]}
    if len(products) != len(result["products"]):
        raise ValueError("CONTRACT_DUPLICATE_CANDIDATE")
    for unit in result["units"]:
        if not required.issubset(unit) or unit["translation_result"] is not None:
            raise ValueError("TRANSLATION_UNIT_CONTRACT_INVALID")
        if digest(unit["source_text_es"].encode("utf8")) != unit["source_text_sha256"]:
            raise ValueError("TRANSLATION_UNIT_SOURCE_HASH_INVALID")
        if unit["translation_unit_id"] in by_id:
            raise ValueError("TRANSLATION_UNIT_ID_CONFLICT")
        product = products.get(unit["candidate_id"])
        if not product or product["selection_id"] != unit["selection_id"] or product["asin"] != unit["asin"]:
            raise ValueError("TRANSLATION_UNIT_IDENTITY_INVALID")
        if product["field_statuses"].get(unit["field_name"]) != "READY":
            raise ValueError("TRANSLATION_UNIT_FIELD_NOT_READY")
        if unit["attribute_role"] in {"label", "value"}:
            attrs = product["raw_record"].get("attributes") or product["raw_record"].get("ordered_detail_evidence")
            attr = attrs[unit["attribute_source_index"]]
            expected = attr["label_raw" if unit["attribute_role"] == "label" else "value_raw"]
            if unit["source_text_es"] != expected or unit["attribute_position"] != attr["position"] or unit["attribute_section"] != attr["section"]:
                raise ValueError("TRANSLATION_ATTRIBUTE_PAIRING_INVALID")
        by_id[unit["translation_unit_id"]] = unit
    references = [uid for product in result["products"] for uid in product["translation_unit_ids"]]
    if len(references) != len(by_id) or set(references) != set(by_id):
        raise ValueError("TRANSLATION_PRODUCT_UNIT_BINDING_INVALID")
    return {"status": "PASS", "products": len(products), "units": len(by_id)}
