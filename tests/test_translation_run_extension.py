from pathlib import Path
import json
import hashlib
from copy import deepcopy

import pytest

from amazon_es_bestseller.translation.run_context import TranslationRunContext, build_run_cache_snapshot
from amazon_es_bestseller.translation.preclean import audit_records
from amazon_es_bestseller.translation.production import build_production_input, records_for_preclean
from amazon_es_bestseller.orchestration.translation_batch import load_source_candidate
from amazon_es_bestseller.translation.service import TranslationService
from test_translation_run_context import setup_run, save, FakeProvider, invoke


def extension(tmp_path, monkeypatch):
    products, config, parent, rows = setup_run(tmp_path)
    parent_ref = json.loads(config.read_text(encoding='utf-8'))['run_context']
    context = TranslationRunContext(parent_ref, products_path=products, products=rows, cache_path=parent['run_cache_path'])
    context.prepare_execution()
    loaded = load_source_candidate(parent['source_manifest']['path'], manifest_hash=parent['source_manifest']['sha256'], available_asins=parent['available_asins'])
    # Add a second canonical fixture record to the test loader's bound dataset.
    new_record = deepcopy(loaded['records'][0]); new_record['asin']='B07F6LYVT6'
    loaded['records'].append(new_record)
    monkeypatch.setattr('amazon_es_bestseller.translation.run_context.load_source_candidate', lambda *args, **kwargs: loaded)
    child_rows = audit_records(records_for_preclean(build_production_input([r for r in loaded['records'] if r['asin']=='B07F6LYVT6'])))['translation_input_records']
    child_products = tmp_path/'child_products.json'
    child_ref = save(child_products, {'records':child_rows})
    service = TranslationService(FakeProvider(), context.cache, **parent['service_contract'])
    old_key = service._memory_key('Textura fina','feature_bullets')
    ledger = tmp_path/'ledger.jsonl'
    ledger.write_text(json.dumps({'event':'PROVIDER_ENTRY','canonical_dispatch_key':old_key,'asin':'B000000020'})+'\n'+json.dumps({'event':'CANARY_PROCESS_FINISHED','exit_code':0})+'\n')
    from amazon_es_bestseller.orchestration.translation_batch import file_hash
    authority = {'version':'translation-run-extension-v1','parent':parent_ref,'products':child_ref,'source_manifest':parent['source_manifest'],'selected_asins':['B07F6LYVT6'],'parent_cache_snapshot':save(tmp_path/'parent_cache_evidence.json',json.loads(context.cache.path.read_text(encoding='utf-8'))), 'parent_result':save(tmp_path/'parent_result.json',{'B000000020':{'asin':'B000000020','fields':{e['target_field']:e for e in context.references},'translation_status':'partial'}}),'expected_attempted_units':1,'attempt_ledgers':[{'path':str(ledger),'sha256':file_hash(ledger)}]}
    child = deepcopy(parent)
    child.update(products=child_ref,selected_asins=['B07F6LYVT6'],extension_authorization=save(tmp_path/'authorization.json',authority))
    child['snapshot']=build_run_cache_snapshot(parent['historical_caches'],child_rows,service,tmp_path/'child_snapshot.json')
    child_config=tmp_path/'child_config.json'
    save(child_config,{'fields':child['fields'],'run_context':save(tmp_path/'child_run.json',child)})
    return child_products,child_config,child,child_rows,context,old_key


def test_same_source_increment_dryrun_claim_and_merge_preserve_parent(tmp_path,monkeypatch):
    products,config,manifest,rows,parent,old_key=extension(tmp_path, monkeypatch)
    before=parent.cache.path.read_bytes();marker=parent.marker.read_bytes()
    assert invoke(products,config,manifest,tmp_path/'plan.json',dry=True)==0
    plan=json.loads((tmp_path/'plan.json').read_text(encoding='utf-8'))
    assert plan['total_records']==1 and plan['cumulative_records']==2
    assert old_key not in plan['dispatch_keys']
    child=TranslationRunContext(json.loads(config.read_text(encoding='utf-8'))['run_context'],products_path=products,products=rows,cache_path=parent.cache.path)
    assert child.cache.claim_memory(old_key.replace('|v1','|changed'),{'translation_status':'pending'})[0] is False
    child.prepare_execution()
    assert parent.marker.read_bytes()==marker and parent.cache.path.read_bytes()==before
    assert child.extension_marker.exists()
    output={'records':{'B07F6LYVT6':{'asin':'B07F6LYVT6','fields':{},'translation_status':'pending'}}}
    child.apply_result(output,rows)
    assert set(output['records'])=={'B07F6LYVT6','B000000020'}
    from amazon_es_bestseller.translation.providers import qwen_mt
    monkeypatch.setattr(qwen_mt, 'QwenMTProvider', FakeProvider)
    FakeProvider.calls=[]
    assert invoke(products,config,manifest,tmp_path/'live_fixture.json')==0
    assert all(asin=='B07F6LYVT6' and field!='feature_bullets' for asin,field,text in FakeProvider.calls)
    assert set(json.loads((tmp_path/'live_fixture.json').read_text(encoding='utf-8')))=={'B07F6LYVT6','B000000020'}
    prior_calls=len(FakeProvider.calls)
    assert invoke(products,config,manifest,tmp_path/'resumed_fixture.json')==0
    assert len(FakeProvider.calls)==prior_calls
    assert parent.marker.read_bytes()==marker
    assert len(child.extension_marker.read_text(encoding='utf-8').splitlines())==1


@pytest.mark.parametrize('damage',['source','parent_ref','scope','cache_evidence','parent_result','ledger','reference','raw_source','old_cache_record','foreign_ledger'])
def test_extension_tampering_rejected(tmp_path,monkeypatch,damage):
    products,config,manifest,rows,parent,key=extension(tmp_path, monkeypatch)
    a=json.loads(Path(manifest['extension_authorization']['path']).read_text(encoding='utf-8'))
    if damage=='source':a['source_manifest']['sha256']='bad'
    elif damage=='parent_ref':a['parent']['sha256']='bad'
    elif damage=='scope':a['selected_asins']=['B000000020']
    elif damage=='cache_evidence':a['parent_cache_snapshot']['sha256']='bad'
    elif damage=='parent_result':a['parent_result']['sha256']='bad'
    elif damage=='ledger':a['expected_attempted_units']=2
    elif damage=='raw_source':
        rows[0]['raw_fields']['title_es_raw']['source_hash']='bad'
        a['products']=save(products,{'records':rows})
        manifest['products']=a['products']
    elif damage=='old_cache_record':
        data=json.loads(parent.cache.path.read_text(encoding='utf-8'))
        next(iter(data['memory'].values()))['translated_text']='tampered'
        save(parent.cache.path,data)
    elif damage=='foreign_ledger':
        ledger=tmp_path/'foreign.jsonl'
        ledger.write_text(json.dumps({'event':'PROVIDER_ENTRY','canonical_dispatch_key':key,'asin':'B07F6LYVT6'})+'\n'+json.dumps({'event':'CANARY_PROCESS_FINISHED','exit_code':0})+'\n',encoding='utf-8')
        from amazon_es_bestseller.orchestration.translation_batch import file_hash
        a['attempt_ledgers']=[{'path':str(ledger),'sha256':file_hash(ledger)}]
    else:
        result=json.loads(Path(a['parent_result']['path']).read_text(encoding='utf-8'))
        result['B000000020']['fields']={}
        a['parent_result']=save(tmp_path/'parent_result.json',result)
    manifest['extension_authorization']=save(tmp_path/'authorization.json',a)
    save(config,{'fields':manifest['fields'],'run_context':save(tmp_path/'child_run.json',manifest)})
    with pytest.raises(ValueError):invoke(products,config,manifest,tmp_path/'plan.json',dry=True)


@pytest.mark.parametrize('damage', [None, 'unregistered', 'overlap', 'foreign_result'])
def test_second_increment_binds_completed_child_and_both_ledgers(tmp_path, monkeypatch, damage):
    products, config, child_manifest, rows, parent, old_key = extension(tmp_path, monkeypatch)
    child_reference = json.loads(config.read_text(encoding='utf-8'))['run_context']
    child = TranslationRunContext(child_reference, products_path=products, products=rows, cache_path=parent.cache.path)
    if damage != 'unregistered':
        child.prepare_execution()
    loaded = load_source_candidate(parent.manifest['source_manifest']['path'], manifest_hash=parent.manifest['source_manifest']['sha256'], available_asins=parent.manifest['available_asins'])
    prior_record = deepcopy(loaded['records'][0]); prior_record['asin'] = rows[0]['asin']
    loaded['records'].append(prior_record)
    record = deepcopy(loaded['records'][0]); record['asin'] = 'B07F6LYVT7'
    loaded['records'].append(record)
    monkeypatch.setattr('amazon_es_bestseller.translation.run_context.load_source_candidate', lambda *a, **k: loaded)
    new_rows = audit_records(records_for_preclean(build_production_input([record])))['translation_input_records']
    new_products = tmp_path / 'second_products.json'
    new_binding = save(new_products, {'records': new_rows})
    authority = json.loads(Path(child_manifest['extension_authorization']['path']).read_text(encoding='utf-8'))
    result = json.loads(Path(authority['parent_result']['path']).read_text(encoding='utf-8'))
    result[rows[0]['asin']] = {'asin': rows[0]['asin'], 'fields': {}, 'translation_status': 'partial'}
    if damage == 'foreign_result':
        result['FOREIGN'] = {}
    ledger = tmp_path / 'child_ledger.jsonl'
    new_key = old_key.replace(old_key.split('|')[0], 'child-source-hash')
    ledger.write_text(json.dumps({'event': 'PROVIDER_ENTRY', 'canonical_dispatch_key': new_key, 'asin': rows[0]['asin']}) + '\n' + json.dumps({'event': 'CANARY_PROCESS_FINISHED', 'exit_code': 0}) + '\n', encoding='utf-8')
    authority.update(products=new_binding, selected_asins=[record['asin']], prior_completed_runs=[child_reference], parent_result=save(tmp_path/'cumulative_result.json', result), expected_attempted_units=2)
    authority['attempt_ledgers'].append({'path': str(ledger), 'sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()})
    second = deepcopy(child_manifest)
    if damage == 'overlap':
        new_rows = rows
        new_binding = save(new_products, {'records': new_rows})
        authority.update(products=new_binding, selected_asins=[rows[0]['asin']])
    second.update(products=new_binding, selected_asins=authority['selected_asins'], extension_authorization=save(tmp_path/'second_authority.json', authority))
    service = TranslationService(FakeProvider(), parent.cache, **second['service_contract'])
    second['snapshot'] = build_run_cache_snapshot(second['historical_caches'], new_rows, service, tmp_path/'second_snapshot.json')
    reference = save(tmp_path/'second_manifest.json', second)
    if damage:
        with pytest.raises(ValueError):
            TranslationRunContext(reference, products_path=new_products, products=new_rows, cache_path=parent.cache.path)
    else:
        context = TranslationRunContext(reference, products_path=new_products, products=new_rows, cache_path=parent.cache.path)
        assert set(context.parent_result) == {'B000000020', 'B07F6LYVT6'}
        assert len(context.excluded_attempts) == 2
        assert context.cache.claim_memory(new_key.replace('|v1', '|changed'), {'translation_status': 'pending'})[0] is False

