import json
from pathlib import Path
import pytest
from amazon_es_bestseller.collection import detail_dual_stage5500 as stage
from amazon_es_bestseller.collection.quick_selection import file_sha
from amazon_es_bestseller.multisource.storage import sealed, immutable_json, digest
from test_detail5500_dual_stages import fixture


def resume_fixture(root,count=3):
    plan,auth,doc,tasks,write=fixture(root)
    original=auth['dual_stage_reference'].copy()
    previous=root/'old-auth.json';immutable_json(previous,auth)
    auth['code_sha']='new-code'
    completed=[]
    for i,task in enumerate(tasks[:count]):
        slot=root/'tasks'/task['task_id'];slot.mkdir(parents=True)
        html=slot/'page.html';html.write_text('original HTML',encoding='utf8')
        meta=slot/'meta.json';immutable_json(meta,{'original':True})
        rp=slot/'receipt.json';immutable_json(rp,sealed(dict(task_id=task['task_id'],state='NORMAL',status_code=200,request_url=task['request_url'])))
        status='IDENTITY_REVIEW' if i==count-1 else 'SUCCESS'
        cp=slot/'checkpoint.json';immutable_json(cp,sealed(dict(task_id=task['task_id'],status=status,
            html_path=str(html),html_sha256=file_sha(html),receipt_path=str(rp),receipt_sha256=file_sha(rp),
            response_metadata_path=str(meta),response_metadata_sha256=file_sha(meta))))
        completed.append(dict(task_id=task['task_id'],checkpoint_reference=dict(path=str(cp),sha256=file_sha(cp))))
    proof=dict(approved=True,approved_by='agent_under_explicit_user_delegation',run_id=plan['run_id'],
        runtime_root=str(root.resolve()),plan_sha256=digest(plan),code_sha='new-code',original_stage_reference=original,
        previous_authorization_reference=dict(path=str(previous),sha256=file_sha(previous)),
        independently_reviewed_saved_results=True,no_successful_task_replay=True,
        completed_transactions=completed)
    pp=root/'resume-proof.json'
    def save():
        pp.write_text(json.dumps(proof),encoding='utf8')
        auth['dual_stage_resume_reference']=dict(path=str(pp),sha256=file_sha(pp))
    save()
    return plan,auth,doc,tasks,proof,save


def test_resume_only_original_remaining_97_counts_previous_failures(tmp_path):
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path)
    s=stage.load_stage(tmp_path,plan,auth,tasks[3:])
    assert s.tasks==tasks[3:100] and len(s.tasks)==97
    for task in s.tasks:s.claim(task)
    result=stage.stage_result(s,{},'auth')
    assert result['attempted_transactions']==100 and result['prior_transactions']==3
    assert result['cohort_sha256']==digest(doc['cohort'])
    with pytest.raises(ValueError):s.claim(tasks[100])
    with pytest.raises(ValueError):s.claim(tasks[0])


@pytest.mark.parametrize('field,value', [('approved',False),('code_sha','old'),('run_id','other'),
    ('independently_reviewed_saved_results',False),('no_successful_task_replay',False)])
def test_resume_bound_authority_required(tmp_path,field,value):
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path)
    proof[field]=value;save()
    with pytest.raises(ValueError):stage.load_stage(tmp_path,plan,auth,tasks[3:])


def test_missing_changed_or_duplicate_prior_checkpoint_cannot_skip_a_request(tmp_path):
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path)
    proof['completed_transactions'].append(proof['completed_transactions'][0]);save()
    with pytest.raises(ValueError):stage.load_stage(tmp_path,plan,auth,tasks[3:])
    proof['completed_transactions'].pop();save()
    path=Path(proof['completed_transactions'][0]['checkpoint_reference']['path'])
    path.write_text('{}',encoding='utf8')
    with pytest.raises(ValueError):stage.load_stage(tmp_path,plan,auth,tasks[3:])


def test_remaining_original_cohort_cannot_be_replaced_by_new_pending(tmp_path):
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path)
    with pytest.raises(ValueError):stage.load_stage(tmp_path,plan,auth,tasks[4:])


def test_original_cohort_must_not_be_rewritten_for_new_code(tmp_path):
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path)
    Path(auth['dual_stage_reference']['path']).write_text('{}',encoding='utf8')
    with pytest.raises(ValueError):stage.load_stage(tmp_path,plan,auth,tasks[3:])


def test_actual_partial52_boundary_only_releases_original48(tmp_path):
    actual=json.loads((Path(__file__).parent/'fixtures/detail5500_dual_partial52.json').read_text(encoding='utf8'))
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path,actual['consumed'])
    s=stage.load_stage(tmp_path,plan,auth,tasks[52:])
    assert len(s.tasks)==actual['remaining_original_tasks']==48
    for task in s.tasks:s.claim(task)
    assert stage.stage_result(s,{},'auth')['attempted_transactions']==actual['original_transaction_cap']==100
    with pytest.raises(ValueError):s.claim(tasks[100])


def test_prior_raw_tampering_blocks_resume_even_with_unchanged_checkpoint(tmp_path):
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path)
    (tmp_path/'tasks/t0/page.html').write_text('changed',encoding='utf8')
    with pytest.raises(ValueError):stage.load_stage(tmp_path,plan,auth,tasks[3:])


def test_prior_access_stop_cannot_be_reclassified_as_identity_resume(tmp_path):
    plan,auth,doc,tasks,proof,save=resume_fixture(tmp_path)
    rp=tmp_path/'tasks/t2/receipt.json'
    rp.write_text(json.dumps(sealed(dict(task_id='t2',state='CHALLENGE',status_code=200,request_url=tasks[2]['request_url']))),encoding='utf8')
    cp=tmp_path/'tasks/t2/checkpoint.json'
    payload=json.loads(cp.read_text(encoding='utf8'))['payload'];payload['receipt_sha256']=file_sha(rp)
    cp.write_text(json.dumps(sealed(payload)),encoding='utf8')
    proof['completed_transactions'][2]['checkpoint_reference']['sha256']=file_sha(cp);save()
    with pytest.raises(ValueError):stage.load_stage(tmp_path,plan,auth,tasks[3:])
