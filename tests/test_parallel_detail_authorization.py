from datetime import datetime,timezone
import json
import pytest
from amazon_es_bestseller.collection.detail_parallel_authorization import validate_approval,SharedDetailGates
from amazon_es_bestseller.multisource.storage import digest,immutable_json,sealed,read_sealed
from amazon_es_bestseller.access.detector import AccessStopError


def documents(tmp_path):
    plan={'run_id':'run','tasks':[{'task_id':'one','asin':'B000000001',
          'request_url':'https://www.amazon.es/dp/B000000001'}]}
    base={'run_id':'run','starts_at':'2026-10-23T00:00:00Z','deadline':'2026-10-24T00:00:00Z',
          'request_deadline':'2026-10-23T20:00:00Z','max_requests':20,'max_page_requests':20,
          'max_other_requests':8,'max_attempts_per_task':1,'request_start_interval_seconds':3.6,
          'browser_profile':str(tmp_path/'profile'),'runtime_root':str(tmp_path),
          'required_postal_code':'28001',
          'plan_sha256':digest(plan)}
    def save(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value),encoding='utf8')
        return {'path':str(path),'sha256':digest(path.read_bytes())}
    base_ref=save('base.json',base)
    ledger_ref=save('ledger.json',{'run_id':'run','base_authorization_sha256':base_ref['sha256'],
                                 'paid_requests':2,'budget_holds':3})
    approval={**base,'approved':True,'approved_by':'user','explicit_user_instruction':'five lanes',
        'scope':'FIVE_LANES_GROUPED_3_PLUS_2','parser_contract':2,'lanes':5,'processing_groups':[3,2],
        'max_uncommitted_tasks':5,'plan_sha256':digest(plan),'code_sha':'current',
        'runtime_root':str(tmp_path),'browser_profile':str(tmp_path/'profile'),
        'base_authorization_reference':base_ref,'cumulative_budget_reference':ledger_ref,
        'access_gate_paths':[str(tmp_path/'gate1'),str(tmp_path/'gate2')]}
    return plan,approval


def test_authorization_retains_original_scope_clock_and_budget(tmp_path):
    plan,approval=documents(tmp_path)
    options=dict(now=datetime(2026,10,23,1,tzinfo=timezone.utc))
    args=(plan,tmp_path,tmp_path/'profile','current')
    assert validate_approval(approval,*args,**options)=={'max_requests':20,'prior_requests':2,'budget_holds':3}
    for key,value in [('max_requests',21),('parser_contract',1),('lanes',3),('code_sha','old'),
                      ('request_start_interval_seconds',3.59),('deadline','2026-10-25T00:00:00Z')]:
        with pytest.raises(ValueError):validate_approval({**approval,key:value},*args,**options)


def test_gate_uses_payload_state_preserves_first_stop_and_cannot_clear(tmp_path):
    paths=[tmp_path/'one',tmp_path/'two']
    for path in paths:immutable_json(path,sealed({'state':'NORMAL'}))
    gates=SharedDetailGates(paths);gates.require_normal();gates.stop('CHALLENGE')
    before=[path.read_bytes() for path in paths]
    gates.stop('UNKNOWN')
    assert [path.read_bytes() for path in paths]==before
    assert all(read_sealed(path)['state']=='CHALLENGE' for path in paths)
    with pytest.raises(AccessStopError):gates.require_normal()
    with pytest.raises(ValueError):gates.stop('NORMAL')


def test_validate_only_does_not_open_profile_or_create_run_state(tmp_path,monkeypatch):
    import asyncio
    from amazon_es_bestseller.collection import detail_five_runtime as runtime
    plan,approval=documents(tmp_path)
    for path in approval['access_gate_paths']:immutable_json(path,sealed({'state':'NORMAL'}))
    plan_path=tmp_path/'plan.json';approval_path=tmp_path/'approval.json'
    plan_path.write_text(json.dumps(plan),encoding='utf8')
    approval_path.write_text(json.dumps(approval),encoding='utf8')
    monkeypatch.setattr(runtime,'current_code',lambda:'current')
    monkeypatch.setattr(runtime,'validate_approval',lambda *args:validate_approval(
        *args,now=datetime(2026,10,23,1,tzinfo=timezone.utc)))
    result=asyncio.run(runtime.run(plan_path,approval_path,tmp_path,tmp_path/'profile',validate_only=True))
    assert result['state']=='VALIDATED_NOT_STARTED'
    assert not (tmp_path/'profile').exists()
    assert not (tmp_path/'parallel_contract.json').exists()


def test_approval_cannot_rebind_the_original_root_or_frozen_plan(tmp_path):
    plan,approval=documents(tmp_path)
    now=datetime(2026,10,23,1,tzinfo=timezone.utc)
    changed_root=tmp_path/'different_run_root'
    with pytest.raises(ValueError,match='ORIGINAL'):
        validate_approval({**approval,'runtime_root':str(changed_root)},plan,changed_root,
                          tmp_path/'profile','current',now=now)
    changed_plan={**plan,'tasks':[{'task_id':'foreign','asin':'B000000002',
                                 'request_url':'https://www.amazon.es/dp/B000000002'}]}
    with pytest.raises(ValueError,match='ORIGINAL'):
        validate_approval({**approval,'plan_sha256':digest(changed_plan)},changed_plan,tmp_path,
                          tmp_path/'profile','current',now=now)


@pytest.mark.parametrize('paid,holds',[(-1,3),(2,-1),(30,3)])
def test_invalid_cumulative_ledger_rejected_during_preflight(tmp_path,paid,holds):
    plan,approval=documents(tmp_path)
    path=tmp_path/'ledger.json'
    value=json.loads(path.read_text(encoding='utf8'))
    value.update(paid_requests=paid,budget_holds=holds)
    path.write_text(json.dumps(value),encoding='utf8')
    approval['cumulative_budget_reference']['sha256']=digest(path.read_bytes())
    with pytest.raises(ValueError):
        validate_approval(approval,plan,tmp_path,tmp_path/'profile','current',
                          now=datetime(2026,10,23,1,tzinfo=timezone.utc))
