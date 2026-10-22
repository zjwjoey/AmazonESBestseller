"""Avoid repeated DOM work, retaining independent export and byte checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_highspeed_v4_native_grid import grid_request
from test_three_source_runner import execution_scope, Advancing
from test_highspeed_v4_execution import NOW
from amazon_es_bestseller.highspeed import v4_adapters
from amazon_es_bestseller.highspeed.v4_authorization import prerequisite_review_session, saved_grid_binding
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.three_runner import V4ThreeSourceRunner
from amazon_es_bestseller.highspeed.three_report import export_three


def test_grid_parent_dom_review_once_per_session_and_fresh_outside():
    d,s,a=grid_request()
    with patch.object(v4_adapters,'parse_v4_page',wraps=v4_adapters.parse_v4_page) as parse:
        with prerequisite_review_session():
            first=saved_grid_binding(d,s)
            first['source_id']='caller mutation'
            assert saved_grid_binding(d,s)['source_id']==s['source_id']
            assert parse.call_count==1
        saved_grid_binding(d,s)
        assert parse.call_count==2


@pytest.mark.parametrize('changed',['html','source','metadata','parent'])
def test_cached_grid_parent_keeps_all_ownership_guards(tmp_path,changed):
    d,s,a=grid_request();d=deepcopy(d)
    p=tmp_path/'actual_grid.html';p.write_bytes(Path(d['evidence_path']).read_bytes());d['evidence_path']=str(p)
    with prerequisite_review_session():
        saved_grid_binding(d,s)
        if changed=='html':p.write_text('<html>changed</html>',encoding='utf8')
        elif changed=='source':s={**s,'source_id':'wrong'}
        elif changed=='metadata':d['metadata']['reftag']='wrong'
        else:d['request_contract_parent_page_url']='https://www.amazon.es/gp/bestsellers/books'
        with pytest.raises(ValueError):saved_grid_binding(d,s)


def test_data_read_does_not_replay_live_source_authorization(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch);store=RunStore(a['store_root']);store.begin_run(p)
    try:
        with patch('amazon_es_bestseller.highspeed.v4_authorization.validate_plan_v4',side_effect=AssertionError('read must not perform source DOM authorization')):
            assert store.normalizer_version(p['run_id'])==p['normalizer_version']
    finally:store.close()


def test_three_runner_central_current_dom_is_reviewed_once(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch);store=RunStore(a['store_root']);clock=[NOW]
    import amazon_es_bestseller.highspeed.v4_capacity as capacity
    import amazon_es_bestseller.highspeed.v4_runner as runner
    real=capacity.audit_v4;calls=[]
    def counted(*args,**kwargs):calls.append(args[0]);return real(*args,**kwargs)
    monkeypatch.setattr(capacity,'audit_v4',counted);monkeypatch.setattr(runner,'audit_v4',counted)
    try:
        r=V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=Advancing(p,a,clock))
        assert r['quality']['central_full_dom_review_passed'] and r['receipt_audit']['diagnostic_passed']
        assert len(calls)==1
    finally:store.close()


def test_export_has_a_fresh_private_review_session(tmp_path,monkeypatch):
    p,a=execution_scope(tmp_path,monkeypatch);store=RunStore(a['store_root']);clock=[NOW]
    import amazon_es_bestseller.highspeed.three_report as report
    from amazon_es_bestseller.highspeed.v4_authorization import _PREREQUISITES
    real=report.audit_capacity;seen=[]
    def audited(*args,**kwargs):
        session=_PREREQUISITES.get();assert session is not None and 'COLLECTION_ONLY_SENTINEL' not in session
        seen.append(id(session));return real(*args,**kwargs)
    try:
        V4ThreeSourceRunner(store,clock=lambda:clock[0],sleep=lambda n:None).run(p,authorization=a,transport=Advancing(p,a,clock))
        monkeypatch.setattr(report,'audit_capacity',audited)
        phases=[]
        with prerequisite_review_session():
            _PREREQUISITES.get()['COLLECTION_ONLY_SENTINEL']=True
            out=export_three(store,p['run_id'],tmp_path/'exports',progress=phases.append)
            assert _PREREQUISITES.get()['COLLECTION_ONLY_SENTINEL'] is True
        manifest=json.loads((Path(out['directory'])/'manifest.json').read_text('utf8'))
        assert manifest['receipt_audit']['central_quality']['central_full_dom_review_passed']
        assert manifest['CSV_JSONL_independently_verified'] and len(seen)==1
        assert phases[0]=='EXPORT_STORE_READ_STARTED'
        assert phases[-1]=='EXPORT_INDEPENDENT_ROUNDTRIP_FINISHED'
        assert phases.index('EXPORT_DOM_RECEIPT_AUDIT_FINISHED')<phases.index('EXPORT_WRITE_AND_INDEPENDENT_ROUNDTRIP_STARTED')
    finally:store.close()
