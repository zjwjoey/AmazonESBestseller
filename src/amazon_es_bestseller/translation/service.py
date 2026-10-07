"""Field-level Translation V2 orchestration.

The service is intentionally serial.  It owns source hashing, category
translation-memory deduplication, cache lookup, protection and per-field
failure isolation; providers only perform one translation request.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .cache import TranslationCache
from .protection import ProtectedText, protect, restore
from .providers.base import TranslationProvider
from .qa import build_qa_report, qa_field
from .schemas import TRANSLATION_SCHEMA_VERSION
from .terminology import (postprocess, contextual_postprocess, strip_display_brand,
                          normalize_unit_display, deterministic_specification,
                          specification_is_deterministic)
from .full_detail import LABEL_ES_ZH
from .zh import spec_zh_from
from .dictionary_service import DictionaryService, is_identity_attribute, normalize_key, resolve_exact
from .field_contract import canonical_translation_field_type, canonical_translation_unit_field
from .production_contract import CANONICAL_FIELD_TARGETS, PRODUCTION_FIELD_ALIASES

DEFAULT_FIELD_MAP = {
    alias: CANONICAL_FIELD_TARGETS[canonical]
    for canonical, aliases in PRODUCTION_FIELD_ALIASES.items()
    for alias in aliases
}


def source_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def translation_memory_field_type(field: str) -> str:
    """Use one TM namespace for equivalent category labels across levels."""
    return canonical_translation_field_type(field)


class TranslationService:
    def __init__(self, provider: TranslationProvider, cache: TranslationCache,
                 *, field_map: Optional[Dict[str, str]] = None,
                 schema_version: str = TRANSLATION_SCHEMA_VERSION,
                 prompt_version: str = "v1", max_fields: Optional[Sequence[str]] = None,
                 source_language: str = "es", target_language: str = "zh-CN",
                 dictionary_manifest: Optional[Dict[str, Any]] = None,
                 structured_schema_version: str = ""):
        self.provider = provider
        self.cache = cache
        self.field_map = dict(field_map or DEFAULT_FIELD_MAP)
        self.schema_version = schema_version
        self.prompt_version = prompt_version
        self.source_language = source_language
        self.target_language = target_language
        self.max_fields = set(max_fields) if max_fields else None
        self._memory: Dict[tuple, Dict[str, Any]] = {}
        # Provider-pool execution may translate several ASINs at once.  The
        # in-process TM is deliberately tiny, but it is still shared mutable
        # state and must not race with cache reads/writes.
        self._memory_lock = threading.RLock()
        # One shared lookup surface for deterministic labels.  The provider
        # still handles unresolved prose; this only prevents duplicate label
        # dictionaries from drifting between the offline and Qwen paths.
        self.dictionary_manifest = dict(dictionary_manifest or {})
        self.dictionary_version = str(self.dictionary_manifest.get("dictionary_version", "0"))
        self.dictionary_hash = str(self.dictionary_manifest.get("dictionary_hash", ""))
        # Kept separate from the field translation schema: formal structured
        # inputs have their own immutable contract and must not share a
        # derived result namespace merely because the provider is unchanged.
        self.structured_schema_version = str(structured_schema_version or "")
        self.dictionary = DictionaryService(manifest=self.dictionary_manifest)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _memory_key(self, text: str, field: str, *, label: Optional[str] = None) -> str:
        return self.cache.memory_key(
            text, self.source_language, self.target_language,
            canonical_translation_unit_field(field, label=label), self.provider.name,
            self.provider.model, self.schema_version, self.prompt_version,
            self.dictionary_version, self.dictionary_hash, self.structured_schema_version)

    def _field_cache_key(self, asin: str, source_field: str, digest: str) -> str:
        return self.cache.key(asin, source_field, digest, self.provider.name,
                              self.provider.model, self.schema_version, self.prompt_version,
                              self.dictionary_version, self.dictionary_hash,
                              self.structured_schema_version)

    def with_structured_schema_version(self, schema_version: str) -> "TranslationService":
        """Create an execution view without mutating a shared provider/pool."""
        return TranslationService(
            self.provider, self.cache, field_map=self.field_map,
            schema_version=self.schema_version, prompt_version=self.prompt_version,
            max_fields=sorted(self.max_fields) if self.max_fields else None,
            source_language=self.source_language, target_language=self.target_language,
            dictionary_manifest=self.dictionary_manifest,
            structured_schema_version=schema_version)

    def _memory_lookup(self, key: str, text: str, field: str, *, label: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Use current namespace first, then immutable cross-namespace TM.

        The latter is constrained by canonical field type, languages and
        source hash inside ``TranslationCache``; it never allows a changed
        dictionary/schema namespace to issue an identical provider request.
        """
        return (self._memory_get(key) or self.cache.get_memory(key) or
                self.cache.find_memory_result(
                    text, self.source_language, self.target_language,
                    canonical_translation_unit_field(field, label=label),
                    self.provider.name, self.provider.model))

    @staticmethod
    def _namespace_review(prior: Dict[str, Any]) -> Dict[str, Any]:
        """Keep an old raw candidate visible without certifying a new render."""
        result = dict(prior)
        status = result.get("translation_status")
        if status in {"success", "cached"}:
            result["translation_status"] = "pending"
            result["qa_status"] = "review_required"
            result["qa_issues"] = list(result.get("qa_issues") or []) + [
                {"code": "CACHE_NAMESPACE_REVIEW_REQUIRED"}]
        elif status == "pending":
            result["qa_status"] = "review_required"
            result["qa_issues"] = list(result.get("qa_issues") or []) + [
                {"code": "CACHE_PENDING_MANUAL_RESUME"}]
        result["resolution_source"] = "immutable_cache_namespace_reuse"
        return result

    def _memory_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._memory_lock:
            value = self._memory.get(key)
        return dict(value) if value is not None else None

    def _memory_put(self, key: str, value: Dict[str, Any]) -> None:
        with self._memory_lock:
            self._memory[key] = dict(value)

    def _stamp_dictionary_version(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Attach the cache namespace to every emitted field envelope."""
        envelope["dictionary_version"] = self.dictionary_version
        envelope["dictionary_hash"] = self.dictionary_hash
        return envelope

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
        if isinstance(value, str):
            rows = []
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            if not lines:
                return None
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

    @staticmethod
    def _prepared_value(record: Dict[str, Any], source_field: str) -> Any:
        """Read a Pre-Clean envelope without bypassing its admission gate."""
        prepared = record.get("fields")
        if isinstance(prepared, dict) and source_field in prepared:
            envelope = prepared[source_field]
            if not isinstance(envelope, dict) or not envelope.get("translate_allowed"):
                return None
            return envelope.get("clean_text") or ""
        return record.get(source_field)

    @classmethod
    def _record_brand(cls, record: Dict[str, Any]) -> str:
        value = cls._prepared_value(record, "brand")
        if value is None:
            value = record.get("brand") or record.get("brand_es") or ""
        return str(value or "").strip()

    def _resolve_scalar_before_provider(self, *, asin: str, source_field: str,
                                        target: str, text: str) -> Optional[Dict[str, Any]]:
        """Resolve a scalar through identity, dictionary and deterministic rules.

        ``None`` means that natural-language text still needs TM/provider
        handling.  The returned envelope is identical to a provider result so
        downstream QA/export code does not need a special branch.
        """
        if not text.strip():
            return {"asin": asin, "field": source_field, "target_field": target,
                    "source_text": text, "source_hash": source_hash(text),
                    "translated_text": "", "translation_status": "source_missing",
                    "qa_status": "source_missing", "provider": "deterministic",
                    "model": "rules-v1", "resolution_source": "source_missing",
                    "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                    "attempt_count": 0, "last_error": None, "qa_issues": [],
                    "translated_at": self._now()}
        brand = source_field in {"brand", "brand_es"}
        if brand:
            resolution = (text, "source_preserved", "identity-v1")
        elif source_field in {"category_l1", "category_l2", "category_l3", "leaf_category",
                              "category_l1_es", "category_l2_es", "category_l3_es", "leaf_category_es"}:
            row = resolve_exact(self.dictionary, text, kind="category")
            resolution = ((row["resolved_text"], row["resolution_source"], "dictionary-v1")
                          if row["status"] == "resolved" else None)
        elif source_field in {"selected_variation_raw", "selected_variant_es", "variation_es"}:
            row = resolve_exact(self.dictionary, text, kind="packaging", field="selected_variant_es")
            resolution = ((row["resolved_text"], row["resolution_source"], "dictionary-v1")
                          if row["status"] == "resolved" else None)
        elif source_field == "specification_es":
            # Use one deterministic path so specification fields receive the
            # same hard QA as provider results; do not mark a lossy rule output
            # as a successful translation merely because it is non-empty.
            return self._deterministic_spec_result(
                asin=asin, source_field=source_field, target=target, text=text)
        else:
            resolution = None
        if resolution is None:
            return None
        translated, origin, model = resolution
        return {"asin": asin, "field": source_field, "target_field": target,
                "source_text": text, "source_hash": source_hash(text),
                "translated_text": translated, "translation_status": "success",
                "qa_status": "pass", "provider": "deterministic", "model": model,
                "resolution_source": origin, "schema_version": self.schema_version,
                "prompt_version": self.prompt_version, "attempt_count": 0,
                "last_error": None, "qa_issues": [], "translated_at": self._now()}

    @classmethod
    def _structured_items(cls, source_field: str, raw_value: Any) -> Optional[List[tuple[Optional[str], str]]]:
        """Return lossless item boundaries used by both planning and execution."""
        is_bullet_field = source_field in {"feature_bullets", "feature_bullets_es", "feature_bullets_raw", "features_es"}
        if is_bullet_field:
            bullets = cls._bullet_values(raw_value)
            return [(None, value) for value in bullets] if bullets is not None else None
        if source_field not in {"product_details", "product_details_es", "detail_attributes_raw"}:
            return None
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
        rendered_items = []
        item_providers = []
        item_models = []
        item_aliases = []
        for index, (label, value) in enumerate(items):
            unit_field = canonical_translation_unit_field(source_field, label=label)
            memory_key = self._memory_key(value, unit_field)
            memory = self._memory_lookup(memory_key, value, unit_field, label=label)
            if memory and ((memory.get("translation_status") == "partial" and repair_partial)
                           or (memory.get("translation_status") in {"failed", "qa_failed"}
                               and repair_failed)):
                memory = None
            item_issues = []
            item_candidate_text = ""
            if memory:
                item_status = memory.get("translation_status", "success")
                statuses.append(item_status)
                item_issues = list(memory.get("qa_issues") or [])
                issues.extend({"item_index": index, **issue} for issue in item_issues)
                rendered_value = str(memory.get("translated_text") or "")
                item_candidate_text = str(memory.get("candidate_text") or rendered_value)
                item_provider = memory.get("provider", "cached")
                item_alias = memory.get("provider_alias")
                item_model = memory.get("model", self.provider.model)
                item_attempts = 0
                item_resolution = "cached"
            elif label is not None and is_identity_attribute(label):
                # Identity values (models, OEM/part numbers, company names,
                # UPC/EAN/ASIN/ISBN) never need machine translation.
                rendered_value = value
                item_candidate_text = rendered_value
                item_status = "success"
                statuses.append(item_status)
                item_provider = "deterministic"
                item_alias = None
                item_model = "identity-v1"
                item_attempts = 0
                item_resolution = "source_preserved"
            else:
                deterministic = (resolve_exact(
                    self.dictionary, value, kind="value", field=normalize_key(label))
                    if label is not None else None)
                if deterministic and deterministic["status"] == "resolved":
                    rendered_value = deterministic["resolved_text"]
                    item_candidate_text = rendered_value
                    item_status = "success"
                    statuses.append(item_status)
                    item_provider = "deterministic"
                    item_alias = None
                    item_model = "dictionary-v1"
                    item_attempts = 0
                    item_resolution = deterministic["resolution_source"]
                else:
                    brand = self._record_brand(record)
                    protected = protect(value, protected_values=[asin, brand])
                    response = self.provider.translate(
                        protected.text, asin=asin, field=source_field,
                        source_language=self.source_language, target_language=self.target_language,
                        context={"target_field": target, "item_index": index,
                                 "label": label, "translation_unit_field": unit_field,
                                 "protected_tokens": list(protected.tokens),
                                 "schema_version": self.schema_version,
                                 "prompt_version": self.prompt_version,
                                 "dictionary_version": self.dictionary_version})
                    attempts += response.attempts
                    item_provider = response.provider or self.provider.name
                    item_alias = (response.raw or {}).get("provider_alias")
                    item_model = response.model or self.provider.model
                    item_candidate_text = response.text or ""
                    item_attempts = response.attempts
                    item_resolution = "provider"
                    if response.status == "success" and response.text:
                        normalized = postprocess(source_field, response.text, value)
                        restored, _ = restore(protected, normalized)
                        restored = normalize_unit_display(restored)
                        restored = contextual_postprocess(source_field, restored, value)
                        qa = qa_field(protected, restored, value, field=source_field,
                                      brand=brand,
                                      # ``para`` is a frequent legitimate
                                      # connector in short bullet fragments;
                                      # retain the high-signal residual checks
                                      # without failing the whole item on it.
                                      allowed_residual=["para"])
                        item_issues = list(qa["issues"])
                        rendered_value = restored
                        item_status = "qa_failed" if item_issues else "success"
                        statuses.append(item_status)
                        issues.extend({"item_index": index, **issue} for issue in item_issues)
                        memory_payload = {
                            "candidate_text": item_candidate_text,
                            "translated_text": rendered_value,
                            "translation_status": item_status,
                            "qa_status": "qa_failed" if item_issues else "pass",
                            "qa_issues": list(item_issues),
                            "provider": item_provider,
                            "provider_alias": item_alias,
                            "model": item_model,
                            "translated_at": self._now(),
                        }
                        self._memory_put(memory_key, memory_payload)
                        self.cache.put_memory(memory_key, memory_payload)
                    elif response.status == "success":
                        item_status = "qa_failed"
                        statuses.append("qa_failed")
                        rendered_value = ""
                        item_issues = [{"code": "EMPTY_TRANSLATION"}]
                        issues.extend({"item_index": index, **issue} for issue in item_issues)
                    elif response.status == "failed" and response.error == "EMPTY_TRANSLATION":
                        item_status = "qa_failed"
                        statuses.append("qa_failed")
                        rendered_value = ""
                        item_issues = [{"code": "EMPTY_TRANSLATION"}]
                        issues.extend({"item_index": index, **issue} for issue in item_issues)
                    elif response.status == "pending":
                        item_status = "pending"
                        statuses.append("pending")
                        rendered_value = ""
                        if response.error:
                            errors.append(str(response.error))
                    else:
                        item_status = "failed"
                        statuses.append("failed")
                        rendered_value = ""
                        if response.error:
                            errors.append(str(response.error))
            item_providers.append(item_provider)
            item_models.append(item_model)
            item_aliases.append(item_alias)
            rendered_items.append({"item_index": index,
                                   "item_identity": "%s:%s:%s" % (source_field, index, source_hash(value)),
                                   "label": label,
                                   "source_text": value, "translated_text": rendered_value,
                                   "translation_status": item_status,
                                   "qa_status": ("pass" if item_status == "success" else
                                                 "qa_failed" if item_status == "qa_failed" else "pending"),
                                   "provider": item_provider, "model": item_model,
                                   "provider_alias": item_alias,
                                   "attempt_count": item_attempts,
                                   "resolution_source": item_resolution,
                                   "candidate_text": item_candidate_text})
            if label is not None:
                label_zh = (self.dictionary.lookup_attribute_label(label.strip())
                            or LABEL_ES_ZH.get(label.strip().casefold(), label.strip()))
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
        elif any(status == "pending" for status in statuses):
            overall = "pending"
            qa_status = "pending"
        else:
            overall = "failed"
            qa_status = "pending"
        all_deterministic = bool(item_providers) and all(p == "deterministic" for p in item_providers)
        all_identity = all_deterministic and all(m == "identity-v1" for m in item_models)
        return {"asin": asin, "field": source_field, "target_field": target,
                "source_text": source_text, "source_hash": source_hash(source_text),
                "translated_text": "\n".join(rendered),
                "translation_status": overall, "qa_status": qa_status,
                "provider": "deterministic" if all_deterministic else self.provider.name,
                "provider_alias": next((alias for alias in item_aliases if alias), None),
                "model": "identity-v1" if all_identity else ("dictionary-v1" if all_deterministic else self.provider.model),
                "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                "attempt_count": attempts, "last_error": "; ".join(errors) or None,
                "items": rendered_items,
                "qa_issues": issues, "translated_at": self._now()}

    def _deterministic_spec_result(self, *, asin: str, source_field: str,
                                   target: str, text: str) -> Optional[Dict[str, Any]]:
        if source_field != "specification_es" or not specification_is_deterministic(text):
            return None
        translated = deterministic_specification(text)
        if not translated or translated == text:
            return None
        # Deterministic output is already rendered from the immutable source;
        # use the source as the QA envelope instead of requiring provider-style
        # placeholders for every numeric token.
        protected = ProtectedText(text)
        qa = qa_field(protected, translated, text, field=source_field)
        issues = list(qa["issues"])
        status = "qa_failed" if issues else "success"
        return {"asin": asin, "field": source_field, "target_field": target,
                "source_text": text, "source_hash": source_hash(text),
                "translated_text": translated, "translation_status": status,
                "qa_status": "qa_failed" if issues else "pass",
                "provider": "deterministic", "model": "rules-v1",
                "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                "attempt_count": 0, "last_error": None, "qa_issues": issues,
                "candidate_text": translated,
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
            value = self._prepared_value(record, source)
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
        review_blocked = 0
        unique_requests = set()
        for record in subset:
            asin = str(record.get("asin") or "").strip().upper()
            selected = self.selected_fields(record, fields)
            envelopes = record.get("fields") if isinstance(record.get("fields"), dict) else None
            if envelopes is not None:
                # Prepared records carry one admission envelope per canonical
                # source field.  Count the gate at field level even when the
                # same SKU has other fields eligible for translation; the old
                # record-level fallback silently hid blocked detail/bullet
                # fields whenever ``selected`` was non-empty.
                requested = set(fields or ())
                for source_field, envelope in envelopes.items():
                    if not isinstance(envelope, dict):
                        continue
                    target = self.field_map.get(source_field)
                    if requested and source_field not in requested and target not in requested:
                        continue
                    has_source = bool(envelope.get("source_text") or envelope.get("clean_text"))
                    if not has_source:
                        source_missing += 1
                    elif not envelope.get("translate_allowed"):
                        review_blocked += 1
            elif not selected:
                # Raw/non-preclean inputs retain the historical record-level
                # fallback; raw rows are normally admitted through Pre-Clean
                # before this method is called by the CLI.
                source_missing += 1
            for source, target, text in selected:
                digest = source_hash(text)
                key = self._field_cache_key(asin, source, digest)
                deterministic = self._resolve_scalar_before_provider(
                    asin=asin, source_field=source, target=target, text=text)
                if deterministic is not None:
                    continue
                cached = self.cache.get(key) or self.cache.find_result(asin, source, digest)
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
                items = self._structured_items(source, self._prepared_value(record, source))
                if items is not None:
                    for item_label, item_text in items:
                        if item_label is not None and is_identity_attribute(item_label):
                            continue
                        if (item_label is not None
                                and resolve_exact(self.dictionary, item_text, kind="value",
                                                  field=normalize_key(item_label))["status"] == "resolved"):
                            continue
                        unit_field = canonical_translation_unit_field(source, label=item_label)
                        item_memory = self._memory_lookup(
                            self._memory_key(item_text, unit_field), item_text, unit_field,
                            label=item_label)
                        item_bypass = item_memory and (
                            (item_memory.get("translation_status") == "partial" and repair_partial)
                            or (item_memory.get("translation_status") in {"failed", "qa_failed"}
                                and repair_failed))
                        if item_memory and not item_bypass:
                            translation_memory_hits += 1
                        else:
                            unique_requests.add((unit_field, source_hash(item_text),
                                                 self.source_language, self.target_language,
                                                 self.provider.name, self.provider.model))
                else:
                    memory = self._memory_lookup(self._memory_key(text, source), text, source)
                    bypass_memory = memory and ((memory.get("translation_status") == "partial" and repair_partial)
                                                or (memory.get("translation_status") in {"failed", "qa_failed"}
                                                    and repair_failed))
                    if memory and not bypass_memory:
                        translation_memory_hits += 1
                    else:
                        unique_requests.add((translation_memory_field_type(source), digest,
                                             self.source_language, self.target_language,
                                             self.provider.name, self.provider.model))
                rows.append({"asin": asin, "source_field": source, "target_field": target,
                             "source_hash": digest, "source_chars": len(text)})
        return {"schema_version": self.schema_version, "provider": self.provider.name,
                "model": self.provider.model, "total_records": len(subset),
                "total_fields": len(rows), "cache_hits": cache_hits,
                "translation_memory_hits": translation_memory_hits,
                "source_missing": source_missing,
                "review_blocked": review_blocked,
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
            key = self._field_cache_key(asin, source_field, digest)
            deterministic_result = self._resolve_scalar_before_provider(
                asin=asin, source_field=source_field, target=target, text=text)
            if deterministic_result is not None:
                self._stamp_dictionary_version(deterministic_result)
                self.cache.put(key, deterministic_result)
                output_fields[target] = deterministic_result
                continue
            cached = self.cache.get(key)
            if cached and cached.get("resolution_source") == "immutable_cache_namespace_reuse":
                output_fields[target] = cached
                continue
            if cached and cached.get("translation_status") == "partial" and not repair_partial:
                output_fields[target] = cached
                continue
            if cached and cached.get("translation_status") in {"failed", "qa_failed"} and not repair_failed:
                output_fields[target] = cached
                continue
            if cached and cached.get("translation_status") in {"success", "cached"}:
                cached["translation_status"] = "cached"
                output_fields[target] = cached
                continue
            # A result under an older dictionary/schema namespace is evidence
            # of a prior provider attempt. Closure no-repeat must surface it
            # for offline re-render/re-QA, never silently call again.
            prior = self.cache.find_result(asin, source_field, digest)
            if prior:
                reused = self._namespace_review(prior)
                self._stamp_dictionary_version(reused)
                self.cache.put(key, reused)
                output_fields[target] = reused
                continue
            raw_value = self._prepared_value(record, source_field)
            is_bullet_field = source_field in {"feature_bullets", "feature_bullets_es", "feature_bullets_raw", "features_es"}
            has_structured_value = (self._bullet_values(raw_value) is not None
                                    if is_bullet_field else self._structured_rows(raw_value) is not None)
            if has_structured_value:
                structured_result = self._translate_structured(
                    asin=asin, source_field=source_field, target=target,
                    source_text=text, raw_value=raw_value, record=record,
                    repair_partial=repair_partial, repair_failed=repair_failed)
                if structured_result:
                    self._stamp_dictionary_version(structured_result)
                    self.cache.put(key, structured_result)
                    output_fields[target] = structured_result
                    continue
            memory_key = self._memory_key(text, source_field)
            memory = self._memory_lookup(memory_key, text, source_field)
            if memory and memory.get("translation_status") == "pending":
                result = {"asin": asin, "field": source_field, "target_field": target,
                          "source_text": text, "source_hash": digest,
                          "translated_text": str(memory.get("translated_text") or ""),
                          "candidate_text": str(memory.get("candidate_text") or ""),
                          "translation_status": "pending", "qa_status": "review_required",
                          "provider": memory.get("provider", self.provider.name),
                          "provider_alias": memory.get("provider_alias"),
                          "model": memory.get("model", self.provider.model),
                          "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                          "attempt_count": 0, "last_error": memory.get("last_error"),
                          "qa_issues": list(memory.get("qa_issues") or []) + [
                              {"code": "CACHE_PENDING_MANUAL_RESUME"}],
                          "translated_at": self._now(),
                          "resolution_source": "immutable_cache_namespace_reuse"}
                self._stamp_dictionary_version(result)
                self.cache.put(key, result)
                output_fields[target] = result
                continue
            if memory and ((memory.get("translation_status") == "partial" and repair_partial)
                           or (memory.get("translation_status") in {"failed", "qa_failed"}
                               and repair_failed)):
                memory = None
            if memory:
                result = {"asin": asin, "field": source_field, "target_field": target,
                          "source_text": text, "source_hash": digest,
                          "translated_text": memory["translated_text"],
                          "candidate_text": memory.get("candidate_text", memory["translated_text"]),
                          "translation_status": "cached" if memory["translation_status"] == "success" else memory["translation_status"],
                          "qa_status": memory["qa_status"],
                          "provider": memory.get("provider", self.provider.name),
                          "provider_alias": memory.get("provider_alias"),
                          "model": memory.get("model", self.provider.model),
                          "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                          "attempt_count": 0, "last_error": None,
                          "qa_issues": list(memory["qa_issues"]),
                          "translated_at": self._now()}
            else:
                # Protect numbers and explicit identity tokens.  Brand and ASIN
                # are always protected even when translating another field.
                brand = self._record_brand(record)
                protected = protect(text, protected_values=[asin, brand])
                response = self.provider.translate(protected.text, asin=asin, field=source_field,
                                                   source_language=self.source_language,
                                                   target_language=self.target_language,
                                                   context={"target_field": target, "protected_tokens": list(protected.tokens),
                                                   "schema_version": self.schema_version,
                                                   "prompt_version": self.prompt_version,
                                                   "dictionary_version": self.dictionary_version})
                result = {"asin": asin, "field": source_field, "target_field": target,
                          "source_text": text, "source_hash": digest,
                          "translated_text": response.text or "", "translation_status": response.status,
                          "candidate_text": response.text or "",
                          "qa_status": "pending", "provider": response.provider or self.provider.name,
                          "provider_alias": (response.raw or {}).get("provider_alias"),
                          "model": response.model or self.provider.model,
                          "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                          "attempt_count": response.attempts, "last_error": response.error,
                          "qa_issues": [], "translated_at": self._now()}
                if response.status == "success" and response.text:
                    # Keep protected identity values as placeholders while the
                    # terminology normalizer runs; otherwise a brand such as
                    # ``Metal`` would be translated as a material term. QA is
                    # deliberately flag-only: the raw provider candidate is
                    # preserved and no facts are appended or deleted here.
                    normalized = postprocess(source_field, response.text, text)
                    restored, _ = restore(protected, normalized)
                    restored = normalize_unit_display(restored)
                    restored = contextual_postprocess(source_field, restored, text)
                    if source_field in {"title_es_raw", "title_es", "title"}:
                        restored = strip_display_brand(restored, brand)
                    qa = qa_field(protected, restored, text, field=source_field,
                                  brand=brand, allowed_residual=[brand])
                    result["translated_text"] = restored
                    result["qa_status"] = qa["qa_status"]
                    result["qa_issues"] = list(qa["issues"])
                    if result["qa_issues"]:
                        result["translation_status"] = "qa_failed"
                    # Translation memory deduplicates provider calls even when
                    # the identical source later needs the same QA review.
                    memory_payload = {
                        "translated_text": result["translated_text"],
                        "candidate_text": result.get("candidate_text", result["translated_text"]),
                        "translation_status": result["translation_status"],
                        "qa_status": result["qa_status"],
                        "qa_issues": list(result["qa_issues"]),
                        "provider": result["provider"],
                        "provider_alias": result.get("provider_alias"),
                        "model": result["model"],
                        "translated_at": result["translated_at"],
                    }
                    self._memory_put(memory_key, memory_payload)
                    self.cache.put_memory(memory_key, memory_payload)
                elif response.status == "success":
                    result["translation_status"] = "qa_failed"
                    result["qa_status"] = "qa_failed"
                    result["qa_issues"] = [{"code": "EMPTY_TRANSLATION"}]
                elif response.status == "failed" and response.error == "EMPTY_TRANSLATION":
                    result["translation_status"] = "qa_failed"
                    result["qa_status"] = "qa_failed"
                    result["qa_issues"] = [{"code": "EMPTY_TRANSLATION"}]
                elif response.status == "failed":
                    result["translation_status"] = "failed"
            self._stamp_dictionary_version(result)
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
                envelope = ((record.get("fields") or {}).get(source_field)
                            if isinstance(record.get("fields"), dict) else None)
                if isinstance(envelope, dict):
                    source_value = str(envelope.get("source_text") or
                                       envelope.get("clean_text") or "")
                else:
                    source_value = ""
                if source_value and isinstance(envelope, dict) and not envelope.get("translate_allowed"):
                    preclean_issues = [
                        {"code": "PRECLEAN_REVIEW_REQUIRED", "issue": issue,
                         "severity": envelope.get("severity", "P2")}
                        for issue in (envelope.get("issues") or ["NEEDS_REVIEW"])
                    ]
                    output_fields[target] = {
                        "asin": asin, "field": source_field, "target_field": target,
                        "source_text": source_value, "source_hash": source_hash(source_value),
                        "translated_text": "", "candidate_text": "",
                        "translation_status": "preclean_blocked", "qa_status": "review_required",
                        "provider": "preclean", "model": "preclean-v1",
                        "resolution_source": "preclean_review",
                        "preclean_status": envelope.get("clean_status"),
                        "preclean_issues": list(envelope.get("issues") or []),
                        "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                        "attempt_count": 0, "last_error": None, "qa_issues": preclean_issues,
                        "translated_at": self._now(),
                    }
                else:
                    output_fields[target] = {
                        "asin": asin, "field": source_field, "target_field": target,
                        "source_text": "", "source_hash": "", "translated_text": "",
                        "translation_status": "source_missing", "qa_status": "source_missing",
                        "provider": self.provider.name, "model": self.provider.model,
                        "schema_version": self.schema_version, "prompt_version": self.prompt_version,
                        "attempt_count": 0, "last_error": None, "qa_issues": [],
                        "translated_at": self._now(),
                    }
                present_targets.add(target)
        for envelope in output_fields.values():
            self._stamp_dictionary_version(envelope)
        statuses = [v.get("translation_status") for v in output_fields.values()]
        blocked = "preclean_blocked" in statuses
        actionable_statuses = [status for status in statuses
                              if status not in {"source_missing", "preclean_blocked"}]
        if not statuses or not actionable_statuses:
            overall = "preclean_blocked" if blocked else "source_missing"
        elif blocked and all(s in {"success", "cached"} for s in actionable_statuses):
            overall = "partial"
        elif all(s in {"success", "cached"} for s in actionable_statuses):
            overall = "success"
        elif any(s in {"success", "cached"} for s in actionable_statuses):
            overall = "partial"
        elif any(s == "qa_failed" for s in actionable_statuses):
            overall = "qa_failed"
        elif any(s == "pending" for s in actionable_statuses):
            overall = "pending"
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

    def translate_records_parallel(self, records: Sequence[Dict[str, Any]], pool: Any, *,
                                   fields: Optional[Sequence[str]] = None,
                                   offset: int = 0, limit: Optional[int] = None,
                                   repair_partial: bool = False, repair_failed: bool = False,
                                   dry_run: bool = False) -> Dict[str, Any]:
        """Run record workers through a ProviderPool while preserving order.

        Deterministic fields still short-circuit inside ``_translate_record``;
        only unresolved provider units reach the pool.  Cache writes are
        protected by TranslationCache's shared lock and saved once at the end.
        """
        if dry_run:
            result = self.translate_records(records, fields=fields, offset=offset, limit=limit,
                                            repair_partial=repair_partial, repair_failed=repair_failed,
                                            dry_run=True)
            result["pool"] = pool.snapshot()
            return result
        from .pool import PoolProviderAdapter
        subset = list(records)[max(0, offset):]
        if limit is not None:
            subset = subset[:max(0, limit)]
        original_provider = self.provider
        self.provider = PoolProviderAdapter(pool, model=getattr(original_provider, "model", "qwen-mt-flash"))
        results: list[Optional[Dict[str, Any]]] = [None] * len(subset)
        try:
            with ThreadPoolExecutor(max_workers=pool.max_workers, thread_name_prefix="translation-record") as executor:
                futures = {executor.submit(self._translate_record, record, fields=fields,
                                            repair_partial=repair_partial, repair_failed=repair_failed): index
                           for index, record in enumerate(subset)}
                for future in as_completed(futures):
                    index = futures[future]
                    results[index] = future.result()
                    # Persist each completed record so interruption or a later
                    # worker exception never discards already completed work.
                    self.cache.save()
        finally:
            self.provider = original_provider
            self.cache.save()
        outputs = {result["asin"]: result for result in results if result and result.get("asin")}
        # Keep the same flat display overlay as the serial path for fields
        # that passed QA. The auditable envelopes remain authoritative for
        # partial/failed fields.
        for result in outputs.values():
            for target, value in (result.get("fields") or {}).items():
                if value.get("translated_text") and value.get("translation_status") in {"success", "cached"}:
                    result[target] = value["translated_text"]
        summary = {"total": len(outputs)}
        for result in outputs.values():
            status = result.get("translation_status", "pending")
            summary[status] = summary.get(status, 0) + 1
        return {"records": outputs, "summary": summary,
                "qa_report": build_qa_report(outputs.values()), "pool": pool.snapshot()}
