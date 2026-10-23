from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from amazon_es_bestseller.collection.shared_detail_queue import SharedDetailQueue

def tasks(n=30):return [{"asin":f"B{i:09d}","candidate_ids":[str(i)]} for i in range(n)]

def test_three_workers_claim_each_uncollected_asin_once(tmp_path):
 success={"B000000000"};q=SharedDetailQueue(tmp_path,tasks(),is_success=lambda a:a in success,blocked=["B000000001"])
 def drain(worker):
  got=[]
  while (task:=q.claim(worker)) is not None:got.append(task["asin"])
  return got
 with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(drain,[1,2,3]))
 flat=sum(results,[]);assert len(flat)==len(set(flat))==28
 assert not success.intersection(flat) and "B000000001" not in flat

def test_stopped_claim_is_not_transferred_and_success_not_refetched_on_restart(tmp_path):
 good=set();q=SharedDetailQueue(tmp_path,tasks(3),is_success=lambda a:a in good)
 a=q.claim(1);q.finish(a["asin"],1,"ACCESS_STOP")
 b=q.claim(2);good.add(b["asin"]);q.finish(b["asin"],2,"SUCCESS")
 resumed=SharedDetailQueue(tmp_path,tasks(3),is_success=lambda a:a in good)
 assert resumed.claim(3)["asin"]=="B000000002"
 assert resumed.claim(3) is None

def test_queue_cannot_emit_fake_success_or_change_owner(tmp_path):
 q=SharedDetailQueue(tmp_path,tasks(1),is_success=lambda a:False);t=q.claim(1)
 with pytest.raises(ValueError,match="SUCCESS_EVIDENCE"):q.finish(t["asin"],1,"SUCCESS")
 with pytest.raises(ValueError,match="OWNER"):q.finish(t["asin"],2,"FAILED")

def test_changed_plan_or_duplicate_asin_is_rejected(tmp_path):
 SharedDetailQueue(tmp_path,tasks(2),is_success=lambda a:False)
 with pytest.raises(ValueError,match="FROZEN"):SharedDetailQueue(tmp_path,tasks(3),is_success=lambda a:False)
 with pytest.raises(ValueError,match="DUPLICATE"):SharedDetailQueue(tmp_path,[tasks(1)[0]]*2,is_success=lambda a:False)
