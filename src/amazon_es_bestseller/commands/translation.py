'Offline implementation of the staged production-translation command.\n\nThe command creates immutable, source-hash-bound artifacts.  It intentionally\nuses only ``FakeTranslationProvider``: this repository slice must never turn a\nCLI test or a production checkpoint resume into a provider/API request.\n'
from __future__ import annotations

import json
import os
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
from .common import _load_json, _load_translation_products, _save_json


class FakeTranslationProvider(TranslationProvider):
    'Deterministic test provider with no credentials, transport, or network.'

    name = 'fake'

    @property
    def model(self) -> str:
        return 'fake-offline-v1'

    def translate(self, text: str, *, asin: str, field: str,
                  source_language: str = 'es', target_language: str = 'zh-CN',
                  context: dict[str, Any] | None = None) -> ProviderResponse:
        value = str(text or '')
        replacements = (
            ('Bolsa t\xe9rmica', '\u4fdd\u6e29\u888b'), ('bolsa t\xe9rmica', '\u4fdd\u6e29\u888b'),
            ('Producto', '\u5546\u54c1'), ('producto', '\u5546\u54c1'),
            ('Capacidad', '\u5bb9\u91cf'), ('Color', '\u989c\u8272'), ('Rojo', '\u7ea2\u8272'),
            ('Voltaje', '\u7535\u538b'), ('Sin alcohol', '\u4e0d\u542b\u9152\u7cbe'), ('No', '\u5426'),
        )
        for source, target in replacements:
            value = value.replace(source, target)
        # A fake response must not pretend that arbitrary Spanish was reviewed.
        # Preserve numbers/unit evidence, but make the synthetic nature explicit.
        if re.search('[A-Za-z\xc1\xc9\xcd\xd3\xda\xdc\xd1\xe1\xe9\xed\xf3\xfa\xfc\xf1]', value):
            tokens = ' '.join(re.findall('\\d+(?:[.,]\\d+)?\\s*[A-Za-z%]+', value))
            placeholders = ' '.join(re.findall(r'__T\d{4}__', value))
            value = ('\u79bb\u7ebf\u6a21\u62df\u8bd1\u6587 ' + tokens + ' ' + placeholders).strip()
        return ProviderResponse(text=value, provider=self.name, model=self.model,
                                status='success', attempts=0,
                                raw={'offline': True, 'asin': asin, 'field': field})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _selected_records(records: list[dict[str, Any]], args: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    asin_filter: set[str] = set()
    if args.asin_list:
        value: Any = args.asin_list
        possible_path = Path(str(value))
        if possible_path.exists():
            value = json.loads(possible_path.read_text(encoding='utf-8'))
        asin_filter = set(normalize_asin_filter(value))
    selected = records
    if asin_filter:
        selected = [row for row in selected if str(row.get('asin') or '').strip().upper() in asin_filter]
    if args.category:
        selected = [row for row in selected if matches_category_filter(row, args.category)]
    selected = selected[max(0, int(args.offset or 0)):]
    if args.limit is not None:
        selected = selected[:max(0, int(args.limit))]
    selection = {'offset': args.offset, 'limit': args.limit,
                 'asin_filter': sorted(asin_filter), 'category': args.category,
                 'record_count': len(selected)}
    return selected, selection


def _service(run_dir: Path, config: Mapping[str, Any], args: Any) -> TranslationService:
    provider_mode = str(config.get('provider_mode') or 'fake').strip().lower()
    if provider_mode == 'qwen-mt':
        from ..translation.providers.qwen_mt import QwenMTProvider
        from ..translation.budget import BudgetLedger, BudgetedProvider, VerifiedPriceCard
        provider: TranslationProvider = QwenMTProvider(
            model=args.model or str(config.get('model') or 'qwen-mt-flash'),
            endpoint=config.get('endpoint'), protocol=config.get('protocol'),
            rate=float(args.rate if args.rate is not None else config.get('rate', 0.5)),
            # Retries must re-enter BudgetedProvider below so each transport
            # attempt is separately reserved and charged.  Do not keep hidden
            # provider-internal retry attempts beneath one reservation.
            max_retries=0,
        )
        if not provider.api_key:
            raise SystemExit('QWEN_CREDENTIAL_UNAVAILABLE: real translation not started')
        # A real provider is never allowed to run on an unpriced or
        # non-CNY configuration. The ledger survives resume and reserves the
        # worst permitted request before Qwen transport is entered.
        price_card = VerifiedPriceCard.from_mapping(
            config.get('pricing_verification'), provider=provider.name, model=provider.model,
        )
        ledger = BudgetLedger(
            run_dir / 'budget' / 'translation_budget_ledger.json',
            limit_cny=config.get('budget_cny', '5.00'), price_card=price_card,
            safety_buffer_cny=config.get('budget_safety_buffer_cny', '0.10'),
            max_input_tokens=int(config.get('budget_max_input_tokens', 12000)),
            max_output_tokens=int(config.get('budget_max_output_tokens', 2048)),
            prompt_overhead_tokens=int(config.get('budget_prompt_overhead_tokens', 1024)),
        )
        provider = BudgetedProvider(provider, ledger)
    elif provider_mode == 'fake':
        provider = FakeTranslationProvider()
    else:
        raise SystemExit('unsupported provider_mode: %s' % provider_mode)
    return TranslationService(
        provider, TranslationCache(run_dir / 'translations' / 'translation_cache.json'),
        schema_version=str(config.get('schema_version') or TRANSLATION_SCHEMA_VERSION),
        prompt_version=str(config.get('prompt_version') or 'amazon-es-retail-v2'),
        source_language=str(config.get('source_language') or 'es'),
        target_language=str(config.get('target_language') or 'zh-CN'),
    )


def _plan(service: TranslationService, records: list[dict[str, Any]], config: Mapping[str, Any],
          production_input: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
    result = service.translate_records(records, fields=config.get('fields') or None, dry_run=True)
    plan = dict(result['summary'])
    aliases = [str(item.get('name') or item.get('alias') or '')
               for item in config.get('providers', []) if isinstance(item, Mapping)]
    total = int(plan.get('estimated_api_requests', 0))
    plan.update({
        'unique_asins': len({str(row.get('asin') or '').upper() for row in records if row.get('asin')} ),
        'dataset_hash': (production_input.get('manifest') or {}).get('dataset_hash'),
        'prompt_version': config.get('prompt_version', 'amazon-es-retail-v2'),
        'schema_version': config.get('schema_version', TRANSLATION_SCHEMA_VERSION),
        'selection': dict(selection), 'provider': 'fake', 'offline': True,
        'pool': {'max_workers': int(config.get('max_workers', 1))},
        'estimated_provider_requests': {
            alias: total // len(aliases) + (1 if index < total % len(aliases) else 0)
            for index, alias in enumerate(aliases)
        } if aliases else {},
    })
    return plan


def _current_release_is_bound(state: Mapping[str, Any], production_input: Mapping[str, Any]) -> bool:
    if (state.get('input_manifest') or {}).get('dataset_hash') != (production_input.get('manifest') or {}).get('dataset_hash'):
        return False
    expected = {str(item.get('asin') or '').upper(): item.get('source_record_hash')
                for item in production_input.get('records', [])}
    actual_rows = list(state.get('records') or [])
    actual = {str(row.get('asin') or '').upper(): row for row in actual_rows if row.get('asin')}
    if not expected or set(actual) != set(expected):
        return False
    for item in production_input.get('records', []):
        asin = str(item.get('asin') or '').upper()
        row = actual.get(asin, {})
        if row.get('source_record_hash') != expected[asin]:
            return False
        expected_fields = item.get('fields') or {}
        actual_fields = {field.get('field'): field for field in row.get('fields', []) if isinstance(field, Mapping)}
        if set(actual_fields) != set(expected_fields):
            return False
        if any(actual_fields[name].get('source_hash') != source.get('source_hash')
               for name, source in expected_fields.items()):
            return False
    return True


def run_translation_production(args: Any, *, load_products: Callable[[str], list],
                               load_json: Callable[[str], Any], save_json: Callable[[Any, str], None],
                               load_evidence_json: Callable[[str, str], Any],
                               default_details: str, default_rankings: str,
                               load_images: Callable[[str, list], Mapping],
                               load_category_planning: Callable[[str], Any]) -> None:
    'Run one production stage; all writes stay under the explicit run directory.'
    run_dir = Path(args.run_dir or (Path('runtime') / 'translation_v2' / 'production' / args.run_id))
    stage = args.stage
    if stage == 'build-input':
        records = load_products(args.master)
        if not isinstance(records, list):
            raise SystemExit('production Master must be a JSON array or research CSV')
        result = build_production_input(records, source_run_id=args.source_run_id,
                                        source_schema_version=args.source_schema_version,
                                        translation_schema_version=TRANSLATION_SCHEMA_VERSION, run_id=args.run_id)
        save_json(result['manifest'], str(run_dir / 'input_manifest.json'))
        save_json(result, str(run_dir / 'translation_input.json'))
        return
    input_path = run_dir / 'translation_input.json'
    if not input_path.exists():
        raise SystemExit('missing production input: %s' % input_path)
    production_input = load_json(str(input_path))
    if not isinstance(production_input, Mapping) or not isinstance(production_input.get('records'), list):
        raise SystemExit('invalid production translation_input.json')
    if stage == 'preclean':
        result = audit_records(records_for_preclean(production_input))
        write_reports(result, run_dir / 'preclean')
        save_json({'manifest': production_input.get('manifest'), 'summary': result['summary']},
                  str(run_dir / 'preclean' / 'production_preclean_manifest.json'))
        return
    preclean_path = run_dir / 'preclean' / 'translation_input_records.json'
    if not preclean_path.exists():
        raise SystemExit('production-preclean required: %s' % preclean_path)
    wrapper = load_json(str(preclean_path))
    preclean_records = wrapper.get('records', []) if isinstance(wrapper, Mapping) else wrapper
    if stage in {'plan', 'translate'}:
        config = load_json(args.config) if args.config and Path(args.config).exists() else {}
        if not isinstance(config, Mapping):
            raise SystemExit('production config must be an object')
        records, selection = _selected_records(list(preclean_records), args)
        service = _service(run_dir, config, args)
        if stage == 'plan' or args.dry_run:
            plan = _plan(service, records, config, production_input, selection)
            save_json(plan, str(run_dir / 'plan' / 'translation_plan.json'))
            return
        if not args.yes:
            raise SystemExit('production-translate requires --yes (offline fake provider)')
        result = service.translate_records(records, fields=config.get('fields') or None)
        manifest = production_input.get('manifest') or {}
        batch_id = shard_batch_id(dataset_hash=str(manifest.get('dataset_hash') or ''), selection=selection,
                                  prompt_version=str(config.get('prompt_version') or 'amazon-es-retail-v2'),
                                  schema_version=str(config.get('schema_version') or TRANSLATION_SCHEMA_VERSION))
        provider_mode = str(config.get('provider_mode') or 'fake').lower()
        batch_id = shard_batch_id(dataset_hash=str(manifest.get('dataset_hash') or ''), selection=selection,
                                  prompt_version=str(config.get('prompt_version') or 'amazon-es-retail-v2'),
                                  schema_version=str(config.get('schema_version') or TRANSLATION_SCHEMA_VERSION),
                                  provider=service.provider.name, execution_mode=provider_mode)
        shard = {'run_id': args.run_id, 'batch_id': batch_id, 'created_at': _now(), 'selection': selection,
                 'input_dataset_hash': manifest.get('dataset_hash'), 'translation_schema_version': TRANSLATION_SCHEMA_VERSION,
                 'prompt_version': config.get('prompt_version', 'amazon-es-retail-v2'), 'provider': service.provider.name,
                 'execution_mode': provider_mode, 'records': result['records']}
        shard_dir = run_dir / 'translations' / 'shards'
        shard_path = shard_dir / (batch_id + '.json')
        existing = load_json(str(shard_path)) if shard_path.exists() else None
        if existing is None:
            save_json(shard, str(shard_path))
        elif not shard_immutable_equal(existing, shard):
            raise SystemExit('BATCH_ID_CONFLICT: immutable shard differs: %s' % batch_id)
        shards = [load_json(str(path)) for path in sorted(shard_dir.glob('batch_*.json'))]
        aggregate = merge_translation_shards(shards)
        save_json(aggregate, str(run_dir / 'translations' / 'translation_results.json'))
        save_json(result['qa_report'], str(run_dir / 'qa' / 'translation_qa.json'))
        save_json({'summary': result['summary'], 'manifest': manifest, 'batch_id': batch_id,
                   'aggregate_record_count': len(aggregate), 'provider': 'fake'},
                  str(run_dir / 'translations' / 'translation_run.json'))
        return
    if stage == 'promote':
        translations_path = run_dir / 'translations' / 'translation_results.json'
        if not translations_path.exists():
            raise SystemExit('production-translate required: %s' % translations_path)
        config = load_json(args.config) if args.config and Path(args.config).exists() else {}
        shards = [load_json(str(path)) for path in sorted((run_dir / 'translations' / 'shards').glob('batch_*.json'))]
        modes = {str(shard.get('execution_mode') or 'unknown').lower() for shard in shards}
        providers = {str(shard.get('provider') or 'unknown').lower() for shard in shards}
        execution_mode = next(iter(modes)) if len(modes) == 1 else 'mixed'
        state = build_production_state(production_input, preclean_records, load_json(str(translations_path)),
                                       qa_version=str(config.get('qa_version') or 'translation-qa-v1'),
                                       prompt_version=str(config.get('prompt_version') or 'amazon-es-retail-v2'))
        # Promotion trusts immutable translation evidence, never a later CLI
        # config.  Missing or mixed evidence is deliberately non-formal.
        state['translation_execution_mode'] = execution_mode
        state['translation_providers'] = sorted(providers)
        state['release_candidate']['translation_execution_mode'] = state['translation_execution_mode']
        save_json(state, str(run_dir / 'state' / 'translation_state.json'))
        save_json(state['release_candidate'], str(run_dir / 'release' / 'production_release_candidate.json'))
        save_json(state['repair_queue'], str(run_dir / 'repair' / 'repair_queue.json'))
        save_json(state['summary'], str(run_dir / 'state' / 'production_summary.json'))
        return
    if stage == 'export':
        if args.force:
            raise SystemExit('translation-production \u4e0d\u652f\u6301 --force; use --debug-export for NOT_FOR_RELEASE output')
        release_path = run_dir / 'release' / 'production_release_candidate.json'
        state_path = run_dir / 'state' / 'translation_state.json'
        if not release_path.exists() or not state_path.exists():
            raise SystemExit('production-promote required')
        release, state = load_json(str(release_path)), load_json(str(state_path))
        if not _current_release_is_bound(state, production_input):
            raise SystemExit('Release Gate blocked: stored READY failed source-hash revalidation')
        ready, status = release_gate(release)
        execution_mode = str(state.get('translation_execution_mode') or 'unknown').lower()
        fake_run = execution_mode != 'qwen-mt' or len(state.get('translation_providers') or []) != 1
        if fake_run and not args.debug_export:
            raise SystemExit('Release Gate blocked: non-provider or mixed translation evidence is never a formal release')
        if not ready and not args.debug_export:
            raise SystemExit('Release Gate blocked: release_status=%s' % status)
        products = [dict(item.get('source_record') or {}) for item in production_input.get('records', [])]
        translations = {str(item.get('asin') or '').upper(): item.get('fields') or {}
                        for item in release.get('records', [])}
        from ..export.excel import export_workbook
        from ..qa.field_closure import audit_field_closure
        from ..qa.run import blocking_issues
        blocked = blocking_issues(products)
        closure = audit_field_closure(products, details=load_evidence_json(args.details, default_details),
                                      rankings=load_evidence_json(args.rankings, default_rankings),
                                      html_dir=args.html_dir or None, run_dir=args.collection_run_dir or None,
                                      translations=translations)
        blocked += [(row.get('asin'), row.get('classification'), row.get('message'))
                    for row in closure.get('records', []) if row.get('severity') == 'P1' and
                    row.get('classification') in {'PARSER_MISSED', 'MAPPING_MISSED', 'DERIVED_MISSING', 'TRANSLATION_INCOMPLETE'}]
        if blocked and not args.debug_export:
            raise SystemExit('Release Gate blocked: QA/field-closure findings=%d' % len(blocked))
        out_path = args.out or str(run_dir / 'release' / ('production_debug_unreleased.xlsx' if args.debug_export else 'production_release.xlsx'))
        previous = None
        if args.prev_workbook:
            import openpyxl
            previous = openpyxl.load_workbook(args.prev_workbook)
        workbook = export_workbook(products, translations=translations,
                                   images_by_asin=load_images(args.images_dir, products),
                                   category_planning=load_category_planning(args.category_planning),
                                   prev_workbook=previous, out_path=out_path, profile=args.profile)
        debug_only = args.debug_export or fake_run
        save_json({'release_status': 'FORCED_DEBUG' if debug_only else 'READY',
                   'formal_release': not debug_only,
                   'label': 'NOT_FOR_RELEASE' if debug_only else 'PRODUCTION_RELEASE',
                   'blocked_count': len(blocked)}, str(Path(out_path).with_suffix('.release_status.json')))
        return
    raise SystemExit('unknown translation-production stage: %s' % stage)


def cmd_translate_ds(args) -> None:
    '\u6309 ASIN \u987a\u5e8f\u8c03\u7528 DS\uff0c\u8f93\u51fa ASIN \u2192 \u7ffb\u8bd1\u7ed3\u679c\u6620\u5c04\u3002'
    if args.offline:
        raise SystemExit('translate-ds \u9700\u8981\u8054\u7f51\uff0c\u4e0d\u80fd\u4e0e --offline \u540c\u7528')
    products = _load_json(args.products)
    if not isinstance(products, list):
        raise SystemExit('products JSON \u9876\u5c42\u5fc5\u987b\u662f\u6570\u7ec4: %s' % args.products)

    endpoint = args.endpoint or os.getenv('DEEPSEEK_ENDPOINT') or os.getenv('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com/chat/completions'
    model = args.model or os.getenv('DEEPSEEK_MODEL') or os.getenv('DS_MODEL') or 'deepseek-chat'
    print('translate-ds \u5373\u5c06\u8c03\u7528 DeepSeek API\uff1a%d \u4e2a ASIN\uff0cendpoint=%s\uff0cmodel=%s'
          % (len(products), endpoint, model))
    try:
        confirmation = input('\u8f93\u5165 YES \u786e\u8ba4\u5f00\u59cb\u8c03\u7528 API\uff0c\u5176\u4ed6\u8f93\u5165\u5c06\u53d6\u6d88\uff1a')
    except (EOFError, KeyboardInterrupt):
        raise SystemExit('\u672a\u786e\u8ba4\uff0c\u5df2\u53d6\u6d88 DS API \u8c03\u7528')
    if confirmation.strip().upper() != 'YES':
        raise SystemExit('\u672a\u786e\u8ba4\uff0c\u5df2\u53d6\u6d88 DS API \u8c03\u7528')

    from ..translation.ds import DeepSeekTranslator

    translator = DeepSeekTranslator(
        endpoint=args.endpoint or None,
        model=args.model or None,
        cache_path=args.cache or args.out,
        max_retries=args.max_retries,
        backoff_seconds=args.backoff_seconds,
        timeout=args.timeout,
    )
    output: dict[str, dict] = {}
    for product in products:
        if args.repair_partial:
            result = translator.translate_record(product, repair_partial=True)
        else:
            result = translator.translate_record(product)
        asin = str(result.get('asin') or product.get('asin') or '').strip().upper()
        if asin:
            output[asin] = result
        translator.save_cache()
    _save_json(output, args.out)
    success = sum(1 for r in output.values() if r.get('translation_status') == 'success')
    partial = sum(1 for r in output.values() if r.get('translation_status') == 'partial')
    failed = sum(1 for r in output.values() if r.get('translation_status') == 'failed')
    print('translate-ds \u5b8c\u6210\uff1a\u6210\u529f %d\u3001\u90e8\u5206 %d\u3001\u5931\u8d25 %d\u3001\u603b\u8ba1 %d \u2192 %s'
          % (success, partial, failed, len(output), args.out))


def cmd_translate(args) -> None:
    'Field-level Translation V2; dry-run is always offline and side-effect free.'
    products = _load_translation_products(args.products)
    if not isinstance(products, list):
        raise SystemExit('products JSON \u9876\u5c42\u5fc5\u987b\u662f\u6570\u7ec4: %s' % args.products)
    # Raw CSV/list input is admitted through the same deterministic Pre-Clean
    # gate as the standalone command.  Already prepared records are reused so
    # a rerun never mutates source evidence or repeats cleanup.
    if not all(isinstance(row, dict) and isinstance(row.get('fields'), dict)
               for row in products):
        from ..translation.preclean import audit_records
        # Keep already prepared rows intact when a batch is resumed from a
        # mixed source; only raw rows need the offline audit pass.
        prepared = []
        raw_indexes = []
        raw_rows = []
        for index, row in enumerate(products):
            if isinstance(row, dict) and isinstance(row.get('fields'), dict):
                prepared.append(row)
            else:
                prepared.append(None)
                raw_indexes.append(index)
                raw_rows.append(row)
        cleaned = audit_records(raw_rows)['translation_input_records']
        for index, row in zip(raw_indexes, cleaned):
            prepared[index] = row
        products = prepared
    from ..translation.cache import TranslationCache
    from ..translation.providers.qwen_mt import QwenMTProvider
    from ..translation.service import TranslationService

    config = {}
    if args.config:
        config = _load_json(args.config)
        if not isinstance(config, dict):
            raise SystemExit('translation config \u9876\u5c42\u5fc5\u987b\u662f\u5bf9\u8c61: %s' % args.config)
    provider_name = args.provider or config.get('provider', 'qwen-mt')
    if provider_name not in {'qwen-mt', 'qwen_mt'}:
        raise SystemExit('Translation V2 \u5f53\u524d\u53ea\u5141\u8bb8 provider=qwen-mt\uff1b\u65e7 DeepSeek \u8bf7\u7ee7\u7eed\u4f7f\u7528 translate-ds')
    run_context = None
    if config.get('run_context'):
        from ..translation.run_context import TranslationRunContext
        run_context = TranslationRunContext(config['run_context'], products_path=args.products,
                                             products=products, cache_path=args.cache)
    preflight = None
    if config.get('strict_provider_mapping'):
        from ..translation.pool import preflight_qwen_provider_pool
        preflight = preflight_qwen_provider_pool(config)
        if preflight['status'] != 'READY':
            if args.dry_run:
                _save_json({'status': 'PROVIDER_CONFIGURATION_BLOCKED', 'provider_preflight': preflight,
                            'total_records': len(products), 'dispatches': 0, 'http_attempts': 0}, args.out)
                print('translate dry-run: PROVIDER_CONFIGURATION_BLOCKED (0 dispatches)')
                return
            raise SystemExit('PROVIDER_CONFIGURATION_BLOCKED: %s' % preflight['missing'])
    model = args.model or config.get('model') or os.getenv('QWEN_MT_MODEL') or 'qwen-mt-flash'
    if run_context:
        run_context.validate_provider(name='qwen-mt', model=model,
            source_language=config.get('source_language', 'es'), target_language=config.get('target_language', 'zh-CN'))
        if preflight and any(row['model'] != model for row in preflight['providers']):
            raise ValueError('RUN_CONTEXT_PROVIDER_MODEL_MISMATCH')
    provider = QwenMTProvider(model=model,
                              api_key='OFFLINE_DRY_RUN_NO_CREDENTIAL' if preflight and args.dry_run else None,
                              endpoint=preflight['providers'][0]['endpoint'] if preflight else config.get('endpoint'),
                              protocol=config.get('protocol'),
                              timeout=float(config.get('timeout', config.get('timeout_seconds', 60))),
                              max_retries=int(config.get('max_retries', 2)),
                              backoff_seconds=float(config.get('backoff_seconds', 5.0)),
                              rate=float(args.rate if args.rate is not None
                                         else config.get('rate', 0.5)))
    cache = run_context.cache if run_context else TranslationCache(args.cache)
    if config.get('resume_admission'):
        from ..translation.resume_admission import ResumeAdmission
        if args.repair_partial or args.repair_failed:
            raise ValueError('RESUME_ADMISSION_REPAIR_FLAGS_FORBIDDEN')
        cache.resume_admission = ResumeAdmission.from_file(config['resume_admission'],
            cache_path=cache.path, products_path=args.products)
    fields = args.field or ([args.fields] if args.fields else None) or config.get('fields') or None
    if fields:
        fields = [item.strip() for value in fields for item in str(value).split(',') if item.strip()]
    if run_context:
        run_context.validate_options(fields=fields, offset=args.offset, limit=args.limit,
            repair_partial=args.repair_partial, repair_failed=args.repair_failed)
    service_kwargs = run_context.service_kwargs() if run_context else {
        'source_language': config.get('source_language', 'es'),
        'target_language': config.get('target_language', 'zh-CN')}
    service = TranslationService(provider, cache, **service_kwargs)
    parallel_requested = bool(getattr(args, 'parallel_providers', False) or
                              isinstance(config.get('providers'), list) and len(config['providers']) > 1)
    pool = None
    if parallel_requested and not (preflight and args.dry_run):
        from ..translation.pool import build_qwen_provider_pool
        pool_config = dict(config)
        pool_config.setdefault('max_workers', len(config.get('providers', [])) or 2)
        pool = build_qwen_provider_pool(pool_config)
    if args.dry_run:
        if pool is not None:
            result = service.translate_records_parallel(
                products, pool, fields=fields, offset=args.offset, limit=args.limit,
                repair_partial=args.repair_partial, repair_failed=args.repair_failed, dry_run=True)
        else:
            result = service.translate_records(products, fields=fields, offset=args.offset,
                                               limit=args.limit, repair_partial=args.repair_partial,
                                               repair_failed=args.repair_failed, dry_run=True)
        plan = result['summary']
        if run_context:
            run_context.apply_plan(plan)
        if preflight:
            plan['provider_preflight'] = preflight
            plan['pool'] = {'provider_count': len(preflight['providers']), 'max_workers': preflight['max_workers'],
                            'in_flight': 0, 'completed': 0, 'providers': {}}
            aliases = [row['alias'] for row in preflight['providers']]
            total_requests = int(plan.get('estimated_api_requests', 0))
            plan['estimated_provider_requests'] = {
                alias: total_requests // len(aliases) + (1 if index < total_requests % len(aliases) else 0)
                for index, alias in enumerate(aliases)}
        if pool is not None:
            plan['pool'] = result.get('pool', pool.snapshot())
            aliases = [str(item.get('name') or item.get('alias'))
                       for item in config.get('providers', [])]
            if aliases:
                total_requests = int(plan.get('estimated_api_requests', 0))
                plan['estimated_provider_requests'] = {
                    alias: total_requests // len(aliases) + (1 if index < total_requests % len(aliases) else 0)
                    for index, alias in enumerate(aliases)}
            print('Parallel workers = %d' % pool.max_workers)
            for alias, spec in ((item.get('name') or item.get('alias'), item)
                                for item in config.get('providers', [])):
                print('  Provider %s: model=%s rate=%s' %
                      (alias, spec.get('model', 'qwen-mt-flash'), spec.get('rate', 0.5)))
        _save_json(plan, args.out)
        print('translate dry-run%s\uff1aSKU %d\u3001\u5f85\u7ffb\u8bd1\u5b57\u6bb5 %d\u3001\u7f13\u5b58\u547d\u4e2d %d\u3001TM \u547d\u4e2d %d\u3001\u9884\u8ba1 API \u8bf7\u6c42 %d\u3001source_missing %d\u3001review_blocked %d\u3001rate=%.3g/s\uff08\u672a\u8c03\u7528 API\uff09\u2192 %s' %
              (' [parallel-providers]' if pool is not None else '',
               plan['total_records'], plan['total_fields'], plan['cache_hits'],
               plan['translation_memory_hits'], plan['estimated_api_requests'],
               plan['source_missing'], plan['review_blocked'], provider.rate, args.out))
        return
    if args.offline:
        raise SystemExit('translate \u5b9e\u9645 API \u8c03\u7528\u4e0d\u80fd\u4e0e --offline \u540c\u7528\uff1b\u53ef\u5148\u4f7f\u7528 --dry-run')
    plan = service.plan(products, fields=fields, offset=args.offset, limit=args.limit,
                        repair_partial=args.repair_partial, repair_failed=args.repair_failed)
    if run_context:
        run_context.apply_plan(plan)
    print('translate V2 \u5373\u5c06\u8c03\u7528 %s%s\uff1aSKU %d\u3001\u5f85\u7ffb\u8bd1\u5b57\u6bb5 %d\u3001\u7f13\u5b58\u547d\u4e2d %d\u3001TM \u547d\u4e2d %d\u3001\u9884\u8ba1 API \u8bf7\u6c42 %d\u3001source_missing %d\u3001review_blocked %d\u3001model=%s\u3001rate=%.3g/s' %
          (provider.name, ' [parallel-providers]' if pool is not None else '',
           plan['total_records'], plan['total_fields'], plan['cache_hits'],
           plan['translation_memory_hits'], plan['estimated_api_requests'],
           plan['source_missing'], plan['review_blocked'], model, provider.rate))
    if pool is not None:
        print('Parallel workers = %d' % pool.max_workers)
        for alias, spec in ((item.get('name') or item.get('alias'), item)
                            for item in config.get('providers', [])):
            print('  Provider %s: model=%s rate=%s' % (alias, spec.get('model', 'qwen-mt-flash'), spec.get('rate', 0.5)))
    if not args.yes:
        try:
            confirmation = input('\u8f93\u5165 YES \u786e\u8ba4\u5f00\u59cb\u8c03\u7528 API\uff0c\u5176\u4ed6\u8f93\u5165\u5c06\u53d6\u6d88\uff1a')
        except (EOFError, KeyboardInterrupt):
            raise SystemExit('\u672a\u786e\u8ba4\uff0c\u5df2\u53d6\u6d88 Translation V2 API \u8c03\u7528')
        if confirmation.strip().upper() != 'YES':
            raise SystemExit('\u672a\u786e\u8ba4\uff0c\u5df2\u53d6\u6d88 Translation V2 API \u8c03\u7528')
    if run_context:
        run_context.prepare_execution()
    if pool is not None:
        result = service.translate_records_parallel(
            products, pool, fields=fields, offset=args.offset, limit=args.limit,
            repair_partial=args.repair_partial, repair_failed=args.repair_failed)
    else:
        result = service.translate_records(products, fields=fields, offset=args.offset,
                                           limit=args.limit, repair_partial=args.repair_partial,
                                           repair_failed=args.repair_failed)
    if run_context:
        run_context.apply_result(result, products)
        result['summary']['execution_plan'] = plan
    _save_json(result['records'], args.out)
    qa_out = args.qa_out or str(Path(args.out).with_name('translation_qa.json'))
    _save_json(result['qa_report'], qa_out)
    summary_out = getattr(args, 'summary_out', '') or str(Path(args.out).with_name('translation_run.json'))
    _save_json({'summary': result.get('summary', {}), 'pool': result.get('pool'),
                'qa_report': result.get('qa_report', {}), 'provider': provider.name,
                'model': model, 'api_calls': result.get('pool', {}).get('providers', {})}, summary_out)
    if args.audit_out:
        audit = []
        for record in result['records'].values():
            for field, value in (record.get('fields') or {}).items():
                audit.append({'asin': record.get('asin'), 'field': field,
                              'source_field': value.get('field', field),
                              'target_field': value.get('target_field', field),
                              'source_hash': value.get('source_hash'),
                              'provider': value.get('provider'), 'model': value.get('model'),
                              'status': value.get('translation_status'),
                              'translation_status': value.get('translation_status'),
                              'qa_status': value.get('qa_status'),
                              'last_error': value.get('last_error')})
        Path(args.audit_out).parent.mkdir(parents=True, exist_ok=True)
        with Path(args.audit_out).open('w', encoding='utf-8') as handle:
            for row in audit:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    print('translate V2 \u5b8c\u6210\uff1a%s \u2192 %s\uff1bQA \u2192 %s\uff1b\u8fd0\u884c\u6458\u8981 \u2192 %s' % (result['summary'], args.out, qa_out, summary_out))


def cmd_dictionary_only(args) -> None:
    'Profile source data and run the conservative dictionary-only pipeline.\n\n    This command intentionally does not construct Qwen/DeepSeek clients.  It\n    is safe to run against the full internal-research dataset and writes all\n    reports to an independent directory.\n    '
    from ..translation.dictionary_only import load_records, run_dictionary_only, write_reports
    from ..translation.dictionary_service import DictionaryService

    products = load_records(args.products)
    input_asins = [str(row.get('asin') or row.get('ASIN') or '').strip().upper() for row in products]
    present_asins = [asin for asin in input_asins if asin]
    print('Input file: %s' % args.products)
    print('Row count: %d' % len(products))
    print('Unique ASIN: %d' % len(set(present_asins)))
    print('Mode: dictionary-only')
    print('Qwen API: disabled')
    service = DictionaryService()
    result = run_dictionary_only(products, service=service, top_n=args.top_n)
    paths = write_reports(result, args.out, service=service)
    summary = result['summary']
    print('dictionary-only \u5b8c\u6210\uff1aSKU %d\u3001\u552f\u4e00 ASIN %d\u3001\u603b\u5904\u7406\u5355\u5143 %d\u3001\u5b57\u5178 %d\u3001\u89c4\u5219 %d\u3001\u4fdd\u62a4/\u4fdd\u7559 %d\u3001\u672a\u89e3\u51b3 %d\u3001Qwen API 0 \u2192 %s' %
          (summary['total_skus'], summary['unique_asins'], summary['total_units'],
           summary['resolved_by_dictionary'], summary['resolved_by_rules'],
           summary['source_preserved'] + summary['protected'],
           summary['remaining_for_qwen'], args.out))
    for name, path in paths.items():
        print('  %s -> %s' % (name, path))


def cmd_preclean(args) -> None:
    'Fully offline Translation V2 input preparation and audit.'
    from ..translation.preclean import run_preclean, write_reports
    result = run_preclean(args.products)
    paths = write_reports(result, args.out_dir)
    summary = result['summary']
    print('Input file: %s' % args.products)
    print('Row count: %d' % summary['input_rows'])
    print('Unique ASIN: %d' % summary['unique_asins'])
    print('Mode: preclean (offline)')
    print('Qwen API: disabled')
    print('Pre-Clean \u5b8c\u6210\uff1a\u72b6\u6001=%s\u3001CLEAN/NORMALIZED=%d\u3001review_queue=%d\u3001cross_field=%d\u3001identity=%d \u2192 %s' % (
        summary['final_state'],
        sum(summary['status_counts'].get(key, 0) for key in ('CLEAN', 'NORMALIZED')),
        summary['review_queue_count'], summary['cross_field_issue_count'],
        summary['identity_count'], args.out_dir))
    for name, path in paths.items():
        print('  %s -> %s' % (name, path))
