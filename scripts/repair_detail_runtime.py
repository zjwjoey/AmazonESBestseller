"""Stage task-script repairs and reconcile saved outcomes, without live work.

Writes only to a new output directory outside the production project. It never
imports the historical task runner or changes its evidence/checkpoints.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from amazon_es_bestseller.collection.runtime_io import read_json, atomic_json
from amazon_es_bestseller.collection.detail_executor import _usable_record, execute_detail_plan
from amazon_es_bestseller.collection.detail_reconciliation import checkpoint_outcome, reconcile_outcomes
from amazon_es_bestseller.monitoring.detail_planner import write_detail_plan, validate_detail_plan


def verify_staged_contract(scripts, tasks, outcomes, held):
    """Exercise actual patched progress/request-boundary functions, no runner startup."""
    import threading
    import time
    from types import SimpleNamespace
    tree = ast.parse((Path(scripts) / 'run_shared.py').read_text(encoding='utf-8'))
    def function(name):
        candidates = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(candidates) != 1:
            raise ValueError('UNREVIEWED_RUNTIME_FUNCTION:' + name)
        return compile(ast.Module(body=candidates, type_ignores=[]), str(Path(scripts) / 'run_shared.py'), 'exec')
    captured = []
    namespace = {'state_lock': threading.RLock(), 'tasks': tasks, 'outcomes': outcomes,
        'held': set(held), 'reconcile_outcomes': reconcile_outcomes, 'Counter': Counter,
        'worker_states': {str(i): {'state': 'FINISHED'} for i in range(3)},
        'prepared': {'admitted': len(tasks), 'blocked': 0}, 'ROOT': Path('offline_contract'),
        'now': lambda: 'OFFLINE_VALIDATION', 'started': time.monotonic(), 'time': time,
        'write': lambda path, value: captured.append(value)}
    exec(function('progress'), namespace)
    result = namespace['progress']()
    assert captured == [result]
    assert sum(result['outcomes'].values()) == len(tasks)
    assert result['successful_unique_asins'] == reconcile_outcomes(tasks, outcomes, held)['success']
    assert 'records' not in result['terminal_accounting']
    serialized_size = len(__import__('json').dumps(result).encode('utf-8'))
    assert serialized_size < 30000  # never append 5500 ASIN records per event
    event = threading.Event()
    event.set()
    calls = []
    namespace.update({'common': SimpleNamespace(persistence_failed=event),
        'pace_lock': threading.Lock(), 'last_start': [None],
        'original_goto': lambda *a, **kw: calls.append((a, kw))})
    exec(function('paced_goto'), namespace)
    try:
        namespace['paced_goto']('https://www.amazon.es/dp/B07B8D8RC5')
    except OSError as error:
        assert str(error) == 'SHARED_PERSISTENCE_FAILURE'
    else:
        raise AssertionError('PERSISTENCE_FAILURE_MUST_STOP_REQUESTS')
    assert calls == []
    return {'progress_coverage': sum(result['outcomes'].values()),
            'success_count': result['successful_unique_asins'], 'progress_bytes': serialized_size,
            'request_boundary_stops_after_persistence_failure': True, 'network_requests': 0}


def stage_scripts(task_root, output, checkout):
    """Produce reviewed copies, retaining the original frozen task root."""
    scripts = output / 'staged_runtime'
    scripts.mkdir()
    text = (task_root / 'run.py').read_text(encoding='utf-8')
    text = text.replace('ROOT = Path(__file__).resolve().parent', f'ROOT = Path({str(task_root)!r})')
    old = "RELEASE = PROJECT / '.releases/action5500-main6c1cf342-compat'"
    if text.count(old) != 1:
        raise ValueError('UNREVIEWED_RUNTIME_RELEASE')
    text = text.replace(old, f'RELEASE = Path({str(checkout)!r})')
    start, end = text.index('def read('), text.index('\ndef validate():')
    text = text[:start] + '''from amazon_es_bestseller.collection.runtime_io import read_json, atomic_json
import threading
persistence_failed = threading.Event()


def read(path, default=None):
    try:
        return read_json(path, default)
    except OSError:
        persistence_failed.set()
        raise


def write(path, data):
    try:
        atomic_json(path, data)
    except OSError:
        persistence_failed.set()
        raise

''' + text[end:]
    # Auxiliary task modules remain read from the original folder. The patched
    # run_shared inserts its own folder first so `import run` uses this copy.
    text = text.replace('import amazon_es_bestseller\n',
                        'sys.path.insert(1, str(ROOT))\nimport amazon_es_bestseller\n', 1)
    (scripts / 'run.py').write_text(text, encoding='utf-8')
    text = (task_root / 'run_shared.py').read_text(encoding='utf-8')
    text = text.replace('import run as common',
        'sys.path.insert(0, str(Path(__file__).resolve().parent))\nimport run as common', 1)
    text = text.replace('from collections import Counter,defaultdict',
        'from collections import Counter,defaultdict\nfrom amazon_es_bestseller.collection.detail_reconciliation import reconcile_outcomes', 1)
    # The package import must follow `common`, which selects its release.
    text = text.replace('\nfrom amazon_es_bestseller.collection.detail_reconciliation import reconcile_outcomes', '', 1)
    text = text.replace('import run as common', 'import run as common\nfrom amazon_es_bestseller.collection.detail_reconciliation import reconcile_outcomes', 1)
    # The shared queue was a task-local compatibility addition, absent from
    # main. Stage that small existing helper, not a collector framework.
    text = text.replace('from amazon_es_bestseller.collection.shared_detail_queue import SharedDetailQueue',
                        'from shared_detail_queue import SharedDetailQueue', 1)
    old = "counts=Counter(outcomes.values());success=counts.get('success',0)+counts.get('reused',0)"
    if text.count(old) != 1:
        raise ValueError('UNREVIEWED_PROGRESS_IMPLEMENTATION')
    text = text.replace(old, "accounting=reconcile_outcomes(tasks,outcomes,{a:'HELD' for a in held})\n            counts=Counter(accounting['counts']);success=accounting['success']")
    text = text.replace("'outcomes':dict(counts),'workers':worker_states",
        "'outcomes':dict(counts),'terminal_accounting':{k:v for k,v in accounting.items() if k!='records'},'workers':worker_states", 1)
    text = text.replace("while True:\n                    if (ROOT/'shared_queue_pause.json').exists():",
        "while True:\n                    if common.persistence_failed.is_set():\n                        update('PROGRAM_STOP',error='SHARED_PERSISTENCE_FAILURE');break\n                    if (ROOT/'shared_queue_pause.json').exists():", 1)
    text = text.replace('return original_goto(url,timeout_ms=timeout_ms)',
        "if common.persistence_failed.is_set():raise OSError('SHARED_PERSISTENCE_FAILURE')\n                    return original_goto(url,timeout_ms=timeout_ms)", 1)
    # Checkpoint / DetailState failures originate below the common IO wrappers.
    # Latch them before attempting any further status persistence.
    text = text.replace("except Exception as exc:\n            write(wroot/'stop_reason.json'", "except Exception as exc:\n            if isinstance(exc,OSError):common.persistence_failed.set()\n            write(wroot/'stop_reason.json'", 1)
    for name, body in [('run_shared.py', text)]:
        compile(body, str(scripts / name), 'exec')
        (scripts / name).write_text(body, encoding='utf-8')
    compile((scripts / 'run.py').read_text(encoding='utf-8'), str(scripts / 'run.py'), 'exec')
    queue_source = task_root.parents[1] / '.releases/action5500-main6c1cf342-compat/src/amazon_es_bestseller/collection/shared_detail_queue.py'
    queue_text = queue_source.read_text(encoding='utf-8')
    queue_text = queue_text.replace('import threading', 'import threading\nfrom amazon_es_bestseller.collection.runtime_io import read_json', 1)
    queue_text = queue_text.replace("claim=json.loads((self.root/'claims'/(asin+'.json')).read_text(encoding='utf-8'))",
                                    "claim=read_json(self.root/'claims'/(asin+'.json'))", 1)
    (scripts / 'shared_detail_queue.py').write_text(queue_text, encoding='utf-8')
    resume_text = (task_root / 'resume_control.py').read_text(encoding='utf-8')
    resume_text = resume_text.replace('from pathlib import Path', 'from pathlib import Path\nfrom amazon_es_bestseller.collection.runtime_io import read_json', 1)
    resume_text = resume_text.replace("def read(p):return json.loads(p.read_text(encoding='utf-8'))", "def read(p):return read_json(p)", 1)
    (scripts / 'resume_control.py').write_text(resume_text, encoding='utf-8')
    return {'deployed': False, 'launched': False, 'directory': str(scripts),
            'package_root': str(checkout), 'task_root': str(task_root),
            'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in scripts.glob('*.py')}}


def stage_checkpoint_accounting(task_root, output):
    """Stage only two already-reviewed task scripts; no task/data is executed."""
    task_root, output = Path(task_root).resolve(), Path(output).resolve()
    production = task_root.parents[1]
    if output == production or output.is_relative_to(production):
        raise ValueError('OUTPUT_MUST_NOT_WRITE_PRODUCTION_PROJECT')
    helper = 'from amazon_es_bestseller.collection.detail_reconciliation import checkpoint_outcome\n'
    patches = {
        'resume_control.py': ("outcomes[asin]=c.get('status') or 'pending'",
                              'outcomes[asin]=checkpoint_outcome(c,usable)',
                              'from amazon_es_bestseller.collection.runtime_io import read_json\n', 1),
        'run_shared.py': ("outcomes[cp['asin']]=cp.get('status') or 'pending'",
                          "outcomes[cp['asin']]=checkpoint_outcome(cp,_usable_record)",
                          'import run as common\n', 2),
    }
    staged, source_hashes = {}, {}
    for name, (old, new, anchor, expected_count) in patches.items():
        body = (task_root / name).read_bytes()
        source_hashes[name] = hashlib.sha256(body).hexdigest()
        text = body.decode('utf-8')
        # read_bytes keeps CRLF; normalize only the staged code text.
        text = text.replace('\r\n', '\n')
        if text.count(old) == expected_count and new not in text:
            text = text.replace(old, new)
        elif old in text or text.count(new) != expected_count:
            raise ValueError('UNREVIEWED_TASK_CHECKPOINT_ACCOUNTING:' + name)
        if helper not in text:
            if text.count(anchor) != 1:
                raise ValueError('UNREVIEWED_TASK_IMPORT_ANCHOR:' + name)
            text = text.replace(anchor, anchor + helper, 1)
        compile(text, str(task_root / name), 'exec')
        staged[name] = text
    output.mkdir(parents=True, exist_ok=False)
    for name, text in staged.items():
        (output / name).write_text(text, encoding='utf-8')
    return {'directory': str(output), 'source_hashes': source_hashes,
            'staged_hashes': {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                              for name in staged}, 'production_writes': 0, 'network_requests': 0}


def repair(task_root, output, checkout):
    task_root, output, checkout = Path(task_root).resolve(), Path(output).resolve(), Path(checkout).resolve()
    production = task_root.parents[1]  # <project>/runtime/<task>
    if output == production or output.is_relative_to(production):
        raise ValueError('OUTPUT_MUST_NOT_WRITE_PRODUCTION_PROJECT')
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    def read(path, default=None):
        path = Path(path)
        value = read_json(path, default)
        if path.is_file():
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return value
    tasks = read(task_root / 'shared_queue/frozen_tasks.json')
    outcomes = {}
    state = read(task_root / 'run/state/details_state.json', {})
    for asin, row in state.items():
        if _usable_record(row, asin):
            outcomes[asin] = 'reused'
    paths = sorted((task_root / 'workers').glob('worker_*/run/checkpoints/*.json'))
    paths += sorted((task_root / 'shared_queue/checkpoints').glob('*.json'))
    for index, path in enumerate(paths):
        cp = read(path)
        asin = cp.get('asin')
        if cp.get('status') == 'success' and _usable_record(cp.get('record'), asin):
            outcomes[asin] = 'success'
        elif asin and outcomes.get(asin) not in {'success', 'reused'}:
            if path.parent == task_root / 'shared_queue/checkpoints':
                outcomes[asin] = checkpoint_outcome(cp, _usable_record)
            else:
                outcomes.setdefault(asin, checkpoint_outcome(cp, _usable_record))
        if index and index % 1500 == 0:
            print(f'Offline checkpoints read: {index}/{len(paths)}', flush=True)
    held = read(task_root / 'shared_held_items.json', {})
    reviewed = []
    for task in tasks:
        asin = task['asin']
        if task['action'] != 'REUSE_VALID_CACHE' or outcomes.get(asin) in {'success', 'reused'}:
            continue
        plan = validate_detail_plan(read(task['plan']))
        if plan['plan_hash'] != task['plan_hash']:
            raise ValueError('FROZEN_SOURCE_PLAN_CHANGED')
        row = next(row for row in plan['records'] if row['asin'] == asin)
        plan_path = write_detail_plan({'snapshot_id': plan['snapshot_id'], 'records': [row]}, output / 'plans' / asin)['json']
        # No original checkpoint/state is supplied: this is an isolated replay.
        # An offline executor cannot request a missing page.
        class NoBrowser:
            def goto(self, *a, **kw):
                raise AssertionError('NETWORK_FORBIDDEN')
        result = execute_detail_plan(read_json(plan_path), NoBrowser(), str(output / 'replay' / asin),
                                     offline=True, parser_version='v2')
        html_path = Path(row['saved_html_dir']) / (asin + '.html')
        if html_path.exists():
            hashes[str(html_path)] = hashlib.sha256(html_path.read_bytes()).hexdigest()
        status = result['records'][0]['status']
        identity_unresolved = bool(row.get('resolved_asin') and row['resolved_asin'] != asin)
        if status in {'REUSED', 'SUCCESS', 'ALREADY_SUCCESS'}:
            outcomes[asin] = 'reused'
        else:
            outcomes[asin] = ('CACHE_UNAVAILABLE_IDENTITY_REVIEW' if identity_unresolved
                              else 'CACHE_REVIEW_REQUIRED')
        reviewed.append({'asin': asin, 'status': outcomes[asin], 'executor_status': status,
                         'requested_count': result['requested_count'], 'saved_html_exists': html_path.is_file(),
                         'planned_resolved_asin': row.get('resolved_asin'),
                         'planned_identity_status': row.get('identity_status'),
                         'reason': 'No verified same-ASIN saved evidence was replayable' if status == 'PENDING' else status})
    accounting = reconcile_outcomes(tasks, outcomes, held)
    original = read(task_root / 'parallel_result.json')
    # Accounting repair must not silently lose prior known success or promote a
    # missing cache into success. Any difference is exposed, not patched away.
    report = {'original_summary': {k: original.get(k) for k in ['target', 'status', 'outcomes', 'successful_unique_asins']},
              'accounting': accounting, 'cache_reviews': reviewed,
              'original_successes_preserved': accounting['success'] >= original['successful_unique_asins'],
              'network_requests': 0, 'translation_api_calls': 0,
              'staged_runtime': stage_scripts(task_root, output, checkout)}
    report['staged_contract_verification'] = verify_staged_contract(
        report['staged_runtime']['directory'], tasks, outcomes, held)
    changed = [path for path, sha in hashes.items() if hashlib.sha256(Path(path).read_bytes()).hexdigest() != sha]
    if changed:
        raise ValueError('SOURCE_CHANGED_DURING_REVIEW:' + str(changed))
    report['read_source_files_unchanged'] = len(hashes)
    atomic_json(output / 'source_hashes.json', hashes)
    atomic_json(output / 'reconciliation.json', report)
    print({k: accounting[k] for k in ['target', 'accounted', 'success', 'non_success', 'counts']}, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repair(args.task_root, args.output, Path(__file__).resolve().parents[1])
