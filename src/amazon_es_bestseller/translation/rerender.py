"""Selective, QA-gated re-rendering after a dictionary promotion.

This module never mutates the Spanish Master or human notes.  It updates only
the field envelopes whose ASIN, source record hash, field and source hash are
explicitly named by promoted evidence.  Everything else is a repair item.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Iterable, Mapping

from .dictionary_service import normalize_key
from .service import source_hash


def _records_by_asin(records: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row.get("asin") or "").upper(): row for row in records
            if str(row.get("asin") or "").strip()}


def _translation_map(translations: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    values = translations.values() if isinstance(translations, Mapping) else translations
    return {str(row.get("asin") or "").upper(): deepcopy(dict(row)) for row in values
            if isinstance(row, Mapping) and str(row.get("asin") or "").strip()}


def _qa_pass(result: Any, *, asin: str, field: str, target_field: str,
             source_text: str, target: str, dictionary_version: str,
             schema_version: str, context_key: str) -> bool:
    if not isinstance(result, Mapping) or str(result.get("status") or result.get("qa_status") or "").upper() != "PASS":
        return False
    expected = {
        "asin": asin, "field": field, "target_field": target_field,
        "source_hash": source_hash(source_text), "target": target,
        "dictionary_version": dictionary_version, "schema_version": schema_version,
        "context_key": context_key,
    }
    return all(str(result.get(key) or "") == value for key, value in expected.items())


def rerender_affected_fields(records: Iterable[Mapping[str, Any]],
                             translations: Mapping[str, Any] | Iterable[Mapping[str, Any]],
                             manifest: Mapping[str, Any], *,
                             qa_callback: Callable[..., Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Re-render exact scalar fields and queue anything not freshly QA-passed.

    The callback may be backed by the normal Chinese QA implementation or a
    review system.  Its result must carry exact bindings; a bare PASS is not
    enough to make a field READY.
    """
    dictionary_version = str(manifest.get("dictionary_version") or "0")
    schema_version = str(manifest.get("translation_schema_version") or "")
    record_map = _records_by_asin(records)
    outputs = _translation_map(translations)
    updates: list[dict[str, Any]] = []
    selective_repair: list[dict[str, Any]] = []
    for promotion in manifest.get("promotions") or []:
        if str(promotion.get("status") or "PROMOTED") != "PROMOTED":
            continue
        for evidence in promotion.get("evidence") or []:
            asin = str(evidence.get("asin") or "").upper()
            field = str(evidence.get("affected_field") or "")
            target_field = str(evidence.get("target_field") or field)
            target = str(promotion.get("target") or evidence.get("target") or "")
            context_key = str(promotion.get("context_key") or "")
            item = {"asin": asin, "field": field, "target_field": target_field,
                    "dictionary_version": dictionary_version, "context_key": context_key}
            source_record = record_map.get(asin)
            translated_record = outputs.get(asin)
            envelope = ((translated_record or {}).get("fields") or {}).get(target_field)
            if not source_record or not translated_record or not isinstance(envelope, Mapping):
                selective_repair.append({**item, "reason": "AFFECTED_FIELD_NOT_FOUND"})
                continue
            if str(translated_record.get("source_record_hash") or "") != str(evidence.get("source_record_hash") or ""):
                selective_repair.append({**item, "reason": "SOURCE_RECORD_CHANGED"})
                continue
            source_text = str(envelope.get("source_text") or "")
            if str(envelope.get("source_hash") or "") != str(evidence.get("source_hash") or "") or normalize_key(source_text) != str(promotion.get("source_normalized") or ""):
                selective_repair.append({**item, "reason": "SOURCE_FIELD_CHANGED"})
                continue
            candidate = target
            qa = (qa_callback(asin=asin, field=field, target_field=target_field,
                              source_text=source_text, translated_text=candidate,
                              source_hash=source_hash(source_text), dictionary_version=dictionary_version,
                              schema_version=schema_version, context_key=context_key)
                  if qa_callback else {})
            if not _qa_pass(qa, asin=asin, field=field, target_field=target_field,
                            source_text=source_text, target=candidate,
                            dictionary_version=dictionary_version, schema_version=schema_version,
                            context_key=context_key):
                selective_repair.append({**item, "reason": "RERENDER_QA_NOT_PASS", "qa": dict(qa) if isinstance(qa, Mapping) else {}})
                continue
            changed = dict(envelope)
            changed.update(translated_text=candidate, candidate_text=candidate,
                           translation_status="success", qa_status="pass", qa_issues=[],
                           resolution_source="dictionary_rerender",
                           dictionary_version=dictionary_version,
                           dictionary_hash=manifest.get("dictionary_hash"),
                           rerender_status="READY")
            translated_record.setdefault("fields", {})[target_field] = changed
            updates.append({**item, "status": "READY", "source_hash": changed["source_hash"]})
    return {"dictionary_version": dictionary_version,
            "dictionary_hash": manifest.get("dictionary_hash"), "records": outputs,
            "updates": updates, "selective_repair": selective_repair,
            # A stable Vn -> Vn manifest is a verified no-op, not a failed
            # rerender.  Only exact affected fields need a fresh QA callback.
            "ready": not selective_repair,
            "status": "NO_CHANGE" if not updates and not selective_repair else
                      "READY" if not selective_repair else "REPAIR_REQUIRED"}


def rerender_structured_items(state: Mapping[str, Any], manifest: Mapping[str, Any], *,
                              qa_callback: Callable[..., Mapping[str, Any]]) -> dict[str, Any]:
    """Re-render only exact, deterministic structured-detail items offline.

    ``translated_text`` remains the immutable provider/original result.  A
    successful dictionary update is recorded separately as an effective render
    so an old provider candidate can never be rewritten as dictionary output.
    """
    result = deepcopy(dict(state))
    dictionary_version = str(manifest.get("dictionary_version") or "")
    dictionary_hash = str(manifest.get("dictionary_hash") or "")
    updates: list[dict[str, Any]] = []
    selective_repair: list[dict[str, Any]] = []
    records = {str(row.get("asin") or "").upper(): row for row in result.get("records") or []}
    for promotion in manifest.get("promotions") or []:
        if str(promotion.get("status") or "PROMOTED") != "PROMOTED":
            continue
        for evidence in promotion.get("evidence") or []:
            asin = str(evidence.get("asin") or "").upper()
            field_name = str(evidence.get("affected_field") or "")
            item_id = str(evidence.get("item_id") or "")
            item_ref = {"asin": asin, "field": field_name, "item_id": item_id,
                        "dictionary_version": dictionary_version, "dictionary_hash": dictionary_hash}
            record = records.get(asin)
            field = next((row for row in (record or {}).get("fields") or []
                          if str(row.get("field") or "") == field_name), None)
            item_position, item = next(((position, row) for position, row in enumerate((field or {}).get("items") or [])
                                       if str(row.get("item_id") or "") == item_id), (None, None))
            if not record or not field or not item:
                selective_repair.append({**item_ref, "reason": "AFFECTED_ITEM_NOT_FOUND"})
                continue
            if str(record.get("source_record_hash") or "") != str(evidence.get("source_record_hash") or ""):
                selective_repair.append({**item_ref, "reason": "SOURCE_RECORD_CHANGED"})
                continue
            if str(item.get("asin") or "").upper() != asin:
                selective_repair.append({**item_ref, "reason": "ITEM_IDENTITY_CHANGED"})
                continue
            if str(field.get("target_field") or "") != str(evidence.get("target_field") or ""):
                selective_repair.append({**item_ref, "reason": "TARGET_FIELD_CHANGED"})
                continue
            source = str(item.get("value_raw") or "")
            if (str(evidence.get("source_hash") or "") != source_hash(source)
                    or str(item.get("source_hash") or "") != str(evidence.get("item_source_hash") or "")
                    or normalize_key(source) != str(promotion.get("source_normalized") or "")):
                selective_repair.append({**item_ref, "reason": "SOURCE_ITEM_CHANGED"})
                continue
            if field_name != "product_details" or item.get("admission") != "TRANSLATION_TASK":
                selective_repair.append({**item_ref, "reason": "NON_DETERMINISTIC_STRUCTURED_ITEM"})
                continue
            candidate = str(promotion.get("target") or evidence.get("target") or "")
            qa = qa_callback(asin=asin, field=field_name, item=item, position=item_position, candidate=candidate,
                             dictionary_version=dictionary_version, dictionary_hash=dictionary_hash)
            if not isinstance(qa, Mapping) or str(qa.get("status") or "") != "PASS":
                selective_repair.append({**item_ref, "reason": "RERENDER_QA_NOT_PASS",
                                         "qa": dict(qa) if isinstance(qa, Mapping) else {}})
                continue
            item["effective_render"] = {
                "translated_text": candidate,
                "qa": dict(qa),
                "dictionary_version": dictionary_version,
                "dictionary_hash": dictionary_hash,
                "resolution_source": "dictionary_rerender",
                "rerender_status": "READY",
            }
            updates.append({**item_ref, "status": "READY", "source_hash": item.get("source_hash")})
    return {"dictionary_version": dictionary_version, "dictionary_hash": dictionary_hash,
            "state": result, "updates": updates, "selective_repair": selective_repair,
            "ready": not selective_repair,
            "status": "NO_CHANGE" if not updates and not selective_repair else
                      "READY" if not selective_repair else "REPAIR_REQUIRED"}
