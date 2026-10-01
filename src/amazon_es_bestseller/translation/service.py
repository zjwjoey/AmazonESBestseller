"""Field-level Translation V2 orchestration.

The service is intentionally serial.  It owns source hashing, category
translation-memory deduplication, cache lookup, protection and per-field
failure isolation; providers only perform one translation request.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cache import TranslationCache
from .protection import protect, restore
from .providers.base import TranslationProvider
from .qa import build_qa_report, qa_field
from .schemas import TRANSLATION_SCHEMA_VERSION
from .terminology import (postprocess, deterministic_specification,
                          specification_is_deterministic)
from .full_detail import LABEL_ES_ZH
from .zh import spec_zh_from


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

    def _memory_key(self, text: str, field: str) -> str:
        return self.cache.memory_key(
            text, self.source_language, self.target_language,
            translation_memory_field_type(field), self.provider.name,
            self.provider.model, self.schema_version, self.prompt_version)

    @staticmethod
    def _structured_rows(value: Any) -> Optional[List[tuple[str, str]]]:
        if isinstance(value, dict):
            return [(str(k), str(v)) for k, v in value.items() if str(v).strip()]
        if isinstance(value, list) and all(isinstance(row, dict) for row in value):
            rows = []
            for row in value:
                label = row.get("label_raw") or row.get("label") or row.get("name")
                raw_value = row.get("value_raw") or row.get("value")
                if label is not None and raw_value is not None and str(raw_value).strip():
                    rows.append((str(label), str(raw_value)))
            return rows
        if isinstance(value, str) and "\n" in value:
            rows = []
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            for line in lines:
                if ":" not in line and "：" not in line:
                    return None
                label, raw_value = re.split(r"[:：]", line, maxsplit=1)
                if not label.strip() or not raw_value.strip():
                    return None
                rows.append((label.strip(), raw_value.strip()))
            return rows or None
        return None

    @staticmethod
    def _bullet_values(value: Any) -> Optional[List[str]]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and "\n" in value:
            return [line.strip() for line in value.splitlines() if line.strip()]
        return None

    @classmethod
    def _structured_items(cls, source_field: str, raw_value: Any) -> Optional[List[tuple[Optional[str], str]]]:
        """Return lossless item boundaries used by both planning and execution."""
        is_bullet_field = source_field in {"feature_bullets_es", "feature_bullets_raw", "features_es"}
        if is_bullet_field:
            bullets = cls._bullet_values(raw_value)
            return [(None, value) for value in bullets] if bullets is not None else None
        rows = cls._structured_rows(raw_value)
        return list(rows) if rows is not None else None

    def _translate_structured(self, *, asin: str, source_field: str, target: str,
                              source_text: str, raw_value: Any, record: Dict[str, Any],
                              repair_partial: bool = False, repair_failed: bool = False) -> Dict[str, Any]:
        """Translate structured details/bullets item-by-item and re-render them.

        Labels and bullet boundaries remain deterministic; only each value/text
        item is sent to the provider.  This prevents a model from flattening a
        detail table or merging separate selling points.
        """
        items = self._structured_items(source_field, raw_value)
        if items is None:
            return {}
        rendered = []
        issues: List[Dict[str, Any]] = []
        statuses = []
        attempts = 0
        errors = []
        for index, (label, value) in enumerate(items):
            memory_key = self._memory_key(value, source_field)
            memory = self._memory.get(memory_key) or self.cache.get_memory(memory_key)
            if memory and ((memory.get("translation_status") == "partial" and repair_partial)
                           or (memory.get("translation_status") in {"failed", "qa_failed"}
                               and repair_failed)):
                memory = None
            if memory:
                item_status = memory.get("translation_status", "success")
                statuses.append(item_status)
                item_issues = list(memory.get("qa_issues") or [])
                issues.extend({"item_index": index, **issue} for issue in item_issues)
                rendered_value = str(memory.get("translated_text") or "")
            else:
                protected = protect(value, protected_values=[asin, str(record.get("brand") or "")])
                response = self.provider.translate(
                    protected.text, asin=asin, field=source_field,
                    source_language=self.source_language, target_language=self.target_language,
                    context={"target_field": target, "item_index": index,
                             "label": label, "protected_tokens": list(protected.tokens)})
                attempts += response.attempts
                if response.status == "success" and response.text:
                    qa = qa_field(protected, response.text, value, field=source_field,
                                  brand=record.get("brand", ""))
                    restored, restore_issues = restore(protected, response.text)
                    item_issues = list(qa["issues"]) + list(restore_issues)
                    item_status = "qa_failed" if item_issues else "success"
                    statuses.append(item_status)
                    issues.extend({"item_index": index, **issue} for issue in item_issues)
                    rendered_value = postprocess(source_field, restored, value)
                    memory_payload = {
                        "translated_text": rendered_value,
                        "translation_status": item_status,
                        "qa_status": "qa_failed" if item_issues else "pass",
                        "qa_issues": list(item_issues),
                    }
                    self._memory[memory_key] = memory_payload
                    self.cache.put_memory(memory_key, memory_payload)
                else:
                    statuses.append("failed")
                    rendered_value = ""
                    if response.error:
                        errors.append(str(response.error))
            if label is not None:
                label_zh = LABEL_ES_ZH.get(label.strip().casefold(), label.strip())
                rendered.append("%s：%s" % (label_zh, rendered_value))
            else:
                rendered.append(rendered_value)
        if all(status == "success" for status in statuses):
            overall = "success"
            qa_status = "pass"
        elif any(status == "success" for status in statuses):
            overall = "partial"
            qa_status = "qa_failed" if issues else "pending"
        elif any(status == "qa_failed" for status in statuses):
            overall = "qa_failed"
            qa_status = "qa_failed"
        else:
            overall = "failed"
            qa_status = "pending"
        return {"asin": asin, "field": source_field, "target_field": target,
                "source_text": source_text, "source_hash": source_hash(source_text),
                "translated_text": "\n".join(rendered),
                "translation_status": overall, "qa_status": qa_status,
                "provider": self.provider.name, "model": self.provider.model,
                "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                "attempt_count": attempts, "last_error": "; ".join(errors) or None,
                "qa_issues": issues, "translated_at": self._now()}

    def _deterministic_spec_result(self, *, asin: str, source_field: str,
                                   target: str, text: str) -> Optional[Dict[str, Any]]:
        if source_field != "specification_es" or not specification_is_deterministic(text):
            return None
        translated = deterministic_specification(text)
        if not translated or translated == text:
            return None
        source_numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
        result_numbers = re.findall(r"\d+(?:[.,]\d+)?", translated)
        issues = []
        if sorted(source_numbers) != sorted(result_numbers):
            issues.append({"code": "NUMERIC_MISMATCH", "source": source_numbers,
                           "result": result_numbers})
        protected = protect(text)
        for value in protected.tokens.values():
            # Deterministic rules are allowed to convert units (cm→厘米,
            # ml→毫升); numeric equality above covers the invariant here.
            if re.search(r"(?:ml|cl|dl|kg|mg|mm|cm|kw|hz|ghz|mah|bar|psi|°c|%)$",
                         value.strip().lower()):
                continue
            if value not in translated:
                issues.append({"code": "PROTECTED_TOKEN_MISSING", "token": value})
        status = "qa_failed" if issues else "success"
        return {"asin": asin, "field": source_field, "target_field": target,
                "source_text": text, "source_hash": source_hash(text),
                "translated_text": translated, "translation_status": status,
                "qa_status": "qa_failed" if issues else "pass",
                "provider": "deterministic", "model": "rules-v1",
                "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                "attempt_count": 0, "last_error": None, "qa_issues": issues,
                "translated_at": self._now()}

    def _deterministic_brand_result(self, *, asin: str, source_field: str,
                                    target: str, text: str) -> Dict[str, Any]:
        """Brands are identity evidence, not machine-translation content."""
        return {"asin": asin, "field": source_field, "target_field": target,
                "source_text": text, "source_hash": source_hash(text),
                "translated_text": text, "translation_status": "success",
                "qa_status": "pass", "provider": "deterministic",
                "model": "identity-v1", "schema_version": self.schema_version,
                "prompt_version": self.prompt_version, "attempt_count": 0,
                "last_error": None, "qa_issues": [], "translated_at": self._now()}

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
             offset: int = 0, limit: Optional[int] = None,
             repair_partial: bool = False, repair_failed: bool = False) -> Dict[str, Any]:
        subset = list(records)[max(0, offset):]
        if limit is not None:
            subset = subset[:max(0, limit)]
        rows = []
        cache_hits = 0
        translation_memory_hits = 0
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
                memory = None
                bypass_memory = False
                if cached and cached.get("translation_status") in {"success", "cached"}:
                    cache_hits += 1
                    continue
                if cached and cached.get("translation_status") in {"partial", "failed", "qa_failed"} \
                        and not ((cached.get("translation_status") == "partial" and repair_partial)
                                 or (cached.get("translation_status") in {"failed", "qa_failed"}
                                     and repair_failed)):
                    continue
                if source in {"brand", "brand_es"}:
                    continue
                if source == "specification_es" and specification_is_deterministic(text):
                    continue
                items = self._structured_items(source, record.get(source))
                if items is not None:
                    for _, item_text in items:
                        item_memory = self.cache.get_memory(self._memory_key(item_text, source))
                        item_bypass = item_memory and (
                            (item_memory.get("translation_status") == "partial" and repair_partial)
                            or (item_memory.get("translation_status") in {"failed", "qa_failed"}
                                and repair_failed))
                        if item_memory and not item_bypass:
                            translation_memory_hits += 1
                        else:
                            unique_requests.add((translation_memory_field_type(source), source_hash(item_text),
                                                 self.provider.name, self.provider.model))
                else:
                    memory = self.cache.get_memory(self._memory_key(text, source))
                    bypass_memory = memory and ((memory.get("translation_status") == "partial" and repair_partial)
                                                or (memory.get("translation_status") in {"failed", "qa_failed"}
                                                    and repair_failed))
                    if memory and not bypass_memory:
                        translation_memory_hits += 1
                    else:
                        unique_requests.add((translation_memory_field_type(source), digest,
                                             self.provider.name, self.provider.model))
                rows.append({"asin": asin, "source_field": source, "target_field": target,
                             "source_hash": digest, "source_chars": len(text)})
        return {"schema_version": self.schema_version, "provider": self.provider.name,
                "model": self.provider.model, "total_records": len(subset),
                "total_fields": len(rows), "cache_hits": cache_hits,
                "translation_memory_hits": translation_memory_hits,
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
            raw_value = record.get(source_field)
            is_bullet_field = source_field in {"feature_bullets_es", "feature_bullets_raw", "features_es"}
            has_structured_value = (self._bullet_values(raw_value) is not None
                                    if is_bullet_field else self._structured_rows(raw_value) is not None)
            if has_structured_value:
                structured_result = self._translate_structured(
                    asin=asin, source_field=source_field, target=target,
                    source_text=text, raw_value=raw_value, record=record,
                    repair_partial=repair_partial, repair_failed=repair_failed)
                if structured_result:
                    self.cache.put(key, structured_result)
                    output_fields[target] = structured_result
                    continue
            if source_field in {"brand", "brand_es"}:
                result = self._deterministic_brand_result(
                    asin=asin, source_field=source_field, target=target, text=text)
                self.cache.put(key, result)
                output_fields[target] = result
                continue
            deterministic_result = self._deterministic_spec_result(
                asin=asin, source_field=source_field, target=target, text=text)
            if deterministic_result:
                self.cache.put(key, deterministic_result)
                self.cache.put_memory(self._memory_key(text, source_field), {
                    "translated_text": deterministic_result["translated_text"],
                    "translation_status": deterministic_result["translation_status"],
                    "qa_status": deterministic_result["qa_status"],
                    "qa_issues": list(deterministic_result["qa_issues"]),
                })
                output_fields[target] = deterministic_result
                continue
            memory_key = self._memory_key(text, source_field)
            memory = self._memory.get(memory_key) or self.cache.get_memory(memory_key)
            if memory and ((memory.get("translation_status") == "partial" and repair_partial)
                           or (memory.get("translation_status") in {"failed", "qa_failed"}
                               and repair_failed)):
                memory = None
            if memory:
                result = {"asin": asin, "field": source_field, "target_field": target,
                          "source_text": text, "source_hash": digest,
                          "translated_text": memory["translated_text"],
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
                                  brand=record.get("brand", ""),
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
                        "translated_text": result["translated_text"],
                        "translation_status": result["translation_status"],
                        "qa_status": result["qa_status"],
                        "qa_issues": list(result["qa_issues"]),
                    }
                    self.cache.put_memory(memory_key, self._memory[memory_key])
                elif response.status == "success":
                    result["translation_status"] = "qa_failed"
                    result["qa_status"] = "qa_failed"
                    result["qa_issues"] = [{"code": "EMPTY_TRANSLATION"}]
                elif response.status == "failed":
                    result["translation_status"] = "failed"
            self.cache.put(key, result)
            output_fields[target] = result
        if fields:
            requested = set(fields)
            present_targets = set(output_fields)
            for source_field, target in self.field_map.items():
                if source_field not in requested and target not in requested:
                    continue
                if target in present_targets:
                    continue
                output_fields[target] = {
                    "asin": asin, "field": source_field, "target_field": target,
                    "source_text": "", "source_hash": "", "translated_text": "",
                    "translation_status": "source_missing", "qa_status": "source_missing",
                    "provider": self.provider.name, "model": self.provider.model,
                    "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                    "attempt_count": 0, "last_error": None, "qa_issues": [],
                    "translated_at": self._now(),
                }
        statuses = [v.get("translation_status") for v in output_fields.values()]
        if not statuses:
            overall = "source_missing"
        elif all(s == "source_missing" for s in statuses):
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
            return {"records": {}, "summary": self.plan(records, fields=fields, offset=offset, limit=limit,
                                                          repair_partial=repair_partial, repair_failed=repair_failed),
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
