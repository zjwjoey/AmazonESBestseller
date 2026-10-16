"""Gate must inspect every LIVE failure without decoding all healthy history."""
import json
import pytest
from test_highspeed_store import setup, URL, TIME
from amazon_es_bestseller.highspeed import store as module
from amazon_es_bestseller.multisource.storage import atomic_json,sealed


def save(s,state='NORMAL',origin='LIVE_CAPTURE'):
    req=s.reserve_request('run',{'kind':'PAGE','url':URL,'observed_at':TIME})
    return s.save_response('run',req,{'text':'SAVED_TEST_ONLY','access_state':state,'origin':origin})


def test_healthy_history_not_decoded_to_recheck_gate(tmp_path,monkeypatch):
    s,p,source=setup(tmp_path,max_requests_per_run=30,max_pages_per_run=30,max_requests_per_day=30)
    try:
        for _ in range(20):save(s)
        original=json.loads;decoded=[]
        def loads(*a,**kw):
            value=original(*a,**kw)
            if isinstance(value,dict) and value.get('request_id') and value.get('access_state')=='NORMAL':
                decoded.append(value['request_id'])
            return value
        monkeypatch.setattr(module.json,'loads',loads)
        assert s.get_gate()['state']=='NORMAL'
        assert decoded==[],'Healthy receipt JSON is repeatedly decoded on each send boundary'
    finally:s.close()


@pytest.mark.parametrize('state',['BLOCKED','RATE_LIMITED','CHALLENGE','UNKNOWN','NETWORK_ERROR'])
def test_failure_inserted_after_prior_normal_check_is_immediately_visible(tmp_path,state):
    s,p,source=setup(tmp_path)
    try:
        for _ in range(3):save(s)
        assert s.get_gate()['state']=='NORMAL'
        failure=save(s,state)
        for _ in range(3):save(s)
        gate=s.get_gate()
        assert gate['state']==state and gate['evidence_path']==failure['evidence_path']
        assert gate['recovered_from_saved_receipt'] is True
    finally:s.close()


def test_handled_history_never_hides_a_new_failure_or_gate_file_change(tmp_path):
    s,p,source=setup(tmp_path)
    try:
        failure=save(s,'NETWORK_ERROR')
        atomic_json(s.root/'access_gate.json',sealed({'state':'NORMAL','agent_handled_request_ids':[failure['request_id']]}))
        assert s.get_gate()['state']=='NORMAL'
        newer=save(s,'RATE_LIMITED')
        assert s.get_gate()['evidence_path']==newer['evidence_path']
        atomic_json(s.root/'access_gate.json',sealed({'state':'CHALLENGE','reason':'new external signal'}))
        assert s.get_gate()['state']=='CHALLENGE'
    finally:s.close()


def test_sqlite_update_to_unknown_receipt_is_not_cached_as_normal(tmp_path):
    s,p,source=setup(tmp_path)
    try:
        receipt=save(s)
        assert s.get_gate()['state']=='NORMAL'
        raw=dict(receipt);raw.pop('access_state');raw.pop('text',None);raw.pop('body',None)
        s.db.execute('UPDATE responses SET data_json=? WHERE request_id=?',(json.dumps(raw),receipt['request_id']))
        assert s.get_gate()['state']=='UNKNOWN'
    finally:s.close()

def test_corrupt_non_object_index_value_still_fails_closed(tmp_path):
    s,p,source=setup(tmp_path)
    try:
        receipt=save(s)
        s.db.execute('UPDATE responses SET data_json=? WHERE request_id=?',('[]',receipt['request_id']))
        with pytest.raises((AttributeError,ValueError,TypeError)):
            s.get_gate()
    finally:s.close()
