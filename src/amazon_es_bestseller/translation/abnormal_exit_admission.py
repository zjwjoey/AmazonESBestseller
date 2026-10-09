"""Independent abnormal-exit evidence contract; normal v1 admission is unchanged.

Hash bindings authenticate the reviewed evidence set, not its author. The bound
launch admission must belong to the already reviewed fsynced audit runner.
Simulation objects cannot claim work and cannot be passed to a live runner.
Exit observations must come from the owner's retained process handles; this
validator cannot retrospectively recover an exit code from a missing PID.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path

from .resume_admission import ResumeAdmission


def _stable(key):
    if not isinstance(key, str) or len(key.split('|')) < 6:
        raise ValueError('ABNORMAL_KEY_INVALID')
    return '|'.join(key.split('|')[:6])


def _verified(binding, kind='HASH'):
    try:
        path = Path(binding['path'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding['sha256']:
            raise ValueError('ABNORMAL_%s_MISMATCH' % kind)
        return path, raw
    except (KeyError, OSError, TypeError) as exc:
        raise ValueError('ABNORMAL_%s_MISSING' % kind) from exc


def _json(binding, kind='HASH'):
    path, raw = _verified(binding, kind)
    try:
        return path, json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('ABNORMAL_%s_INVALID_JSON' % kind) from exc


def _pid_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # nonexistent PID, not access denied
                return False
            raise ValueError('ABNORMAL_PROCESS_STATE_UNPROVEN')
        kernel.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise ValueError('ABNORMAL_PROCESS_STATE_UNPROVEN') from exc


@contextmanager
def _released_lock(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 1:
        raise ValueError('ABNORMAL_LOCK_EVIDENCE_MISSING')
    with path.open('r+b') as handle:  # existing file only; no byte is modified
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        except OSError as exc:
            raise ValueError('ABNORMAL_LOCK_HELD_OR_UNPROVEN') from exc
        finally:
            if acquired:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _quiescent(data, cache_path):
    pids = data.get('source_pids')
    if (not isinstance(pids, list) or not pids or len(set(pids)) != len(pids)
            or any(type(pid) is not int or pid <= 0 for pid in pids)):
        raise ValueError('ABNORMAL_PROCESS_IDENTITY_INVALID')
    if any(_pid_alive(pid) for pid in pids):
        raise ValueError('ABNORMAL_PROCESS_ALIVE')
    if Path(data['claim_lock']).resolve() != Path(str(cache_path) + '.claim.lock').resolve():
        raise ValueError('ABNORMAL_CLAIM_LOCK_PATH')
    with ExitStack() as stack:
        stack.enter_context(_released_lock(data['source_lock']))
        stack.enter_context(_released_lock(data['claim_lock']))
        if any(_pid_alive(pid) for pid in pids):
            raise ValueError('ABNORMAL_PROCESS_ALIVE')
        yield


class _ReadOnlyPlanningView:
    def __init__(self, admission):
        self.admission = admission
        self.identity = 'SIMULATION_ONLY:' + admission.identity
        self.excluded = admission.excluded

    def allows(self, key, current, prior=None):
        return self.admission._allows_snapshot(key, current, prior)


class AbnormalExitAdmission:
    """Evidence admission only; it never grants permission to launch a process."""
    launch_authorized = False

    @classmethod
    def from_file(cls, binding, *, cache_path, products_path=None):
        _, data = _json(binding)
        if data.get('version') != 'abnormal-exit-resume-v2':
            raise ValueError('ABNORMAL_VERSION')
        mode = data.get('mode')
        if mode not in {'EVIDENCE_ONLY', 'SIMULATION_ONLY'}:
            raise ValueError('ABNORMAL_MODE')
        if data.get('audited_send_order') != 'claim-fsynced-entry-fsynced-send-fsynced-before-post-v1':
            raise ValueError('ABNORMAL_AUDITED_SEND_ORDER_UNPROVEN')
        if not isinstance(data.get('bindings'), dict):
            raise ValueError('ABNORMAL_BINDINGS')
        context = _quiescent(data, cache_path) if mode == 'EVIDENCE_ONLY' else nullcontext()
        with context:
            return cls._load(binding, data, cache_path, products_path)

    @classmethod
    def _load(cls, binding, data, cache_path, products_path):
        bindings = data['bindings']
        required = ('cache', 'products', 'source_config', 'source_manifest', 'plan',
                    'launch_admission', 'old_attempts', 'exit_observation', 'baseline_cache', 'source_process_record')
        loaded = {name: _json(bindings[name]) for name in required}
        _verified(bindings['source_runner'])
        _, raw_ledger = _verified(bindings['ledger'])
        try:
            events = [json.loads(line) for line in raw_ledger.decode('utf-8').splitlines()]
            if not events:
                raise ValueError('empty ledger')
        except (UnicodeError, ValueError) as exc:
            raise ValueError('ABNORMAL_LEDGER_INVALID') from exc
        cache = loaded['cache'][1]
        if (not isinstance(cache, dict) or any(not isinstance(cache.get(n), dict)
                for n in ('entries', 'results', 'memory', 'memory_results'))):
            raise ValueError('ABNORMAL_CACHE_INVALID_NO_EMPTY_FALLBACK')
        for namespace in ('entries', 'results', 'memory', 'memory_results'):
            if any(not isinstance(value, dict) for value in cache[namespace].values()):
                raise ValueError('ABNORMAL_CACHE_INVALID_RECORD')
        manifest = loaded['source_manifest'][1]
        launch = loaded['launch_admission'][1]
        if data.get('source_identity') != bindings['launch_admission']['sha256']:
            raise ValueError('ABNORMAL_SOURCE_IDENTITY_BINDING')
        process_record = loaded['source_process_record'][1]
        expected_pids = {process_record.get(name) for name in ('pid', 'launcher_pid')
                         if process_record.get(name) is not None}
        if (expected_pids != set(data['source_pids']) or process_record.get('code_sha') != launch.get('code_sha')
                or process_record.get('runner', {}).get('sha256') != bindings['source_runner']['sha256']):
            raise ValueError('ABNORMAL_SOURCE_PROCESS_BINDING')
        expected_lock = Path(launch['runner']['path']).parent / 'process_startup.lock'
        if Path(data['source_lock']).resolve() != expected_lock.resolve():
            raise ValueError('ABNORMAL_SOURCE_LOCK_PATH')
        _, authority = _json(manifest['extension_authorization'])
        if authority['parent_cache_snapshot']['sha256'] != bindings['baseline_cache']['sha256']:
            raise ValueError('ABNORMAL_BASELINE_SOURCE_BINDING')
        baseline = loaded['baseline_cache'][1]
        for namespace in ('entries', 'results', 'memory', 'memory_results'):
            if not isinstance(baseline.get(namespace), dict):
                raise ValueError('ABNORMAL_BASELINE_CACHE_INVALID')
            for key, value in baseline[namespace].items():
                if cache[namespace].get(key) != value:
                    raise ValueError('ABNORMAL_PRIOR_RAW_CACHE_CHANGED_OR_LOST')
        for name, launch_name in (('source_config', 'configuration'), ('source_manifest', 'manifest'),
                                  ('source_runner', 'runner')):
            if launch[launch_name]['sha256'] != bindings[name]['sha256']:
                raise ValueError('ABNORMAL_SOURCE_LAUNCH_BINDING')
        if launch['plan_file_hash'] != bindings['plan']['sha256']:
            raise ValueError('ABNORMAL_PLAN_LAUNCH_BINDING')
        if manifest['products']['sha256'] != bindings['products']['sha256']:
            raise ValueError('ABNORMAL_PRODUCTS_SOURCE_BINDING')
        if products_path and loaded['products'][0].resolve() != Path(products_path).resolve():
            raise ValueError('ABNORMAL_PRODUCTS_PATH')
        if Path(manifest['run_cache_path']).resolve() != Path(data['source_cache_path']).resolve():
            raise ValueError('ABNORMAL_SOURCE_CACHE_PATH')
        if data['mode'] == 'EVIDENCE_ONLY':
            if Path(cache_path).resolve() != Path(data['source_cache_path']).resolve():
                raise ValueError('ABNORMAL_WORKING_CACHE_PATH')
            if hashlib.sha256(Path(cache_path).read_bytes()).hexdigest() != bindings['cache']['sha256']:
                raise ValueError('ABNORMAL_WORKING_CACHE_HASH')
            exit_record = loaded['exit_observation'][1]
            if (exit_record.get('source_identity') != data['source_identity']
                    or set(exit_record.get('observed_pids', [])) != set(data['source_pids'])):
                raise ValueError('ABNORMAL_EXIT_IDENTITY_UNPROVEN')
            codes = exit_record.get('exit_codes', {})
            if any(type(codes.get(str(pid))) is not int or codes[str(pid)] == 259
                   for pid in data['source_pids']):
                raise ValueError('ABNORMAL_EXIT_NOT_OBSERVED')
            try:
                if datetime.fromisoformat(exit_record['observed_at']).tzinfo is None:
                    raise ValueError('timezone required')
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError('ABNORMAL_EXIT_TIME_INVALID') from exc
        keys = loaded['plan'][1]['dispatch_keys']
        if (not isinstance(keys, list) or len(set(keys)) != len(keys)
                or len({_stable(key) for key in keys}) != len(keys)
                or launch['maximum_new_unique_http_keys'] != len(keys)):
            raise ValueError('ABNORMAL_PLAN_SCOPE')
        plan = set(keys)
        groups = {name: {} for name in ('DURABLE_CLAIM_SAVED', 'PROVIDER_ENTRY',
                                       'HTTP_ATTEMPT_BEFORE_POST', 'HTTP_RESULT')}
        for position, event in enumerate(events):
            if not isinstance(event, dict) or not isinstance(event.get('event'), str):
                raise ValueError('ABNORMAL_LEDGER_INVALID')
            kind, key = event['event'], event.get('canonical_dispatch_key')
            if kind in groups:
                if key not in plan or key in groups[kind]:
                    raise ValueError('ABNORMAL_LEDGER_SCOPE_OR_DUPLICATE')
                groups[kind][key] = (position, event)
        claims, entries, sent, responses = groups.values()
        if not set(responses) <= set(sent) <= set(entries) <= set(claims):
            raise ValueError('ABNORMAL_LEDGER_CHAIN')
        for key in claims:
            positions = [group[key][0] for group in groups.values() if key in group]
            if positions != sorted(positions):
                raise ValueError('ABNORMAL_LEDGER_ORDER')
        inventory = {}
        raw_footprints = set()
        if data['mode'] == 'EVIDENCE_ONLY':
            response_dir = Path(launch['runner']['path']).parent / 'responses'
            if any(response_dir.glob('*.tmp')):
                raise ValueError('ABNORMAL_RESPONSE_TEMP_UNCERTAIN')
            actual = {path.resolve() for path in response_dir.glob('*.json')}
            declared = {Path(item['source_path']).resolve() for item in data.get('responses', [])}
            if actual != declared:
                raise ValueError('ABNORMAL_RESPONSE_INVENTORY')
        for item in data.get('responses', []):
            _, raw = _json(item['file'], 'RESPONSE')
            if data['mode'] == 'EVIDENCE_ONLY':
                _verified({'path': item['source_path'], 'sha256': item['file']['sha256']},
                          'RESPONSE_SOURCE_HASH')
            key = raw.get('canonical_dispatch_key')
            if key not in plan or key in inventory:
                raise ValueError('ABNORMAL_RESPONSE_SCOPE_OR_DUPLICATE')
            inventory[key] = (item, raw)
            raw_footprints.add(_stable(key))
        for key, (_, event) in responses.items():
            if key not in inventory:
                raise ValueError('ABNORMAL_RESPONSE_EVIDENCE_MISSING')
            item, raw = inventory[key]
            if (Path(item['source_path']).resolve() != Path(event['response_path']).resolve()
                    or raw.get('alias') != event.get('alias')
                    or raw.get('response', {}).get('status_code') != event.get('status_code')):
                raise ValueError('ABNORMAL_RESPONSE_BINDING')
        old = {_stable(k) for k in loaded['old_attempts'][1]['unique_attempt_keys']}
        prior_ledger_keys = set()
        if not launch.get('old_ledgers'):
            raise ValueError('ABNORMAL_PRIOR_LEDGER_MISSING')
        for ledger_binding in launch['old_ledgers']:
            _, raw = _verified(ledger_binding)
            try:
                prior_events = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
                prior_ledger_keys.update(_stable(event['canonical_dispatch_key']) for event in prior_events
                                         if event.get('event') == 'HTTP_ATTEMPT_BEFORE_POST')
            except (UnicodeError, ValueError, KeyError, TypeError) as exc:
                raise ValueError('ABNORMAL_PRIOR_LEDGER_INVALID') from exc
        if old != prior_ledger_keys:
            raise ValueError('ABNORMAL_PRIOR_ATTEMPT_EXCLUSIONS_MISMATCH')
        excluded = old | {_stable(k) for k in entries} | raw_footprints
        memory_aliases = {}
        for key in cache['memory']:
            memory_aliases.setdefault(_stable(key), set()).add(key)
        footprints = set(memory_aliases) | {_stable(k) for k in cache['memory_results']}
        artifacts = data.get('cache_artifacts', [])
        if data['mode'] == 'EVIDENCE_ONLY':
            cache_path = Path(cache_path)
            actual = {path.resolve() for pattern in (cache_path.name + '.tmp-*', cache_path.name + '.corrupt-*')
                      for path in cache_path.parent.glob(pattern)}
            declared = {Path(item['source_path']).resolve() for item in artifacts}
            if actual != declared:
                raise ValueError('ABNORMAL_CACHE_ARTIFACT_INVENTORY')
        artifact_footprints = set()
        for item in artifacts:
            _, artifact = _json(item['file'], 'CACHE_ARTIFACT')
            if data['mode'] == 'EVIDENCE_ONLY':
                _verified({'path': item['source_path'], 'sha256': item['file']['sha256']},
                          'CACHE_ARTIFACT_SOURCE_HASH')
            if (not isinstance(artifact, dict) or any(not isinstance(artifact.get(name), dict)
                    for name in ('entries', 'results', 'memory', 'memory_results'))):
                raise ValueError('ABNORMAL_CACHE_ARTIFACT_UNCERTAIN')
            artifact_footprints.update(_stable(k) for k in artifact['memory'])
            artifact_footprints.update(_stable(k) for k in artifact['memory_results'])
        excluded.update(artifact_footprints)
        fingerprints, unclaimed = {}, set()
        for key in plan - set(entries):
            identity = _stable(key)
            if identity in excluded:
                continue
            current = cache['memory'].get(key)
            prior = cache['memory_results'].get(identity)
            if key in claims:
                aliases = memory_aliases.get(identity, set())
                if (current and current.get('translation_status') == 'pending' and aliases == {key}
                        and (not prior or prior.get('translation_status') == 'pending')):
                    fingerprints[key] = ResumeAdmission.fingerprint(current)
                else:
                    excluded.add(identity)
            elif identity not in footprints:
                unclaimed.add(key)
            else:
                excluded.add(identity)
        obj = cls()
        obj.identity = binding['sha256']
        obj.mode = data['mode']
        obj.claim_fingerprints = fingerprints
        obj.unclaimed_keys = unclaimed
        obj.excluded = excluded
        obj.snapshot = cache
        products = loaded['products'][1]
        # Match the existing CLI's JSON-array / {records: [...]} contract,
        # using already hash-verified bytes rather than rereading the source.
        if isinstance(products, dict) and isinstance(products.get('records'), list):
            products = products['records']
        if not isinstance(products, list) or any(not isinstance(record, dict) for record in products):
            raise ValueError('ABNORMAL_PRODUCTS_FORMAT')
        obj.products = products
        obj.source_manifest = manifest
        obj.fields = loaded['source_config'][1].get('fields')
        obj.remaining_http_cap = len(plan) - len(sent)
        obj.unknown_sent = set(sent) - set(responses)
        obj.original_keys = plan
        obj.entered_keys = set(entries)
        obj.old_excluded = old
        return obj

    def _allows_snapshot(self, key, current, prior=None):
        if _stable(key) in self.excluded:
            return False
        if key in self.unclaimed_keys:
            return current is None and prior is None
        return bool(key in self.claim_fingerprints and current
                    and current.get('translation_status') == 'pending'
                    and ResumeAdmission.fingerprint(current) == self.claim_fingerprints[key]
                    and (not prior or prior.get('translation_status') == 'pending'))

    def allows(self, key, current, prior=None):
        return self.mode == 'EVIDENCE_ONLY' and self._allows_snapshot(key, current, prior)

    def simulate_plan(self, provider):
        """Use the actual planner with an in-memory, non-writing snapshot view."""
        from threading import RLock
        from .cache import TranslationCache
        from .service import TranslationService
        # No TranslationCache constructor: it may move a corrupt live file.
        cache = TranslationCache.__new__(TranslationCache)
        cache._lock = RLock()
        for name in ('entries', 'results', 'memory', 'memory_results'):
            setattr(cache, name, self.snapshot[name])
        cache.excluded_attempts = set(self.excluded)
        cache.resume_admission = _ReadOnlyPlanningView(self)
        def no_execution(*args, **kwargs):
            raise ValueError('SIMULATION_ONLY_EXECUTION_FORBIDDEN')
        for name in ('save', 'load', 'put', 'put_memory', 'claim', 'claim_memory',
                     'settle', 'settle_memory', 'wait_memory_settlement'):
            setattr(cache, name, no_execution)
        if ({'name': provider.name, 'model': provider.model}
                != self.source_manifest['provider_identity']):
            raise ValueError('ABNORMAL_SIMULATION_PROVIDER_IDENTITY')
        service = TranslationService(provider, cache, **self.source_manifest['service_contract'])
        plan = service.plan(self.products, fields=self.fields)
        safe = set(self.claim_fingerprints) | self.unclaimed_keys
        if set(plan['dispatch_keys']) - safe or len(plan['dispatch_keys']) > self.remaining_http_cap:
            raise ValueError('ABNORMAL_SIMULATION_PLAN_EXCEEDS_ADMISSION')
        if set(plan['dispatch_keys']) != safe:
            raise ValueError('ABNORMAL_SIMULATION_PLAN_SET_MISMATCH')
        return {'status': 'SIMULATION_ONLY_NOT_LIVE_AUTHORIZATION', 'execution_permitted': False,
                'plan': plan, 'safe_unclaimed': len(self.unclaimed_keys),
                'safe_claimed_never_entered': len(self.claim_fingerprints),
                'unknown_sent_held': len(self.unknown_sent), 'remaining_http_cap': self.remaining_http_cap,
                'original_dispatches': len(self.original_keys), 'simulated_dispatches': len(safe),
                'provider_entry_intersection': len(safe & self.entered_keys),
                'old_attempt_intersection': len({_stable(k) for k in safe} & self.old_excluded)}


def main(argv=None):
    """Standalone simulation CLI. There is deliberately no execution command."""
    import argparse
    from types import SimpleNamespace
    parser = argparse.ArgumentParser(description='Abnormal-exit admission offline simulation only')
    parser.add_argument('--simulate', action='store_true', required=True)
    parser.add_argument('--binding', required=True)
    parser.add_argument('--binding-sha', required=True)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    binding = {'path': args.binding, 'sha256': args.binding_sha}
    _, data = _json(binding)
    if data.get('mode') != 'SIMULATION_ONLY':
        raise ValueError('ABNORMAL_CLI_SIMULATION_MODE_REQUIRED')
    admission = AbnormalExitAdmission.from_file(binding, cache_path=args.cache)
    result = admission.simulate_plan(SimpleNamespace(**admission.source_manifest['provider_identity']))
    result['source_quiescence_verified'] = False
    result['source_exit_evidence_accepted'] = False
    with Path(args.out).open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != 'plan'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
