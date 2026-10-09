import hashlib
import json
import os
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.abnormal_exit_admission import AbnormalExitAdmission
from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.service import TranslationService
from amazon_es_bestseller.translation.providers.base import ProviderResponse


def bound(path, value=None, *, lines=False):
    if value is not None:
        path.write_text('\n'.join(json.dumps(x) for x in value)+'\n' if lines else json.dumps(value), encoding='utf-8')
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


class FixtureProvider:
    name='qwen-mt'
    model='fixture'
    def __init__(self):self.calls=[]
    def translate(self,text,**kwargs):
        self.calls.append(kwargs['context']['canonical_dispatch_key'])
        return ProviderResponse(text='中文说明',provider=self.name,model=self.model)


def setup(tmp_path, mode='EVIDENCE_ONLY', envelope=False):
    provider=FixtureProvider();cache=TranslationCache(tmp_path/'cache.json');service=TranslationService(provider,cache)
    rows=[{'asin':f'B00000000{i}','product_description':text} for i,text in enumerate(
        ['Texto enviado','Texto nunca reservado','Texto incierto','Texto reservado','Texto solo cache'],1)]
    keys=service.plan(rows,fields=['product_description'])['dispatch_keys']
    keys=[service._memory_key(row['product_description'],'product_description') for row in rows]
    sent,never,unknown,claimed,cacheonly=keys
    old_key='historical|es|zh-CN|title|qwen-mt|fixture|0||v1||v1'
    cache.put_memory(old_key,{'translation_status':'success','candidate_text':'immutable old raw'})
    cache.save()
    baseline_path=tmp_path/'baseline.json';baseline_path.write_bytes(cache.path.read_bytes())
    baseline=bound(baseline_path)
    extension=bound(tmp_path/'extension.json',{'parent_cache_snapshot':baseline})
    for k in [sent,unknown,claimed,cacheonly]:cache.claim_memory(k,{'translation_status':'pending'})
    cache.settle_memory(sent,{'translation_status':'qa_failed','candidate_text':'first raw',
                             'translated_text':'first raw','qa_status':'qa_failed','qa_issues':[]})
    rawdir=tmp_path/'responses';rawdir.mkdir()
    raw=rawdir/'response.json';raw_binding=bound(raw,{'canonical_dispatch_key':sent,'alias':'QWEN_A',
        'response':{'status_code':200,'body':{'text':'first raw'}}})
    events=[{'event':'DURABLE_CLAIM_SAVED','canonical_dispatch_key':k} for k in [sent,unknown,claimed]]
    events += [{'event':'PROVIDER_ENTRY','canonical_dispatch_key':k} for k in [sent,unknown]]
    events += [{'event':'HTTP_ATTEMPT_BEFORE_POST','canonical_dispatch_key':sent},
               {'event':'HTTP_RESULT','canonical_dispatch_key':sent,'alias':'QWEN_A','status_code':200,'response_path':str(raw)},
               {'event':'RUN_EXCEPTION','error':'fixture abnormal exit'}]
    products=bound(tmp_path/'products.json',{'records':rows} if envelope else rows)
    config=bound(tmp_path/'config.json',{'fields':['product_description']})
    manifest=bound(tmp_path/'manifest.json',{'products':products,'run_cache_path':str(cache.path),
        'extension_authorization':extension,
        'service_contract':{'schema_version':service.schema_version,'prompt_version':'v1'},
        'provider_identity':{'name':'qwen-mt','model':'fixture'}})
    plan=bound(tmp_path/'plan.json',{'dispatch_keys':keys})
    runner=bound(tmp_path/'runner.json',{'fixture':'audited sender'})
    old_ledger=bound(tmp_path/'old_events.jsonl',[{'event':'HTTP_ATTEMPT_BEFORE_POST','canonical_dispatch_key':old_key}],lines=True)
    launch=bound(tmp_path/'launch.json',{'configuration':config,'manifest':manifest,'runner':runner,'old_ledgers':[old_ledger],
        'code_sha':'fixture-sha',
        'plan_file_hash':plan['sha256'],'maximum_new_unique_http_keys':len(keys)})
    prior=bound(tmp_path/'old.json',{'unique_attempt_keys':[old_key]})
    pid=2147483000
    process_record=bound(tmp_path/'background.json',{'pid':pid,'code_sha':'fixture-sha','runner':runner})
    exit_record=bound(tmp_path/'exit.json',{'observed_pids':[pid], 'exit_codes':{str(pid):1},
        'observed_at':'2026-10-09T12:00:00+00:00','source_identity':launch['sha256']})
    source_lock=tmp_path/'process_startup.lock';source_lock.write_bytes(b'0')
    claim_lock=cache.path.with_name(cache.path.name+'.claim.lock')
    data={'version':'abnormal-exit-resume-v2','mode':mode,'source_identity':launch['sha256'],
        'source_pids':[pid],'source_cache_path':str(cache.path),'source_lock':str(source_lock),
        'claim_lock':str(claim_lock),'audited_send_order':'claim-fsynced-entry-fsynced-send-fsynced-before-post-v1',
        'bindings':{'ledger':bound(tmp_path/'events.jsonl',events,lines=True),'cache':bound(cache.path),
            'products':products,'source_config':config,'source_manifest':manifest,'plan':plan,
            'source_runner':runner,'launch_admission':launch,'old_attempts':prior,'exit_observation':exit_record,
            'baseline_cache':baseline,'source_process_record':process_record},
        'responses':[{'source_path':str(raw),'file':raw_binding}],'cache_artifacts':[]}
    admission_path=tmp_path/'admission.json';binding=bound(admission_path,data)
    return provider,cache,service,rows,keys,data,binding


def admit(tmp_path,mode='EVIDENCE_ONLY'):
    values=setup(tmp_path,mode);provider,cache,service,rows,keys,data,binding=values
    admission=AbnormalExitAdmission.from_file(binding,cache_path=cache.path,products_path=data['bindings']['products']['path'])
    return (*values,admission)


def test_abnormal_without_fabricated_exit0_admits_only_two_safe_keys(tmp_path):
    *values,a=admit(tmp_path);keys=values[4]
    assert a.unclaimed_keys=={keys[1]}
    assert set(a.claim_fingerprints)=={keys[3]}
    assert a.excluded >= {'|'.join(k.split('|')[:6]) for k in [keys[0],keys[2],keys[4]]}
    assert a.launch_authorized is False


@pytest.mark.parametrize('name',['ledger','cache','products','source_config','source_manifest','plan',
                                 'source_runner','launch_admission','old_attempts','exit_observation','baseline_cache','source_process_record'])
def test_hash_change_rejected(tmp_path,name):
    *_,data,binding=setup(tmp_path)
    Path(data['bindings'][name]['path']).write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='HASH'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_real_live_pid_blocks_even_claimed_exit_record(tmp_path):
    *_,data,binding=setup(tmp_path);data['source_pids']=[os.getpid()]
    data['bindings']['exit_observation']=bound(tmp_path/'exit.json',{
        'observed_pids':[os.getpid()],'exit_codes':{str(os.getpid()):1},'observed_at':'fixture','source_identity':data['source_identity']})
    binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='PROCESS_ALIVE'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


@pytest.mark.parametrize('name',['source_lock','claim_lock'])
def test_both_os_locks_must_be_released(tmp_path,name):
    from amazon_es_bestseller.translation.abnormal_exit_admission import _released_lock
    *_,data,binding=setup(tmp_path)
    with _released_lock(data[name]):
        with pytest.raises(ValueError,match='LOCK_HELD'):
            AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_missing_raw_response_and_changed_raw_response_rejected(tmp_path):
    *_,data,binding=setup(tmp_path);Path(data['responses'][0]['file']['path']).unlink()
    with pytest.raises(ValueError,match='RESPONSE'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_simulation_can_plan_but_never_authorize_claim(tmp_path):
    provider,cache,service,rows,keys,data,binding,a=admit(tmp_path,'SIMULATION_ONLY')
    assert not a.allows(keys[1],None)
    cache.resume_admission=a
    assert not cache.claim_memory(keys[1],{'translation_status':'pending'})[0]
    result=a.simulate_plan(provider)
    assert result['execution_permitted'] is False
    assert set(result['plan']['dispatch_keys'])=={keys[1],keys[3]}
    assert not provider.calls


def test_execute_safe_units_once_preserves_prior_raw_and_second_run_zero_calls(tmp_path):
    provider,cache,service,rows,keys,data,binding,a=admit(tmp_path)
    cache.resume_admission=a
    raw_before=dict(cache.memory[keys[0]])
    assert set(service.plan(rows,fields=['product_description'])['dispatch_keys'])=={keys[1],keys[3]}
    service.translate_records(rows,fields=['product_description'])
    first=list(provider.calls)
    service.translate_records(rows,fields=['product_description'])
    assert first==provider.calls and set(first)=={keys[1],keys[3]}
    assert cache.memory[keys[0]]==raw_before


def test_truncated_ledger_and_invalid_cache_are_not_empty_fallback(tmp_path):
    *_,data,binding=setup(tmp_path)
    Path(data['bindings']['ledger']['path']).write_text('{"event":',encoding='utf-8')
    data['bindings']['ledger']=bound(Path(data['bindings']['ledger']['path']))
    binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='LEDGER_INVALID'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_stable_cache_footprint_and_changed_baseline_are_excluded(tmp_path):
    *_,keys,data,binding=setup(tmp_path)
    path=Path(data['bindings']['cache']['path']);payload=json.loads(path.read_text(encoding='utf-8'))
    payload['memory'][keys[1]+'|namespace-drift']={'translation_status':'pending'}
    data['bindings']['cache']=bound(path,payload);binding=bound(Path(binding['path']),data)
    a=AbnormalExitAdmission.from_file(binding,cache_path=path)
    assert keys[1] not in a.unclaimed_keys
    oldkey=next(k for k in payload['memory'] if k.startswith('historical|'))
    payload['memory'][oldkey]['candidate_text']='lost old raw'
    data['bindings']['cache']=bound(path,payload);binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='PRIOR_RAW_CACHE_CHANGED_OR_LOST'):
        AbnormalExitAdmission.from_file(binding,cache_path=path)


def test_response_binding_and_prior_exclusion_set_cannot_be_weakened(tmp_path):
    *_,data,binding=setup(tmp_path)
    rawpath=Path(data['responses'][0]['file']['path']);raw=json.loads(rawpath.read_text(encoding='utf-8'))
    raw['alias']='QWEN_B';data['responses'][0]['file']=bound(rawpath,raw);binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='RESPONSE_BINDING'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_cache_corruption_rejected_without_moving_original(tmp_path):
    *_,data,binding=setup(tmp_path);path=tmp_path/'cache.json';path.write_text('{bad',encoding='utf-8')
    data['bindings']['cache']=bound(path);binding=bound(Path(binding['path']),data)
    before=path.read_bytes()
    with pytest.raises(ValueError,match='INVALID_JSON'):
        AbnormalExitAdmission.from_file(binding,cache_path=path)
    assert path.read_bytes()==before and not list(tmp_path.glob('cache.json.corrupt-*'))


def test_prior_attempt_exclusions_must_match_original_bound_ledgers(tmp_path):
    *_,data,binding=setup(tmp_path)
    data['bindings']['old_attempts']=bound(tmp_path/'old.json',{'unique_attempt_keys':[]})
    binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='PRIOR_ATTEMPT_EXCLUSIONS_MISMATCH'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_source_still_active_exit_code_is_not_exit_proof(tmp_path):
    *_,data,binding=setup(tmp_path);path=tmp_path/'exit.json';value=json.loads(path.read_text(encoding='utf-8'))
    value['exit_codes']={str(data['source_pids'][0]):259}
    data['bindings']['exit_observation']=bound(path,value);binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='EXIT_NOT_OBSERVED'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_old_admission_reuse_after_settlement_rejected_before_any_new_call(tmp_path):
    provider,cache,service,rows,keys,data,binding,a=admit(tmp_path)
    cache.resume_admission=a;service.translate_records(rows,fields=['product_description']);before=list(provider.calls)
    with pytest.raises(ValueError,match='HASH'):
        AbnormalExitAdmission.from_file(binding,cache_path=cache.path)
    assert provider.calls==before


def test_actual_cli_records_envelope_simulates_without_writing(tmp_path):
    provider,cache,service,rows,keys,data,binding=setup(tmp_path,'SIMULATION_ONLY',envelope=True)
    before=cache.path.read_bytes()
    a=AbnormalExitAdmission.from_file(binding,cache_path=cache.path)
    report=a.simulate_plan(provider)
    assert set(report['plan']['dispatch_keys'])=={keys[1],keys[3]}
    assert cache.path.read_bytes()==before and not provider.calls


def test_orphan_cache_temp_requires_complete_inventory_and_unknown_tail_blocks(tmp_path):
    *_,keys,data,binding=setup(tmp_path)
    temp=tmp_path/'cache.json.tmp-orphan';temp.write_text('{incomplete',encoding='utf-8')
    with pytest.raises(ValueError,match='CACHE_ARTIFACT_INVENTORY'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')
    data['cache_artifacts']=[{'source_path':str(temp),'file':bound(temp)}]
    binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='CACHE_ARTIFACT'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_valid_orphan_cache_footprint_holds_never_claimed_key(tmp_path):
    *_,keys,data,binding=setup(tmp_path)
    temp=tmp_path/'cache.json.tmp-orphan'
    artifact={'entries':{},'results':{},'memory':{keys[1]:{'translation_status':'pending'}},'memory_results':{}}
    data['cache_artifacts']=[{'source_path':str(temp),'file':bound(temp,artifact)}]
    binding=bound(Path(binding['path']),data)
    assert keys[1] not in AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json').unclaimed_keys


def test_source_identity_and_complete_raw_inventory_required(tmp_path):
    *_,data,binding=setup(tmp_path)
    data['source_identity']='wrong-source';binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='SOURCE_IDENTITY_BINDING'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')
    data['source_identity']=data['bindings']['launch_admission']['sha256']
    extra=tmp_path/'responses'/'extra.json';bound(extra,{'canonical_dispatch_key':'unknown raw'})
    binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='RESPONSE_INVENTORY'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_orphan_raw_response_temp_cannot_be_ignored(tmp_path):
    *_,data,binding=setup(tmp_path)
    (tmp_path/'responses'/'unfinished.json.tmp').write_text('{partial',encoding='utf-8')
    with pytest.raises(ValueError,match='RESPONSE_TEMP_UNCERTAIN'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


@pytest.mark.parametrize('kind',['response','cache_artifact'])
def test_evidence_copy_must_match_actual_source_bytes(tmp_path,kind):
    *_,data,binding=setup(tmp_path)
    if kind=='response':
        item=data['responses'][0]
        source=Path(item['source_path'])
    else:
        source=tmp_path/'cache.json.tmp-orphan'
        item={'source_path':str(source),'file':bound(source,{
            'entries':{},'results':{},'memory':{},'memory_results':{}})}
        data['cache_artifacts']=[item]
    copy=tmp_path/'evidence_copy.json';copy.write_bytes(source.read_bytes())
    item['file']=bound(copy)
    source.write_text('{}',encoding='utf-8')
    binding=bound(Path(binding['path']),data)
    with pytest.raises(ValueError,match='SOURCE_HASH_MISMATCH'):
        AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


def test_sent_without_response_is_held_and_counted_unknown(tmp_path):
    *_,keys,data,binding=setup(tmp_path)
    ledger=Path(data['bindings']['ledger']['path'])
    events=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
    events.append({'event':'HTTP_ATTEMPT_BEFORE_POST','canonical_dispatch_key':keys[2]})
    data['bindings']['ledger']=bound(ledger,events,lines=True)
    binding=bound(Path(binding['path']),data)
    a=AbnormalExitAdmission.from_file(binding,cache_path=tmp_path/'cache.json')
    assert a.unknown_sent=={keys[2]}
    assert not a.allows(keys[2],a.snapshot['memory'][keys[2]])
