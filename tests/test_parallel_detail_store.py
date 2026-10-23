import pytest
import json
from amazon_es_bestseller.collection.parallel_detail_store import ParallelDetailStore


TASK={'task_id':'task1','candidate_id':'candidate1','selection_id':'selection1',
      'asin':'B000000001','request_url':'https://www.amazon.es/dp/B000000001'}


def store(root,budget=10):
    return ParallelDetailStore(root,{'run_id':'run','tasks':[TASK]},
                               {'max_requests':budget,'prior_requests':2,'budget_holds':3})


def test_terminal_evidence_is_immutable_and_success_never_replayed(tmp_path):
    s=store(tmp_path);s.reserve(TASK,0)
    refs=s.save_capture(TASK,b'<html>evidence</html>',{'status_code':200})
    s.finish(TASK,{'status':'SUCCESS','record':{'asin':TASK['asin']},**refs})
    assert store(tmp_path).pending()==[]
    assert s.used_requests==6
    with pytest.raises(ValueError):s.reserve(TASK,1)
    with pytest.raises(ValueError):s.finish(TASK,{'status':'PARSE_FAILED'})


def test_corrupt_saved_evidence_stops_resume(tmp_path):
    s=store(tmp_path);s.reserve(TASK,0)
    refs=s.save_capture(TASK,b'original',{'status_code':200})
    s.finish(TASK,{'status':'SUCCESS',**refs})
    (tmp_path/refs['html_path']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='EVIDENCE'):store(tmp_path).pending()


def test_budget_and_contract_cannot_reset_on_restart(tmp_path):
    s=store(tmp_path,budget=6);s.reserve(TASK,0)
    assert s.used_requests==6
    with pytest.raises(ValueError):store(tmp_path,budget=7)


def test_reserved_task_never_silently_retried_after_crash(tmp_path):
    store(tmp_path).reserve(TASK,0)
    with pytest.raises(ValueError,match='UNRESOLVED_RESERVATION'):store(tmp_path).pending()


def test_legacy_success_import_keeps_original_bytes_and_budget(tmp_path):
    (tmp_path/'checkpoints').mkdir();(tmp_path/'html').mkdir()
    cp=tmp_path/'checkpoints'/('B000000001.json')
    record={'asin':'B000000001','title_es_raw':'Producto','detail_parser_contract_version':1,
            'identity_status':'MATCH','resolved_asin':'B000000001'}
    body=json.dumps({'status':'success','record':record}).encode()
    cp.write_bytes(body)
    (tmp_path/'html'/'B000000001.html').write_text('original',encoding='utf8')
    s=store(tmp_path)
    assert s.import_legacy_successes()==1
    assert s.pending()==[] and s.used_requests==5
    assert cp.read_bytes()==body
    assert s.outcomes()[0]['result']['record']==record
    cp.write_bytes(body+b' ')
    with pytest.raises(ValueError,match='EVIDENCE'):s.pending()
