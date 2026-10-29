import hashlib,json
import pytest
from amazon_es_bestseller.multisource.storage import digest,sealed
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_alias_review import apply_alias_reviews


def test_reviewed_alias_proofs_are_bound_and_idempotent(tmp_path):
    plan={'run_id':'TEST_ONLY'};ph=digest(plan)
    q=ParallelPageLedger(tmp_path/'q.sqlite',run_id=plan['run_id'],original_plan_sha256=ph,deadline=1000,page_budget=5)
    urls=['https://www.amazon.es/gp/bestsellers/kitchen/1/ref=a','https://www.amazon.es/gp/bestsellers/kitchen/1/ref=b']
    q.seed([{'url':u,'category':'test','family':'BEST_SELLERS'} for u in urls]);refs=[]
    for i,url in enumerate(urls):
        path=tmp_path/f'nav{i}.json';path.write_text(json.dumps(sealed({'page_url':url,'plan_sha256':ph})),encoding='utf8')
        refs.append({'url':url,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    review={'kind':'TRACKING_PATH_ALIAS_REVIEW_V1','evidence_reviewed':True,'reviewed_by':'TEST_ONLY',
        'review_basis':'EXACT_HOST_PATH_AND_SEALED_NAVIGATION','run_id':plan['run_id'],
        'original_plan_sha256':ph,'urls':urls,'navigation_receipts':refs}
    path=tmp_path/'review.json';path.write_text(json.dumps(review),encoding='utf8')
    manifest={'run_id':plan['run_id'],'plan_sha256':ph,'reviews':[{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}
    assert apply_alias_reviews(q,plan,manifest)==[urls[0]]
    before=q.snapshot();assert apply_alias_reviews(q,plan,manifest)==[];assert q.snapshot()==before
    path.write_text('{}',encoding='utf8')
    with pytest.raises(ValueError,match='HASH_CHANGED'):apply_alias_reviews(q,plan,manifest)
