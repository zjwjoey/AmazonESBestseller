from pathlib import Path
from test_highspeed_v4_execution import setup_scope,NOW
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.parallel_page_ledger import ParallelPageLedger
from amazon_es_bestseller.highspeed.parallel_journal_bridge import OfflineJournalBridge
from amazon_es_bestseller.highspeed.parallel_commit import ParallelSnapshotCommitter
import pytest


def test_actual_parser_store_and_audit_commit_before_release(tmp_path):
    plan,auth=setup_scope(tmp_path);store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat())
        source=plan['sources'][0];url=source['actual_source_url']
        q=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='TEST_ONLY',deadline=1000,page_budget=3)
        q.seed([{'url':url,'category':'test','family':source['source_type']}])
        bridge=OfflineJournalBridge(q,store,{**plan,'origin':'OFFLINE_FIXTURE','live_enabled':False},{url:source})
        item=bridge.reserve(1,now=10,observed_at=NOW.isoformat())
        stopped=[];runner=V4T1Runner(store)
        committer=ParallelSnapshotCommitter(runner,plan,q,stop=stopped.append,fixture_mode=True)
        body=Path(auth['sources'][source['source_id']]['evidence']['path']).read_text(encoding='utf8')
        committer(1,item,{'status_code':200,'url':url,'text':body})
        assert not stopped
        assert q.snapshot()['pages'][0][3]=='COMMITTED'
        assert len(store.pages(plan['run_id']))==1
        assert q.claim(1,now=20) is None
    finally:store.close()


@pytest.mark.parametrize('failure',['rendered_challenge','journal_failure'])
def test_rendered_restriction_and_journal_failure_never_release_claim(tmp_path,failure):
    plan,auth=setup_scope(tmp_path);store=RunStore(auth['store_root'])
    try:
        store.begin_run(plan,started_at=NOW.isoformat())
        source=plan['sources'][0];url=source['actual_source_url']
        q=ParallelPageLedger(tmp_path/'queue.sqlite',run_id='TEST_ONLY',original_plan_sha256='TEST_ONLY',deadline=1000,page_budget=3)
        q.seed([{'url':url,'category':'test','family':source['source_type']}])
        bridge=OfflineJournalBridge(q,store,{**plan,'origin':'OFFLINE_FIXTURE','live_enabled':False},{url:source})
        item=bridge.reserve(1,now=10,observed_at=NOW.isoformat());stopped=[]
        def stop(reason):
            stopped.append(reason);q.stop('ACCESS_STOP' if reason=='CHALLENGE' else 'JOURNAL_FAILURE')
        committer=ParallelSnapshotCommitter(V4T1Runner(store),plan,q,stop=stop,fixture_mode=True)
        response={'status_code':200,'url':url,'text':'<html>normal server document</html>',
            'rendered_dom_html':'<html>Robot Check</html>'}
        if failure=='journal_failure':
            def fail(*args,**kwargs):raise OSError('TEST_ONLY disk failure')
            store.save_response=fail
            with pytest.raises(OSError):committer(1,item,response)
        else:
            receipt=committer(1,item,response)
            assert receipt['access_state']=='CHALLENGE'
            assert store.get_gate()['state']=='CHALLENGE'
            assert not store.pages(plan['run_id'])
        assert stopped and q.claim(2,now=30) is None
        assert q.snapshot()['pages'][0][3] in {'HELD','RESERVED'}
    finally:store.close()
