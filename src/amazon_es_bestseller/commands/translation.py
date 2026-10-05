"""Offline implementation of the staged production-translation command.

The command creates immutable, source-hash-bound artifacts.  It intentionally
uses only ``FakeTranslationProvider``: this repository slice must never turn a
CLI test or a production checkpoint resume into a provider/API request.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ..translation.cache import TranslationCache
from ..translation.preclean import audit_records, write_reports
from ..translation.production import (
    build_production_input, build_production_state, merge_translation_shards,
    records_for_preclean, release_gate, shard_batch_id, shard_immutable_equal,
)
from ..translation.production_contract import matches_category_filter, normalize_asin_filter
from ..translation.providers.base import ProviderResponse, TranslationProvider
from ..translation.schemas import TRANSLATION_SCHEMA_VERSION
from ..translation.service import TranslationService


class FakeTranslationProvider(TranslationProvider):
    """Deterministic test provider with no credentials, transport, or network."""

    name = "fake"

    @property
    def model(self) -> str:
        return "fake-offline-v1"

    def translate(self, text: str, *, asin: str, field: str,
                  source_language: str = "es", target_language: str = "zh-CN",
                  context: dict[str, Any] | None = None) -> ProviderResponse:
        value = str(text or "")
        replacements = (
            ("Bolsa térmica", "保温袋"), ("bolsa térmica", "保温袋"),
            ("Producto", "商品"), ("producto", "商品"),
            ("Capacidad", "容量"), ("Color", "颜色"), ("Rojo", "红色"),
            ("Voltaje", "电压"), ("Sin alcohol", "不含酒精"), ("No", "否"),
        )
        for source, target in replacements:
            value = value.replace(source, target)
        # A fake response must not pretend that arbitrary Spanish was reviewed.
        # Preserve numbers/unit evidence, but make the synthetic nature explicit.
        if re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", value):
            tokens = " ".join(re.findall(r"\d+(?:[.,]\d+)?\s*[A-Za-z%]+", value))
            value = ("离线模拟译文 " + tokens).strip()
        return ProviderResponse(text=value, provider=self.name, model=self.model,
                                status="success", attempts=0,
                                raw={"offline": True, "asin": asin, "field": field})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selected_records(records: list[dict[str, Any]], args: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    asin_filter: set[str] = set()
    if args.asin_list:
        value: Any = args.asin_list
        possible_path = Path(str(value))
        if possible_path.exists():
            value = json.loads(possible_path.read_text(encoding="utf-8"))
        asin_filter = set(normalize_asin_filter(value))
    selected = records
    if asin_filter:
        selected = [row for row in selected if str(row.get("asin") or "").strip().upper() in asin_filter]
    if args.category:
        selected = [row for row in selected if matches_category_filter(row, args.category)]
    selected = selected[max(0, int(args.offset or 0)):]
    if args.limit is not None:
        selected = selected[:max(0, int(args.limit))]
    selection = {"offset": args.offset, "limit": args.limit,
                 "asin_filter": sorted(asin_filter), "category": args.category,
                 "record_count": len(selected)}
    return selected, selection


def _service(run_dir: Path, config: Mapping[str, Any], args: Any) -> TranslationService:
    provider_mode = str(config.get("provider_mode") or "fake").strip().lower()
    if provider_mode == "qwen-mt":
        from ..translation.providers.qwen_mt import QwenMTProvider
        from ..translation.budget import BudgetLedger, BudgetedProvider, VerifiedPriceCard
        provider: TranslationProvider = QwenMTProvider(
            model=args.model or str(config.get("model") or "qwen-mt-flash"),
            endpoint=config.get("endpoint"), protocol=config.get("protocol"),
            rate=float(args.rate if args.rate is not None else config.get("rate", 0.5)),
            # Retries must re-enter BudgetedProvider below so each transport
            # attempt is separately reserved and charged.  Do not keep hidden
            # provider-internal retry attempts beneath one reservation.
            max_retries=0,
        )
        if not provider.api_key:
            raise SystemExit("QWEN_CREDENTIAL_UNAVAILABLE: real translation not started")
        # A real provider is never allowed to run on an unpriced or
        # non-CNY configuration. The ledger survives resume and reserves the
        # worst permitted request before Qwen transport is entered.
        price_card = VerifiedPriceCard.from_mapping(
            config.get("pricing_verification"), provider=provider.name, model=provider.model,
        )
        ledger = BudgetLedger(
            run_dir / "budget" / "translation_budget_ledger.json",
            limit_cny=config.get("budget_cny", "5.00"), price_card=price_card,
            safety_buffer_cny=config.get("budget_safety_buffer_cny", "0.10"),
            max_input_tokens=int(config.get("budget_max_input_tokens", 12000)),
            max_output_tokens=int(config.get("budget_max_output_tokens", 2048)),
            prompt_overhead_tokens=int(config.get("budget_prompt_overhead_tokens", 1024)),
        )
        provider = BudgetedProvider(provider, ledger)
    elif provider_mode == "fake":
        provider = FakeTranslationProvider()
    else:
        raise SystemExit("unsupported provider_mode: %s" % provider_mode)
    return TranslationService(
        provider, TranslationCache(run_dir / "translations" / "translation_cache.json"),
        schema_version=str(config.get("schema_version") or TRANSLATION_SCHEMA_VERSION),
        prompt_version=str(config.get("prompt_version") or "amazon-es-retail-v2"),
        source_language=str(config.get("source_language") or "es"),
        target_language=str(config.get("target_language") or "zh-CN"),
    )


def _plan(service: TranslationService, records: list[dict[str, Any]], config: Mapping[str, Any],
          production_input: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
    result = service.translate_records(records, fields=config.get("fields") or None, dry_run=True)
    plan = dict(result["summary"])
    aliases = [str(item.get("name") or item.get("alias") or "")
               for item in config.get("providers", []) if isinstance(item, Mapping)]
    total = int(plan.get("estimated_api_requests", 0))
    plan.update({
        "unique_asins": len({str(row.get("asin") or "").upper() for row in records if row.get("asin")} ),
        "dataset_hash": (production_input.get("manifest") or {}).get("dataset_hash"),
        "prompt_version": config.get("prompt_version", "amazon-es-retail-v2"),
        "schema_version": config.get("schema_version", TRANSLATION_SCHEMA_VERSION),
        "selection": dict(selection), "provider": "fake", "offline": True,
        "pool": {"max_workers": int(config.get("max_workers", 1))},
        "estimated_provider_requests": {
            alias: total // len(aliases) + (1 if index < total % len(aliases) else 0)
            for index, alias in enumerate(aliases)
        } if aliases else {},
    })
    return plan


def _current_release_is_bound(state: Mapping[str, Any], production_input: Mapping[str, Any]) -> bool:
    if (state.get("input_manifest") or {}).get("dataset_hash") != (production_input.get("manifest") or {}).get("dataset_hash"):
        return False
    expected = {str(item.get("asin") or "").upper(): item.get("source_record_hash")
                for item in production_input.get("records", [])}
    actual_rows = list(state.get("records") or [])
    actual = {str(row.get("asin") or "").upper(): row for row in actual_rows if row.get("asin")}
    if not expected or set(actual) != set(expected):
        return False
    for item in production_input.get("records", []):
        asin = str(item.get("asin") or "").upper()
        row = actual.get(asin, {})
        if row.get("source_record_hash") != expected[asin]:
            return False
        expected_fields = item.get("fields") or {}
        actual_fields = {field.get("field"): field for field in row.get("fields", []) if isinstance(field, Mapping)}
        if set(actual_fields) != set(expected_fields):
            return False
        if any(actual_fields[name].get("source_hash") != source.get("source_hash")
               for name, source in expected_fields.items()):
            return False
    return True


def run_translation_production(args: Any, *, load_products: Callable[[str], list],
                               load_json: Callable[[str], Any], save_json: Callable[[Any, str], None],
                               load_evidence_json: Callable[[str, str], Any],
                               default_details: str, default_rankings: str,
                               load_images: Callable[[str, list], Mapping],
                               load_category_planning: Callable[[str], Any]) -> None:
    """Run one production stage; all writes stay under the explicit run directory."""
    run_dir = Path(args.run_dir or (Path("runtime") / "translation_v2" / "production" / args.run_id))
    stage = args.stage
    if stage == "build-input":
        records = load_products(args.master)
        if not isinstance(records, list):
            raise SystemExit("production Master must be a JSON array or research CSV")
        result = build_production_input(records, source_run_id=args.source_run_id,
                                        source_schema_version=args.source_schema_version,
                                        translation_schema_version=TRANSLATION_SCHEMA_VERSION, run_id=args.run_id)
        save_json(result["manifest"], str(run_dir / "input_manifest.json"))
        save_json(result, str(run_dir / "translation_input.json"))
        return
    input_path = run_dir / "translation_input.json"
    if not input_path.exists():
        raise SystemExit("missing production input: %s" % input_path)
    production_input = load_json(str(input_path))
    if not isinstance(production_input, Mapping) or not isinstance(production_input.get("records"), list):
        raise SystemExit("invalid production translation_input.json")
    if stage == "preclean":
        result = audit_records(records_for_preclean(production_input))
        write_reports(result, run_dir / "preclean")
        save_json({"manifest": production_input.get("manifest"), "summary": result["summary"]},
                  str(run_dir / "preclean" / "production_preclean_manifest.json"))
        return
    preclean_path = run_dir / "preclean" / "translation_input_records.json"
    if not preclean_path.exists():
        raise SystemExit("production-preclean required: %s" % preclean_path)
    wrapper = load_json(str(preclean_path))
    preclean_records = wrapper.get("records", []) if isinstance(wrapper, Mapping) else wrapper
    if stage in {"plan", "translate"}:
        config = load_json(args.config) if args.config and Path(args.config).exists() else {}
        if not isinstance(config, Mapping):
            raise SystemExit("production config must be an object")
        records, selection = _selected_records(list(preclean_records), args)
        service = _service(run_dir, config, args)
        if stage == "plan" or args.dry_run:
            plan = _plan(service, records, config, production_input, selection)
            save_json(plan, str(run_dir / "plan" / "translation_plan.json"))
            return
        if not args.yes:
            raise SystemExit("production-translate requires --yes (offline fake provider)")
        result = service.translate_records(records, fields=config.get("fields") or None)
        manifest = production_input.get("manifest") or {}
        batch_id = shard_batch_id(dataset_hash=str(manifest.get("dataset_hash") or ""), selection=selection,
                                  prompt_version=str(config.get("prompt_version") or "amazon-es-retail-v2"),
                                  schema_version=str(config.get("schema_version") or TRANSLATION_SCHEMA_VERSION))
        provider_mode = str(config.get("provider_mode") or "fake").lower()
        batch_id = shard_batch_id(dataset_hash=str(manifest.get("dataset_hash") or ""), selection=selection,
                                  prompt_version=str(config.get("prompt_version") or "amazon-es-retail-v2"),
                                  schema_version=str(config.get("schema_version") or TRANSLATION_SCHEMA_VERSION),
                                  provider=service.provider.name, execution_mode=provider_mode)
        shard = {"run_id": args.run_id, "batch_id": batch_id, "created_at": _now(), "selection": selection,
                 "input_dataset_hash": manifest.get("dataset_hash"), "translation_schema_version": TRANSLATION_SCHEMA_VERSION,
                 "prompt_version": config.get("prompt_version", "amazon-es-retail-v2"), "provider": service.provider.name,
                 "execution_mode": provider_mode, "records": result["records"]}
        shard_dir = run_dir / "translations" / "shards"
        shard_path = shard_dir / (batch_id + ".json")
        existing = load_json(str(shard_path)) if shard_path.exists() else None
        if existing is None:
            save_json(shard, str(shard_path))
        elif not shard_immutable_equal(existing, shard):
            raise SystemExit("BATCH_ID_CONFLICT: immutable shard differs: %s" % batch_id)
        shards = [load_json(str(path)) for path in sorted(shard_dir.glob("batch_*.json"))]
        aggregate = merge_translation_shards(shards)
        save_json(aggregate, str(run_dir / "translations" / "translation_results.json"))
        save_json(result["qa_report"], str(run_dir / "qa" / "translation_qa.json"))
        save_json({"summary": result["summary"], "manifest": manifest, "batch_id": batch_id,
                   "aggregate_record_count": len(aggregate), "provider": "fake"},
                  str(run_dir / "translations" / "translation_run.json"))
        return
    if stage == "promote":
        translations_path = run_dir / "translations" / "translation_results.json"
        if not translations_path.exists():
            raise SystemExit("production-translate required: %s" % translations_path)
        config = load_json(args.config) if args.config and Path(args.config).exists() else {}
        shards = [load_json(str(path)) for path in sorted((run_dir / "translations" / "shards").glob("batch_*.json"))]
        modes = {str(shard.get("execution_mode") or "unknown").lower() for shard in shards}
        providers = {str(shard.get("provider") or "unknown").lower() for shard in shards}
        execution_mode = next(iter(modes)) if len(modes) == 1 else "mixed"
        state = build_production_state(production_input, preclean_records, load_json(str(translations_path)),
                                       qa_version=str(config.get("qa_version") or "translation-qa-v1"),
                                       prompt_version=str(config.get("prompt_version") or "amazon-es-retail-v2"))
        # Promotion trusts immutable translation evidence, never a later CLI
        # config.  Missing or mixed evidence is deliberately non-formal.
        state["translation_execution_mode"] = execution_mode
        state["translation_providers"] = sorted(providers)
        state["release_candidate"]["translation_execution_mode"] = state["translation_execution_mode"]
        save_json(state, str(run_dir / "state" / "translation_state.json"))
        save_json(state["release_candidate"], str(run_dir / "release" / "production_release_candidate.json"))
        save_json(state["repair_queue"], str(run_dir / "repair" / "repair_queue.json"))
        save_json(state["summary"], str(run_dir / "state" / "production_summary.json"))
        return
    if stage == "export":
        if args.force:
            raise SystemExit("translation-production 不支持 --force; use --debug-export for NOT_FOR_RELEASE output")
        release_path = run_dir / "release" / "production_release_candidate.json"
        state_path = run_dir / "state" / "translation_state.json"
        if not release_path.exists() or not state_path.exists():
            raise SystemExit("production-promote required")
        release, state = load_json(str(release_path)), load_json(str(state_path))
        if not _current_release_is_bound(state, production_input):
            raise SystemExit("Release Gate blocked: stored READY failed source-hash revalidation")
        ready, status = release_gate(release)
        execution_mode = str(state.get("translation_execution_mode") or "unknown").lower()
        fake_run = execution_mode != "qwen-mt" or len(state.get("translation_providers") or []) != 1
        if fake_run and not args.debug_export:
            raise SystemExit("Release Gate blocked: non-provider or mixed translation evidence is never a formal release")
        if not ready and not args.debug_export:
            raise SystemExit("Release Gate blocked: release_status=%s" % status)
        products = [dict(item.get("source_record") or {}) for item in production_input.get("records", [])]
        translations = {str(item.get("asin") or "").upper(): item.get("fields") or {}
                        for item in release.get("records", [])}
        from ..export.excel import export_workbook
        from ..qa.field_closure import audit_field_closure
        from ..qa.run import blocking_issues
        blocked = blocking_issues(products)
        closure = audit_field_closure(products, details=load_evidence_json(args.details, default_details),
                                      rankings=load_evidence_json(args.rankings, default_rankings),
                                      html_dir=args.html_dir or None, run_dir=args.collection_run_dir or None,
                                      translations=translations)
        blocked += [(row.get("asin"), row.get("classification"), row.get("message"))
                    for row in closure.get("records", []) if row.get("severity") == "P1" and
                    row.get("classification") in {"PARSER_MISSED", "MAPPING_MISSED", "DERIVED_MISSING", "TRANSLATION_INCOMPLETE"}]
        if blocked and not args.debug_export:
            raise SystemExit("Release Gate blocked: QA/field-closure findings=%d" % len(blocked))
        out_path = args.out or str(run_dir / "release" / ("production_debug_unreleased.xlsx" if args.debug_export else "production_release.xlsx"))
        previous = None
        if args.prev_workbook:
            import openpyxl
            previous = openpyxl.load_workbook(args.prev_workbook)
        workbook = export_workbook(products, translations=translations,
                                   images_by_asin=load_images(args.images_dir, products),
                                   category_planning=load_category_planning(args.category_planning),
                                   prev_workbook=previous, out_path=out_path, profile=args.profile)
        debug_only = args.debug_export or fake_run
        save_json({"release_status": "FORCED_DEBUG" if debug_only else "READY",
                   "formal_release": not debug_only,
                   "label": "NOT_FOR_RELEASE" if debug_only else "PRODUCTION_RELEASE",
                   "blocked_count": len(blocked)}, str(Path(out_path).with_suffix(".release_status.json")))
        return
    raise SystemExit("unknown translation-production stage: %s" % stage)
