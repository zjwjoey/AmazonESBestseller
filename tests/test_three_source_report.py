"""Exports remain TEST_ONLY local artifacts from saved DOM envelopes."""
from datetime import timedelta
from pathlib import Path
import json,hashlib,csv
from test_three_source_runner import execution_scope,Advancing
from test_highspeed_v4_execution import NOW
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
from amazon_es_bestseller.highspeed.three_report import export_three
from amazon_es_bestseller.highspeed.store import RunStore


def test_all_twelve_exports_bound_to_same_run_and_independently_verified(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch);store=RunStore(a['store_root']);clock=[NOW]
    try:
        V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=Advancing(p,a,clock))
        result=export_three(store,p['run_id'],tmp_path/'TEST_ONLY_output');d=Path(result['directory'])
        manifest=json.loads((d/'manifest.json').read_text('utf8'))
        assert len(manifest['files'])==12 and not manifest['accepted']
        assert manifest['acceptance']=='NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET'
        assert manifest['receipt_audit']['diagnostic_passed']
        assert manifest['CSV_JSONL_independently_verified']
        for f in manifest['files']:assert hashlib.sha256((d/f['name']).read_bytes()).hexdigest()==f['sha256']
        for kind in ('BEST_SELLERS','NEW_RELEASES','CUSTOMER_FAVORITES'):
            with (d/(kind+'_商品候选.csv')).open(encoding='utf-8-sig',newline='') as fh:rows=list(csv.DictReader(fh))
            assert rows and all(r['source_type']==kind for r in rows)
            assert len(rows)==manifest['metrics']['sources'][kind]['D_unique_normalized_URLs']
        assert 'NOT_ACCEPTED' in (d/'最终验收报告.md').read_text('utf8')
        assert not json.loads((d/'Amazon类目导航树.json').read_text('utf8'))['global_tree_complete']
    finally:store.close()
