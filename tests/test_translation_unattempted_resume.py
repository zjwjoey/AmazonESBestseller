import hashlib
import json
from pathlib import Path

import pytest

from amazon_es_bestseller.translation.cache import TranslationCache
from amazon_es_bestseller.translation.resume_admission import ResumeAdmission
from amazon_es_bestseller.translation.service import TranslationService
from amazon_es_bestseller.translation.providers.base import ProviderResponse
from amazon_es_bestseller.translation.pool import ProviderPool, TranslationTask


def write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


class Provider:
    name = 'qwen-mt'
    model = 'fixture'

    def __init__(self):
        self.calls = []

    def translate(self, text, **kwargs):
        self.calls.append(kwargs['context']['canonical_dispatch_key'])
        return ProviderResponse(text='测试文本', provider=self.name, model=self.model)


def setup(tmp_path, structured=False):
    provider = Provider()
    cache = TranslationCache(tmp_path/'cache.json')
    service = TranslationService(provider, cache)
    row = {'asin':'B000000001', 'product_description':'Texto sin cifras'}
    if structured:
        row = {'asin':'B000000001', 'feature_bullets':['Texto primero', 'Texto segundo']}
    keys = service.plan([row])['dispatch_keys']
    attempted = service._memory_key('Texto primero','feature_bullets') if structured else 'prior|es|zh-CN|title|qwen-mt|fixture|0||v1||v1'
    for key in keys:
        cache.claim_memory(key, {'translation_status':'pending'})
    if structured:
        cache.settle_memory(attempted, {'translation_status':'qa_failed','translated_text':'原有候选','candidate_text':'raw-first','qa_status':'qa_failed','qa_issues':[], 'provider':provider.name,'model':provider.model})
        field = 'feature_bullets'
        source = service.selected_fields(row)[0][2]
        from amazon_es_bestseller.translation.service import source_hash
        cache.put(service._field_cache_key(row['asin'],field,source_hash(source)), {'translation_status':'partial','translated_text':'原有候选'})
        cache.save()
    events = []
    for key in set(keys)|{attempted}:
        events.append({'event':'DURABLE_CLAIM_SAVED','canonical_dispatch_key':key})
    for typ in ('PROVIDER_ENTRY','HTTP_ATTEMPT_BEFORE_POST','HTTP_RESULT','PROVIDER_RESULT'):
        events.append({'event':typ,'canonical_dispatch_key':attempted,'status_code':200})
    events.append({'event':'CANARY_PROCESS_FINISHED','exit_code':0})
    ledger=tmp_path/'events.jsonl'
    ledger.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
    bindings={'ledger':{'path':str(ledger),'sha256':hashlib.sha256(ledger.read_bytes()).hexdigest()},'cache':{'path':str(cache.path),'sha256':hashlib.sha256(cache.path.read_bytes()).hexdigest()}}
    for name in ('products','source_config','source_manifest'):
        bindings[name]=write(tmp_path/(name+'.json'),[row] if name=='products' else {})
    admission_path=tmp_path/'admission.json'
    binding=write(admission_path,{'version':'unattempted-resume-v1','bindings':bindings})
    cache.resume_admission=ResumeAdmission.from_file(binding,cache_path=cache.path)
    return provider,cache,service,row,keys,attempted,binding


def test_unattempted_plan_claim_and_resume_once(tmp_path):
    provider,cache,service,row,keys,_,_=setup(tmp_path)
    assert service.plan([row])['dispatch_keys']==keys
    service.translate_records([row])
    service.translate_records([row])
    assert provider.calls==keys
    assert not service.plan([row])['dispatch_keys']
    assert cache.memory[keys[0]]['resume_prior_claim']['translation_status']=='pending'


def test_structured_parent_preserves_attempted_raw_candidate(tmp_path):
    provider,cache,service,row,keys,attempted,_=setup(tmp_path,True)
    assert set(service.plan([row])['dispatch_keys'])==set(keys)-{attempted}
    result=service.translate_records([row])['records'][row['asin']]['fields']['feature_bullets_zh']
    assert result['items'][0]['candidate_text']=='raw-first'
    assert provider.calls==[k for k in keys if k!=attempted]
    assert cache.memory[attempted]['candidate_text']=='raw-first'


@pytest.mark.parametrize('name',['ledger','cache','products','source_config','source_manifest'])
def test_hash_tamper_rejected(tmp_path,name):
    *_,binding=setup(tmp_path)
    data=json.loads(Path(binding['path']).read_text(encoding='utf-8'))
    Path(data['bindings'][name]['path']).write_text('tampered',encoding='utf-8')
    with pytest.raises(ValueError,match='HASH'):
        ResumeAdmission.from_file(binding,cache_path=tmp_path/'cache.json')


@pytest.mark.parametrize('status',['success','failed','qa_failed','pending'])
def test_attempted_or_unknown_never_reclaimed(tmp_path,status):
    _,cache,_,_,keys,_,_=setup(tmp_path)
    key=keys[0]
    cache.memory[key]['translation_status']=status
    cache.memory[key]['resume_admission_id']='already-claimed-before-possible-send'
    cache.save()
    assert not cache.claim_memory(key,{'translation_status':'pending'})[0]


def test_first429_persistent_halt_and_send_health_recheck(tmp_path):
    class Limited:
        name='qwen-mt'
        model='fixture'
        calls=0
        def translate(self,*args,**kwargs):
            self.calls+=1
            return ProviderResponse(status='failed',error='HTTP 429 limit_requests',attempts=1)
    provider=Limited()
    halt=tmp_path/'halt.json'
    pool=ProviderPool({'A':provider},failover=False,stop_on_rate_limit=True,halt_path=halt)
    task=TranslationTask.from_values('one',asin='A',field='title')
    pool._call_one(task,'A')
    second=pool._call_one(TranslationTask.from_values('two',asin='B',field='title'),'A')
    assert second.response.attempts==0 and provider.calls==1 and halt.exists()
    fresh=ProviderPool({'A':provider},failover=False,stop_on_rate_limit=True,halt_path=halt)
    fresh.submit([task])
    assert provider.calls==1


def test_atomic_claim_one_winner_and_namespace_drift_rejected(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    _,cache,_,_,keys,_,_=setup(tmp_path)
    key=keys[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        winners=list(pool.map(lambda _:cache.claim_memory(key,{'translation_status':'pending'})[0],range(2)))
    assert sum(winners)==1
    assert not cache.claim_memory(key+'|changed',{'translation_status':'pending'})[0]


def test_unknown_provider_entry_without_response_excluded(tmp_path):
    _,cache,_,_,keys,_,binding=setup(tmp_path)
    data=json.loads(Path(binding['path']).read_text(encoding='utf-8'))
    ledger=Path(data['bindings']['ledger']['path'])
    events=[json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
    events.insert(-1,{'event':'PROVIDER_ENTRY','canonical_dispatch_key':keys[0]})
    events.insert(-1,{'event':'HTTP_ATTEMPT_BEFORE_POST','canonical_dispatch_key':keys[0]})
    ledger.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
    data['bindings']['ledger']['sha256']=hashlib.sha256(ledger.read_bytes()).hexdigest()
    binding=write(Path(binding['path']),data)
    cache.resume_admission=ResumeAdmission.from_file(binding,cache_path=cache.path)
    assert not cache.resume_eligible(keys[0])
    assert not cache.claim_memory(keys[0],{'translation_status':'pending'})[0]


def test_old_settlement_without_claim_id_uses_full_envelope_binding(tmp_path):
    _,cache,_,_,keys,_,binding=setup(tmp_path)
    cache.memory[keys[0]].pop('claim_id')
    cache.save()
    data=json.loads(Path(binding['path']).read_text(encoding='utf-8'))
    data['bindings']['cache']['sha256']=hashlib.sha256(cache.path.read_bytes()).hexdigest()
    binding=write(Path(binding['path']),data)
    cache.resume_admission=ResumeAdmission.from_file(binding,cache_path=cache.path)
    assert cache.resume_eligible(keys[0])
    assert cache.claim_memory(keys[0],{'translation_status':'pending'})[0]
    assert not cache.claim_memory(keys[0],{'translation_status':'pending'})[0]
