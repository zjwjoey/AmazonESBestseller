"""Field-level Translation V2 orchestration.

The service is intentionally serial.  It owns source hashing, category
translation-memory deduplication, cache lookup, protection and per-field
failure isolation; providers only perform one translation request.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cache import TranslationCache
from .protection import protect, restore
from .providers.base import TranslationProvider
from .qa import build_qa_report, qa_field
from .schemas import TRANSLATION_SCHEMA_VERSION
from .terminology import postprocess


DEFAULT_FIELD_MAP = {
    "title_es_raw": "title_zh", "title_es": "title_zh", "title": "title_zh",
    "brand": "brand_zh", "brand_es": "brand_zh",
    "feature_bullets_es": "feature_bullets_zh", "feature_bullets_raw": "feature_bullets_zh",
    "features_es": "feature_bullets_zh",
    "description_es": "description_zh", "product_description_es": "description_zh",
    "product_description_raw": "description_zh",
    "product_details_es": "product_details_zh", "detail_attributes_raw": "product_details_zh",
    "selected_variant_es": "selected_variant_zh", "selected_variation_raw": "selected_variation_zh",
    "variation_es": "selected_variant_zh",
    "specification_es": "specification_zh", "category_l1": "category_l1_zh",
    "category_l2": "category_l2_zh", "category_l3": "category_l3_zh",
    "leaf_category": "leaf_category_zh", "category_l1_es": "category_l1_zh",
    "category_l2_es": "category_l2_zh", "category_l3_es": "category_l3_zh",
    "leaf_category_es": "leaf_category_zh",
}


def source_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def translation_memory_field_type(field: str) -> str:
    """Use one TM namespace for equivalent category labels across levels."""
    if field in {"category_l1", "category_l2", "category_l3", "leaf_category",
                 "category_l1_es", "category_l2_es", "category_l3_es", "leaf_category_es"}:
        return "category"
    return field


class TranslationService:
    def __init__(self, provider: TranslationProvider, cache: TranslationCache,
                 *, field_map: Optional[Dict[str, str]] = None,
                 schema_version: str = TRANSLATION_SCHEMA_VERSION,
                 prompt_version: str = "v1", max_fields: Optional[Sequence[str]] = None,
                 source_language: str = "es", target_language: str = "zh-CN"):
        self.provider = provider
        self.cache = cache
        self.field_map = dict(field_map or DEFAULT_FIELD_MAP)
        self.schema_version = schema_version
        self.prompt_version = prompt_version
        self.source_language = source_language
        self.target_language = target_language
        self.max_fields = set(max_fields) if max_fields else None
        self._memory: Dict[tuple, Dict[str, Any]] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def selected_fields(self, record: Dict[str, Any], fields: Optional[Sequence[str]] = None) -> List[tuple[str, str, str]]:
        requested = set(fields or ())
        selected: List[tuple[str, str, str]] = []
        seen_targets = set()
        for source, target in self.field_map.items():
            if self.max_fields and source not in self.max_fields and target not in self.max_fields:
                continue
            if requested and source not in requested and target not in requested:
                continue
            value = record.get(source)
            if value is None or isinstance(value, (dict, list)):
                # Structured details can still be translated losslessly as JSON text.
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                else:
                    continue
            text = str(value).strip()
            if text:
                if target in seen_targets:
                    continue
                selected.append((source, target, text))
                seen_targets.add(target)
        # A custom field can be passed by its already-Chinese target/source name.
        return selected

    def plan(self, records: Sequence[Dict[str, Any]], *, fields: Optional[Sequence[str]] = None,
             offset: int = 0, limit: Optional[int] = None) -> Dict[str, Any]:
        subset = list(records)[max(0, offset):]
        if limit is not None:
            subset = subset[:max(0, limit)]
        rows = []
        cache_hits = 0
        source_missing = 0
        unique_requests = set()
        for record in subset:
            asin = str(record.get("asin") or "").strip().upper()
            selected = self.selected_fields(record, fields)
            if not selected:
                source_missing += 1
            for source, target, text in selected:
                digest = source_hash(text)
                key = self.cache.key(asin, source, digest, self.provider.name,
                                     self.provider.model, self.schema_version, self.prompt_version)
                cached = self.cache.get(key)
                if cached and cached.get("translation_status") in {"success", "cached"}:
                    cache_hits += 1
                else:
                    unique_requests.add((translation_memory_field_type(source), digest,
                                         self.provider.name, self.provider.model))
                rows.append({"asin": asin, "source_field": source, "target_field": target,
                             "source_hash": digest, "source_chars": len(text)})
        return {"schema_version": self.schema_version, "provider": self.provider.name,
                "model": self.provider.model, "total_records": len(subset),
                "total_fields": len(rows), "cache_hits": cache_hits,
                "source_missing": source_missing,
                "estimated_api_requests": len(unique_requests), "fields": rows}

    def _translate_record(self, record: Dict[str, Any], *, fields: Optional[Sequence[str]] = None,
                          repair_partial: bool = False, repair_failed: bool = False) -> Dict[str, Any]:
        asin = str(record.get("asin") or "").strip().upper()
        if not asin:
            return {"asin": "", "fields": {}, "translation_status": "failed",
                    "error": "missing asin"}
        output_fields: Dict[str, Dict[str, Any]] = {}
        for source_field, target, text in self.selected_fields(record, fields):
            digest = source_hash(text)
            key = self.cache.key(asin, source_field, digest, self.provider.name,
                                 self.provider.model, self.schema_version, self.prompt_version)
            cached = self.cache.get(key)
            if cached and cached.get("translation_status") == "partial" and not repair_partial:
                output_fields[target] = cached
                continue
            if cached and cached.get("translation_status") == "failed" and not repair_failed:
                output_fields[target] = cached
                continue
            if cached and cached.get("translation_status") in {"success", "cached"}:
                cached["translation_status"] = "cached"
                output_fields[target] = cached
                continue
            memory_key = (translation_memory_field_type(source_field), digest,
                          self.provider.name, self.provider.model)
            if memory_key in self._memory:
                memory = self._memory[memory_key]
                result = {"asin": asin, "field": source_field, "target_field": target,
                          "source_text": text, "source_hash": digest,
                          "translated_text": memory["text"],
                          "translation_status": "cached" if memory["translation_status"] == "success" else memory["translation_status"],
                          "qa_status": memory["qa_status"],
                          "provider": self.provider.name, "model": self.provider.model,
                          "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                          "attempt_count": 0, "last_error": None,
                          "qa_issues": list(memory["qa_issues"]), "translated_at": self._now()}
            else:
                # Protect numbers and explicit identity tokens.  Brand and ASIN
                # are always protected even when translating another field.
                protected = protect(text, protected_values=[asin, str(record.get("brand") or "")])
                response = self.provider.translate(protected.text, asin=asin, field=source_field,
                                                   source_language=self.source_language,
                                                   target_language=self.target_language,
                                                   context={"target_field": target, "protected_tokens": list(protected.tokens)})
                result = {"asin": asin, "field": source_field, "target_field": target,
                          "source_text": text, "source_hash": digest,
                          "translated_text": response.text or "", "translation_status": response.status,
                          "qa_status": "pending", "provider": response.provider or self.provider.name,
                          "model": response.model or self.provider.model,
                          "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                          "attempt_count": response.attempts, "last_error": response.error,
                          "qa_issues": [], "translated_at": self._now()}
                if response.status == "success" and response.text:
                    qa = qa_field(protected, response.text, text, field=source_field,
                                  allowed_residual=[record.get("brand", "")])
                    result["qa_status"] = qa["qa_status"]
                    result["qa_issues"] = qa["issues"]
                    if qa["issues"]:
                        result["translation_status"] = "qa_failed"
                    else:
                        restored, _ = restore(protected, response.text)
                        result["translated_text"] = postprocess(source_field, restored, text)
                    # Translation memory deduplicates provider calls even when
                    # the identical source later needs the same QA review.
                    restored, _ = restore(protected, response.text)
                    result["translated_text"] = postprocess(source_field, restored, text)
                    self._memory[memory_key] = {
                        "text": result["translated_text"],
                        "translation_status": result["translation_status"],
                        "qa_status": result["qa_status"],
                        "qa_issues": list(result["qa_issues"]),
                    }
                elif response.status == "success":
                    result["translation_status"] = "qa_failed"
                    result["qa_status"] = "qa_failed"
                    result["qa_issues"] = [{"code": "EMPTY_TRANSLATION"}]
                elif response.status == "failed":
                    result["translation_status"] = "failed"
            self.cache.put(key, result)
            output_fields[target] = result
        statuses = [v.get("translation_status") for v in output_fields.values()]
        if not statuses:
            overall = "source_missing"
        elif all(s in {"success", "cached"} for s in statuses):
            overall = "success"
        elif any(s in {"success", "cached"} for s in statuses):
            overall = "partial"
        elif any(s == "qa_failed" for s in statuses):
            overall = "qa_failed"
        else:
            overall = "failed"
        output = {"asin": asin, "fields": output_fields, "translation_status": overall,
                  "source_record_hash": source_hash(json.dumps(record, ensure_ascii=False, sort_keys=True))}
        # Keep a flat display overlay alongside the auditable field envelopes;
        # this lets the existing enrich/export path consume V2 without knowing
        # provider internals, while raw Spanish remains in the source product.
        for target, value in output_fields.items():
            if value.get("translated_text") and value.get("translation_status") in {"success", "cached"}:
                output[target] = value["translated_text"]
        return output

    def translate_records(self, records: Sequence[Dict[str, Any]], *, fields: Optional[Sequence[str]] = None,
                          offset: int = 0, limit: Optional[int] = None,
                          repair_partial: bool = False, repair_failed: bool = False,
                          dry_run: bool = False) -> Dict[str, Any]:
        subset = list(records)[max(0, offset):]
        if limit is not None:
            subset = subset[:max(0, limit)]
        if dry_run:
            return {"records": {}, "summary": self.plan(records, fields=fields, offset=offset, limit=limit),
                    "qa_report": {"status": "dry_run"}}
        outputs: Dict[str, Dict[str, Any]] = {}
        for record in subset:
            result = self._translate_record(record, fields=fields,
                                             repair_partial=repair_partial, repair_failed=repair_failed)
            if result.get("asin"):
                outputs[result["asin"]] = result
            self.cache.save()
        summary = {"total": len(outputs)}
        for result in outputs.values():
            summary[result.get("translation_status", "pending")] = summary.get(result.get("translation_status", "pending"), 0) + 1
        return {"records": outputs, "summary": summary, "qa_report": build_qa_report(outputs.values())}
