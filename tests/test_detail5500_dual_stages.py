import json
import pytest

from amazon_es_bestseller.collection import detail_dual_stage5500 as stage
from amazon_es_bestseller.multisource.storage import digest
from amazon_es_bestseller.collection.quick_selection import file_sha


def fixture(tmp_path, cumulative=100):
    tasks = [dict(task_id=f't{i}', request_url=f'https://www.amazon.es/dp/B{i:09d}') for i in range(1200)]
    plan = dict(run_id='run', tasks=tasks)
    previous = {100: 0, 500: 100, 1000: 500}[cumulative]
    doc = dict(approved=True, run_id='run', runtime_root=str(tmp_path.resolve()),
               plan_sha256=digest(plan), code_sha='code', cumulative_limit=cumulative,
               previous_cumulative_limit=previous, transaction_limit=cumulative-previous,
               cohort=tasks[:cumulative-previous], no_successful_task_replay=True)
    auth = dict(code_sha='code')
    def write():
        path = tmp_path/'stage.json'
        path.write_text(json.dumps(doc), encoding='utf8')
        auth['dual_stage_reference'] = dict(path=str(path), sha256=file_sha(path))
    write()
    return plan, auth, doc, tasks, write


def test_first_stage_selects_exact_first_100_and_cannot_claim_101(tmp_path):
    plan, auth, doc, tasks, _ = fixture(tmp_path)
    s = stage.load_stage(tmp_path, plan, auth, tasks)
    assert len(s.tasks) == 100
    for task in tasks[:100]: s.claim(task)
    with pytest.raises(ValueError): s.claim(tasks[100])
    with pytest.raises(ValueError): s.claim(tasks[0])


@pytest.mark.parametrize('field,value', [('transaction_limit', 101), ('cumulative_limit', 1600),
    ('code_sha', 'old'), ('run_id','other'), ('no_successful_task_replay',False)])
def test_changed_controls_refused(tmp_path, field, value):
    plan, auth, doc, tasks, write = fixture(tmp_path)
    doc[field] = value; write()
    with pytest.raises(ValueError): stage.load_stage(tmp_path, plan, auth, tasks)


def test_reordered_or_already_successful_cohort_refused(tmp_path):
    plan, auth, doc, tasks, write = fixture(tmp_path)
    doc['cohort'][0], doc['cohort'][1] = doc['cohort'][1], doc['cohort'][0]; write()
    with pytest.raises(ValueError): stage.load_stage(tmp_path, plan, auth, tasks)
    doc['cohort'] = tasks[:100]; write()
    with pytest.raises(ValueError): stage.load_stage(tmp_path, plan, auth, tasks[1:])


@pytest.mark.parametrize('limit', [500,1000])
def test_escalation_requires_previous_independent_acceptance(tmp_path, limit):
    plan, auth, doc, tasks, write = fixture(tmp_path, limit)
    with pytest.raises(ValueError): stage.load_stage(tmp_path, plan, auth, tasks)
    accepted = tmp_path/'previous.accepted.json'
    accepted.write_text(json.dumps(dict(passed=True, independent_full_record_audit=True,
        run_id='run', plan_sha256=digest(plan), cumulative_limit=doc['previous_cumulative_limit'],
        no_successful_task_replay=True)), encoding='utf8')
    doc['previous_acceptance_reference'] = dict(path=str(accepted), sha256=file_sha(accepted)); write()
    s = stage.load_stage(tmp_path, plan, auth, tasks)
    assert len(s.tasks) == {500:400,1000:500}[limit]
    accepted.write_text('{}', encoding='utf8')
    with pytest.raises(ValueError): stage.load_stage(tmp_path, plan, auth, tasks)


def test_stage_cap_is_transaction_count_not_success_quota(tmp_path):
    plan, auth, doc, tasks, _ = fixture(tmp_path)
    s = stage.load_stage(tmp_path, plan, auth, tasks)
    for task in tasks[:100]: s.claim(task)
    result = stage.stage_result(s, {'success':90,'statuses':{'IDENTITY_REVIEW':10}}, 'auth')
    assert result['attempted_transactions'] == 100
    assert result['phase'] == 'DUAL_STAGE_DRAINED_AWAITING_INDEPENDENT_REVIEW'
    assert result['passed'] is False and result['not_final_acceptance'] is True


def test_cli_run_cannot_be_unbounded_without_stage(tmp_path):
    plan, auth, _, tasks, _ = fixture(tmp_path)
    del auth['dual_stage_reference']
    with pytest.raises(ValueError): stage.load_stage(tmp_path, plan, auth, tasks)


def test_production_entry_drains_exact_cap_without_running_final_audit(tmp_path, monkeypatch):
    import asyncio
    from contextlib import nullcontext
    from types import SimpleNamespace as NS
    from amazon_es_bestseller.collection import detail_dual_runtime5500 as runtime
    from amazon_es_bestseller.collection import delivery_setup5500, quick5500_cli, detail5500_runtime
    import playwright.async_api
    plan, auth, doc, tasks, write = fixture(tmp_path)
    plan['shared_gate_path'] = str(tmp_path/'shared.json')
    doc['plan_sha256'] = digest(plan); write()
    (tmp_path/'detail_plan_5500.json').write_text(json.dumps(plan), encoding='utf8')
    ap = tmp_path/'auth.json'; ap.write_text(json.dumps(auth), encoding='utf8')
    checks = []
    bridge = NS(validate_authorization=lambda a:checks.append('authorization'),
                validate_stage=lambda *a,**kw:checks.append(a[0]), progress=lambda:{'success':100})
    monkeypatch.setattr(runtime, 'DetailBridge5500', lambda *a:bridge)
    monkeypatch.setattr(runtime, 'validate_parallel_mode', lambda *a:{'browser_profile':str(tmp_path/'profile')})
    monkeypatch.setattr(runtime.subprocess, 'check_output', lambda args,**kw:'code' if args[1]=='rev-parse' else '')
    monkeypatch.setattr(runtime, 'pending_tasks', lambda *a:tasks)
    monkeypatch.setattr(runtime, 'file_lock', lambda *a:nullcontext())
    monkeypatch.setattr(runtime, 'full_qa_reserve', lambda *a:17611.398)
    monkeypatch.setattr(delivery_setup5500, 'verify_setup', lambda *a:None)
    monkeypatch.setattr(quick5500_cli, 'export', lambda *a:checks.append('export'))
    def forbidden(*a,**kw): raise AssertionError('PILOT_MUST_NOT_RUN_FINAL')
    monkeypatch.setattr(detail5500_runtime, 'audit_stage', forbidden)
    class Context:
        async def close(self): checks.append('closed')
    class Browser:
        async def launch_persistent_context(self,*a,**kw):
            assert kw['headless'] is False
            return Context()
    class Playwright:
        async def __aenter__(self): return NS(chromium=Browser())
        async def __aexit__(self,*a): pass
    monkeypatch.setattr(playwright.async_api, 'async_playwright', Playwright)
    class Collector:
        def __init__(self,*a,**kw): assert kw['qa_seconds']==17611.398
        async def run(self,context,cohort):
            assert cohort==tasks[:100]
            for task in cohort:self.stage.claim(task)
            checks.append('drained')
    monkeypatch.setattr(runtime,'ParallelCollector',Collector)
    asyncio.run(runtime.run(tmp_path,ap,tmp_path/'profile'))
    result=json.loads((tmp_path/'dual_stage_100.drained.json').read_text(encoding='utf8'))
    assert result['attempted_transactions']==100 and not result['passed']
    assert checks == ['authorization','T1','T2','drained','export','closed']
    assert not (tmp_path/'FINAL_acceptance.json').exists()
    assert not (tmp_path/'detail_run_manifest.json').exists()
