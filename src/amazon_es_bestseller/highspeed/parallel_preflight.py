"""No-network preflight shared by the parallel check and execution entries."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET
from ..multisource.storage import digest
from .parallel_amendment import validate_parallel_amendment
from .page_snapshot import validate_page_snapshot
from .task_clock import request_stop_reason


def verify_code_manifest(code_root,manifest):
    root=Path(code_root).resolve();files=manifest.get('files',{})
    required={f'src/amazon_es_bestseller/highspeed/parallel_{name}.py' for name in
        ('transport','commit','coordinator','admission','dispatch_gate','page_ledger','amendment','preflight','checkpoint','cli','alias_review','faults')}
    if not required.issubset(files):raise ValueError('COMPLETE_PARALLEL_CODE_MANIFEST_REQUIRED')
    for relative,expected in files.items():
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise ValueError('CODE_MANIFEST_PATH_INVALID')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('CODE_MANIFEST_BYTES_CHANGED:'+relative)


def verify_isolated_acceptance(plan,amendment,manifest):
    ref=amendment.get('isolated_acceptance_reference') or {}
    path=Path(ref.get('path','__missing__'))
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=ref.get('sha256'):
        raise ValueError('ISOLATED_ACCEPTANCE_HASH_REQUIRED')
    review=json.loads(path.read_text(encoding='utf8'))
    if (review.get('kind')!='PARALLEL_ISOLATED_ACCEPTANCE_V1' or review.get('passed') is not True
            or review.get('run_id')!=plan['run_id'] or review.get('plan_sha256')!=digest(plan)
            or review.get('code_manifest_sha256')!=digest(manifest)
            or review.get('original_clock_reference')!=amendment.get('original_clock_reference')):
        raise ValueError('ISOLATED_ACCEPTANCE_SCOPE_MISMATCH')
    required={'test_parallel_browser_pipeline','test_parallel_snapshot_hooks','test_parallel_checkpoint',
        'test_parallel_real_gate_offline','test_parallel_dispatch_gate','test_parallel_admission',
        'test_parallel_cli','test_parallel_preflight','test_parallel_alias_review','test_parallel_early_access_stop',
        'test_parallel_fault_alerts','test_parallel_fault_browser'}
    covered=set()
    receipts=review.get('test_executions',[])
    if not receipts:raise ValueError('ACTUAL_TEST_EXECUTION_REQUIRED')
    for reference in receipts:
        test_path=Path(reference['path'])
        if hashlib.sha256(test_path.read_bytes()).hexdigest()!=reference['sha256']:
            raise ValueError('TEST_EXECUTION_RECEIPT_CHANGED')
        execution=json.loads(test_path.read_text(encoding='utf8'))
        if (execution.get('exit_code')!=0 or execution.get('code_manifest_sha256_before')!=digest(manifest)
                or execution.get('code_manifest_sha256_after')!=digest(manifest)
                or execution.get('runtime_compatible') is not True):
            raise ValueError('PASSING_RUNTIME_COMPATIBLE_TEST_EXECUTION_REQUIRED')
        junit=Path(execution['junit_reference']['path'])
        if hashlib.sha256(junit.read_bytes()).hexdigest()!=execution['junit_reference']['sha256']:
            raise ValueError('TEST_RESULT_HASH_CHANGED')
        result=ET.fromstring(junit.read_bytes());cases=result.findall('.//testcase')
        if not cases or result.findall('.//failure') or result.findall('.//error') or result.findall('.//skipped'):
            raise ValueError('ALL_REQUIRED_TESTS_MUST_PASS')
        covered.update(part for case in cases for part in case.get('classname','').split('.') if part.startswith('test_'))
    if not required.issubset(covered):raise ValueError('ISOLATED_ACCEPTANCE_COVERAGE_INCOMPLETE')
    return review


def check_parallel_start(runner,plan,authorization,amendment,manifest,*,code_root,now):
    verify_code_manifest(code_root,manifest)
    validate_page_snapshot(plan)
    gate=runner.engine._gate(plan)
    validate_parallel_amendment(plan,authorization,amendment,now=now,gate=gate,code_manifest=manifest)
    store=runner.store;run=store.get_run(plan['run_id'])
    if run['plan']!=plan:raise ValueError('IMMUTABLE_RUN_PLAN_MISMATCH')
    if str(store.root.resolve())!=str(Path(authorization['store_root']).resolve()):
        raise ValueError('ORIGINAL_STORE_REQUIRED')
    if any(r['status']=='RESERVED' for r in store.requests(plan['run_id'])):
        raise ValueError('AMBIGUOUS_UNFINISHED_REQUESTS_NO_AUTO_RETRY')
    reason=request_stop_reason(plan,authorization,store,now=now)
    if reason:raise ValueError(reason)
    return {'authorization_valid':True,'run_id':plan['run_id'],'browser_started':False,
        'task_clock_preserved':True,'prior_requests':len(store.requests(plan['run_id'])),
        'prior_pages':len(store.pages(plan['run_id'])),'delivery_preflight_required':True,
        'pause_release_required':True,'live_ready':False}
