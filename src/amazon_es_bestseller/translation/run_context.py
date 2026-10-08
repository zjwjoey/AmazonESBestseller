"""Hash-bound, run-scoped historical cache and independently admitted references."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..orchestration.translation_batch import file_hash, load_source_candidate
from .cache import TranslationCache
from .legacy_reference import _hash_json, admit_legacy_run_reference_cache
from .preclean import _raw_hash, _source_value, _text, parse_bullets, parse_details
from .production import build_production_input, records_for_preclean
from .qa import build_qa_report
from .schemas import TRANSLATION_SCHEMA_VERSION
from .service import TranslationService


def read_bound(reference: dict[str, Any]) -> Any:
    path = Path(reference['path'])
    if file_hash(path) != reference.get('sha256'):
        raise ValueError('RUN_CONTEXT_ARTIFACT_HASH_MISMATCH:' + str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def build_run_cache_snapshot(cache_references: list[dict[str, Any]], records: list[dict[str, Any]],
                             service: TranslationService, output_path: str | Path) -> dict[str, Any]:
    """Copy relevant evidence without loading/writing any original cache.

    Conflicting candidates and uncertain attempts block every namespace for
    their stable identity. No last-cache-wins translation is manufactured.
    """
    path = Path(output_path)
    if path.exists():
        raise FileExistsError('RUN_CONTEXT_SNAPSHOT_ALREADY_EXISTS')
    payload, conflicts = _snapshot_payload(cache_references, records, service)
    snapshot = TranslationCache(path)
    for namespace in ('entries', 'results', 'memory', 'memory_results'):
        setattr(snapshot, namespace, payload[namespace])
    snapshot.save()
    return {'path': str(path.resolve()), 'sha256': file_hash(path),
            'counts': {namespace: len(payload[namespace]) for namespace in
                       ('entries', 'results', 'memory', 'memory_results')},
            'conflict_or_pending_identities': conflicts}


def _snapshot_payload(cache_references: list[dict[str, Any]], records: list[dict[str, Any]],
                      service: TranslationService) -> tuple[dict[str, Any], int]:
    selected = {row['asin'] for row in records}
    wanted = set()
    for row in records:
        for source, _, text in service.selected_fields(row):
            items = service._structured_items(source, service._prepared_value(row, source))
            if items is None:
                wanted.add('|'.join(service._memory_key(text, source).split('|')[:6]))
            else:
                for label, value in items:
                    wanted.add('|'.join(service._memory_key(value, source, label=label).split('|')[:6]))
    groups: dict[str, dict[str, list[dict[str, Any]]]] = {
        namespace: defaultdict(list) for namespace in ('entries', 'results', 'memory', 'memory_results')}
    for reference in cache_references:
        data = read_bound(reference)
        if not isinstance(data, dict) or not isinstance(data.get('entries'), dict):
            raise ValueError('RUN_CONTEXT_HISTORICAL_CACHE_FORMAT_INVALID')
        for namespace in groups:
            for key, value in (data.get(namespace) or {}).items():
                if not isinstance(value, dict):
                    raise ValueError('RUN_CONTEXT_HISTORICAL_CACHE_ENVELOPE_INVALID')
                relevant = ('|'.join(key.split('|')[:6]) in wanted if namespace.startswith('memory')
                            else value.get('asin') in selected)
                if relevant:
                    groups[namespace][key].append(deepcopy(value))
    identities: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for namespace, entries in groups.items():
        for key, values in entries.items():
            identity = (('memory', '|'.join(key.split('|')[:6])) if namespace.startswith('memory') else
                        ('field', TranslationCache.result_key(values[0].get('asin'), values[0].get('field'),
                                                             values[0].get('source_hash'))))
            identities[identity].extend(values)
    conflicts = {}
    for identity, values in identities.items():
        candidates = {(_hash_json(value.get('candidate_text', value.get('translated_text', ''))),
                       value.get('translation_status'), value.get('qa_status')) for value in values}
        if len(candidates) > 1 or any(value.get('translation_status') == 'pending' for value in values):
            conflicts[identity] = values
    payload: dict[str, Any] = {'cache_version': TranslationCache.VERSION}
    for namespace, entries in groups.items():
        destination = payload[namespace] = {}
        for key, values in entries.items():
            identity = (('memory', '|'.join(key.split('|')[:6])) if namespace.startswith('memory') else
                        ('field', TranslationCache.result_key(values[0].get('asin'), values[0].get('field'),
                                                             values[0].get('source_hash'))))
            value = deepcopy(values[0])
            if identity in conflicts:
                value.update({'translation_status': 'pending', 'qa_status': 'review_required',
                    'translated_text': '', 'last_error': 'HISTORICAL_CACHE_CONFLICT_OR_PENDING',
                    'historical_candidates': conflicts[identity]})
            destination[key] = value
    return payload, len(conflicts)


class TranslationRunContext:
    def __init__(self, reference: dict[str, Any], *, products_path: str | Path,
                 products: list[dict[str, Any]], cache_path: str | Path):
        self.reference = reference
        self.manifest = manifest = read_bound(reference)
        if manifest.get('schema_version') != 'translation-run-context-v1':
            raise ValueError('RUN_CONTEXT_SCHEMA_INVALID')
        bound_products = read_bound(manifest['products'])
        bound_products = bound_products.get('records') if isinstance(bound_products, dict) else bound_products
        if (Path(products_path).resolve() != Path(manifest['products']['path']).resolve()
                or _hash_json(products) != _hash_json(bound_products)):
            raise ValueError('RUN_CONTEXT_PRODUCTS_MISMATCH')
        selected = sorted(row['asin'] for row in products)
        if not selected or len(set(selected)) != len(selected) or selected != manifest['selected_asins']:
            raise ValueError('RUN_CONTEXT_ASIN_SCOPE_MISMATCH')
        self.run_cache_path = Path(manifest['run_cache_path'])
        if Path(cache_path).resolve() != self.run_cache_path.resolve():
            raise ValueError('RUN_CONTEXT_CACHE_PATH_MISMATCH')
        self.snapshot = read_bound(manifest['snapshot'])
        for item in manifest['historical_caches']:
            read_bound(item)
        protected_paths = [manifest[key]['path'] for key in ('products', 'snapshot', 'reference_overlay')]
        protected_paths += [item['path'] for item in manifest['historical_caches']]
        if self.run_cache_path.resolve() in {Path(path).resolve() for path in protected_paths}:
            raise ValueError('RUN_CONTEXT_CACHE_MUST_BE_INDEPENDENT')
        source_manifest = read_bound(manifest['source_manifest'])
        source_path = Path(manifest['source_manifest']['path']).parent / 'spanish_master_5480.json'
        if file_hash(source_path) != source_manifest['artifacts'].get(source_path.name):
            raise ValueError('RUN_CONTEXT_SOURCE_MASTER_HASH_MISMATCH')
        source = json.loads(source_path.read_text(encoding='utf-8'))
        admission = admit_legacy_run_reference_cache(read_bound(manifest['legacy_candidates']),
            source['source_gate'], read_bound(manifest['legacy_policy']),
            output_path=Path(reference['path']).with_name('NEVER_WRITTEN_reference_validation.json'), dry_run=True,
            source_candidate_manifest_path=manifest['source_manifest']['path'],
            source_candidate_manifest_hash=manifest['source_manifest']['sha256'],
            available_asins=manifest['available_asins'], selected_asins=selected)
        overlay = read_bound(manifest['reference_overlay'])
        if (_hash_json(overlay['authority']) != _hash_json(admission['authority'])
                or _hash_json(overlay['reference_envelopes']) != _hash_json(admission['reference_envelopes'])):
            raise ValueError('RUN_CONTEXT_REFERENCE_ADMISSION_MISMATCH')
        loaded = load_source_candidate(manifest['source_manifest']['path'],
            manifest_hash=manifest['source_manifest']['sha256'], available_asins=manifest['available_asins'])
        scoped = [row for row in loaded['records'] if row['asin'] in set(selected)]
        by_asin = {row['asin']: row for row in records_for_preclean(build_production_input(scoped))}
        for row in products:
            for field, raw in row['raw_fields'].items():
                value = _source_value(by_asin[row['asin']], field)
                source_text = (parse_details(value)['source_text'] if field == 'product_details' else
                               parse_bullets(value)['source_text'] if field == 'feature_bullets' else _text(value))
                if (raw['source_hash'] != _raw_hash(value)
                        or row['fields'][field]['source_text'] != source_text):
                    raise ValueError('RUN_CONTEXT_RAW_FIELD_HASH_MISMATCH')
        self.references = deepcopy(admission['reference_envelopes'])
        prepared = {row['asin']: row['fields'] for row in products}
        for envelope in self.references:
            field = prepared[envelope['asin']][envelope['field']]
            if field.get('translate_allowed'):
                raise ValueError('RUN_CONTEXT_REFERENCE_MUST_BE_PROVIDER_MASKED')
        contract = manifest['service_contract']
        if contract['schema_version'] != TRANSLATION_SCHEMA_VERSION:
            raise ValueError('RUN_CONTEXT_TRANSLATION_SCHEMA_MISMATCH')
        if not manifest['historical_caches']:
            raise ValueError('RUN_CONTEXT_HISTORICAL_CACHE_BINDING_MISSING')
        identity = manifest['provider_identity']
        expected_service = TranslationService(type('SnapshotProvider', (), identity)(),
            TranslationCache(manifest['snapshot']['path']), **contract)
        expected_snapshot, _ = _snapshot_payload(manifest['historical_caches'], products, expected_service)
        if _hash_json(expected_snapshot) != _hash_json(self.snapshot):
            raise ValueError('RUN_CONTEXT_SNAPSHOT_ORIGIN_MISMATCH')
        self.marker = self.run_cache_path.with_name(self.run_cache_path.name + '.run-context.json')
        if self.run_cache_path.exists():
            self._verify_marker()
            # Read before TranslationCache.load can recover/rename malformed data.
            json.loads(self.run_cache_path.read_text(encoding='utf-8'))
            self.cache = TranslationCache(self.run_cache_path)
        else:
            self.cache = TranslationCache(manifest['snapshot']['path'])
            self.cache.path = self.run_cache_path

    def _verify_marker(self) -> None:
        if not self.marker.exists() or json.loads(self.marker.read_text(encoding='utf-8')) != self.reference:
            raise ValueError('RUN_CONTEXT_RESUME_BINDING_MISMATCH')

    def service_kwargs(self) -> dict[str, Any]:
        return dict(self.manifest['service_contract'])

    def validate_provider(self, *, name: str, model: str, source_language: str, target_language: str) -> None:
        if ({'name': name, 'model': model} != self.manifest['provider_identity']
                or source_language != self.manifest['service_contract'].get('source_language', 'es')
                or target_language != self.manifest['service_contract'].get('target_language', 'zh-CN')):
            raise ValueError('RUN_CONTEXT_PROVIDER_OR_LANGUAGE_MISMATCH')

    def validate_options(self, *, fields: Any, offset: int, limit: Any,
                         repair_partial: bool, repair_failed: bool) -> None:
        if (fields != self.manifest['fields'] or offset or limit is not None or repair_partial or repair_failed):
            raise ValueError('RUN_CONTEXT_EXECUTION_OPTIONS_MISMATCH')

    def prepare_execution(self) -> None:
        if not self.run_cache_path.exists():
            self.run_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.run_cache_path.open('x', encoding='utf-8') as handle:
                json.dump(self.snapshot, handle, ensure_ascii=False)
            with self.marker.open('x', encoding='utf-8') as handle:
                json.dump(self.reference, handle)
        self._verify_marker()

    def apply_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        plan['provider_masked_review_blocked_including_references'] = plan['review_blocked']
        plan['review_blocked'] -= len(self.references)
        plan['resolved_reference_fields'] = len(self.references)
        plan['reference_fields'] = deepcopy(self.references)
        plan['run_context'] = self.reference
        return plan

    def apply_result(self, result: dict[str, Any], products: list[dict[str, Any]]) -> None:
        outputs = result['records']
        for envelope in self.references:
            record = outputs[envelope['asin']]
            target = envelope['target_field']
            record['fields'][target] = deepcopy(envelope)
            record[target] = envelope['translated_text']
        for row in products:
            record = outputs[row['asin']]
            for field, prepared in row['fields'].items():
                if prepared.get('translate_allowed') or not prepared.get('source_text'):
                    continue
                target = self._target(field)
                if target not in record['fields']:
                    record['fields'][target] = {'asin': row['asin'], 'field': field, 'target_field': target,
                        'source_text': prepared['source_text'], 'translated_text': '',
                        'translation_status': 'pending', 'qa_status': 'review_required',
                        'resolution_source': 'preclean_or_prior_attempt_hold', 'attempt_count': 0}
            statuses = [value['translation_status'] for value in record['fields'].values()]
            record['translation_status'] = ('success' if all(value in {'success', 'cached', 'source_missing'}
                for value in statuses) else 'partial' if any(value in {'success', 'cached'} for value in statuses)
                else 'pending')
        result['summary'] = {'total': len(outputs), **dict(Counter(
            record['translation_status'] for record in outputs.values())),
            'resolved_reference_fields': len(self.references), 'run_context': self.reference}
        result['qa_report'] = build_qa_report(outputs.values())

    @staticmethod
    def _target(field: str) -> str:
        from .production_contract import target_field_for
        return target_field_for(field)
