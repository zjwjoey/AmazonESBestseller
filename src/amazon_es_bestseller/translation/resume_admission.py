"""Finite, evidence-bound admission for durably proven unsent semantic claims."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ResumeAdmission:
    def __init__(self, identity, claim_fingerprints, excluded):
        self.identity = identity
        self.claim_fingerprints = claim_fingerprints
        self.excluded = excluded

    @staticmethod
    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    @classmethod
    def from_file(cls, binding, *, cache_path, products_path=None):
        def verified(item):
            path = Path(item['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('RESUME_ADMISSION_HASH_MISMATCH')
            return path

        path = verified(binding)
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 'unattempted-resume-v1':
            raise ValueError('RESUME_ADMISSION_VERSION')
        files = {name: verified(data['bindings'][name]) for name in
                 ('ledger', 'cache', 'products', 'source_config', 'source_manifest')}
        if files['cache'].resolve() != Path(cache_path).resolve():
            raise ValueError('RESUME_ADMISSION_CACHE_PATH')
        if products_path and files['products'].resolve() != Path(products_path).resolve():
            raise ValueError('RESUME_ADMISSION_PRODUCTS_PATH')
        events = [json.loads(line) for line in files['ledger'].read_text(encoding='utf-8').splitlines()]
        if not events or events[-1].get('event') != 'CANARY_PROCESS_FINISHED' or events[-1].get('exit_code') != 0:
            raise ValueError('RESUME_ADMISSION_UNCLOSED_LEDGER')
        groups = {}
        for index, event in enumerate(events):
            kind = event['event']
            key = event.get('canonical_dispatch_key')
            if key and kind in {'DURABLE_CLAIM_SAVED', 'PROVIDER_ENTRY', 'HTTP_ATTEMPT_BEFORE_POST',
                                'HTTP_RESULT', 'PROVIDER_RESULT'}:
                group = groups.setdefault(kind, {})
                if key in group:
                    raise ValueError('RESUME_ADMISSION_DUPLICATE_EVENT')
                group[key] = index
        claimed = groups.get('DURABLE_CLAIM_SAVED', {})
        entered = groups.get('PROVIDER_ENTRY', {})
        sent = groups.get('HTTP_ATTEMPT_BEFORE_POST', {})
        if not set(sent) <= set(entered) <= set(claimed):
            raise ValueError('RESUME_ADMISSION_EVENT_CHAIN')
        for key in entered:
            positions = [group[key] for group in (claimed, entered, sent) if key in group]
            if positions != sorted(positions):
                raise ValueError('RESUME_ADMISSION_EVENT_ORDER')
        # Any provider entry, including an unknown send outcome, is excluded.
        excluded = {'|'.join(key.split('|')[:6]) for key in entered}
        cache = json.loads(files['cache'].read_text(encoding='utf-8'))
        claim_ids = {}
        for key in set(claimed) - set(entered):
            current = cache.get('memory', {}).get(key, {})
            prior = cache.get('memory_results', {}).get('|'.join(key.split('|')[:6]), {})
            # Older settlement payloads dropped claim_id. The unique fsynced
            # claim event plus the hash-bound full pending envelope is the
            # evidence boundary; never infer unsent from pending status alone.
            if (current.get('translation_status') != 'pending'
                    or prior.get('translation_status', 'pending') != 'pending'):
                raise ValueError('RESUME_ADMISSION_CACHE_STATE')
            claim_ids[key] = cls.fingerprint(current)
        return cls(binding['sha256'], claim_ids, excluded)

    def allows(self, key, current, prior=None):
        return bool(key in self.claim_fingerprints and '|'.join(key.split('|')[:6]) not in self.excluded
                    and current and current.get('translation_status') == 'pending'
                    and self.fingerprint(current) == self.claim_fingerprints[key]
                    and (not prior or prior.get('translation_status') == 'pending'))
