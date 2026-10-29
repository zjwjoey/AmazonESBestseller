import hashlib,json,pathlib,shutil
from datetime import datetime,timezone
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.v4_runner import V4T1Runner
from amazon_es_bestseller.highspeed.parallel_checkpoint import load_parallel_checkpoint


def test_real_checkpoint_import_reopen_preserves_clock_budget_and_pages(tmp_path):
    base=pathlib.Path(r'F:\ai文件\CodexStorage\work\AmazonESBestseller\snapshot50k-parallel-20261029')
    manifest=json.loads((base/'pause-manifest.json').read_text(encoding='utf8'));original=pathlib.Path(manifest['source_root'])
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    assert all(sha(original/n)==h for n,h in manifest['files_sha256'].items())
    plan=json.loads((original/'plan.approved.json').read_text(encoding='utf8'))
    auth=json.loads((original/'authorization.approved.json').read_text(encoding='utf8'))
    copy=tmp_path/'store';shutil.copytree(original/'store',copy)
    store=RunStore(copy)
    try:
        runner=V4T1Runner(store);path=tmp_path/'queue.sqlite';now=datetime(2026,10,29,4,tzinfo=timezone.utc)
        first=load_parallel_checkpoint(runner,plan,auth,path,now=now)
        state=first.snapshot();assert len(state['pages'])==22 and not state['claims']
        assert all(r[3]=='COMMITTED' for r in state['pages'])
        second=load_parallel_checkpoint(runner,plan,auth,path,now=now)
        assert second.snapshot()==state
        assert second.identity[5]==22
        assert second.identity[2]==datetime.fromisoformat(auth['expires_at']).timestamp()
        assert len(store.requests(plan['run_id']))==22
    finally:store.close()
    assert all(sha(original/n)==h for n,h in manifest['files_sha256'].items())
