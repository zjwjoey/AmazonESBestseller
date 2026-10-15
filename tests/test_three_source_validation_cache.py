"""Execution cache retains byte checks on HTML and original reservation proofs."""
from pathlib import Path
import hashlib
import pytest
from test_three_source_contract import scope
from test_three_source_archive import envelope
from amazon_es_bestseller.highspeed.three_archive import historical_context
from amazon_es_bestseller.highspeed.three_navigation import discover_navigation
from amazon_es_bestseller.highspeed.three_contract import source_from_navigation
from amazon_es_bestseller.highspeed.v4_authorization import prerequisite_review_session,validate_plan_v4
from amazon_es_bestseller.multisource.storage import read_sealed,sealed,canonical_bytes


@pytest.mark.parametrize('changed',['html','reservation'])
def test_same_execution_avoids_reparse_but_rejects_changed_proof_bytes(tmp_path,monkeypatch,changed):
    p,a=scope(tmp_path/'scope');reference,body=envelope(tmp_path/'archive')
    parent,page=historical_context(reference)
    nav=next(r for r in discover_navigation(page,parent) if '/kitchen/' in r['url'])
    child=source_from_navigation(nav,parent)
    p['sources']=[child,*[s for s in p['sources'] if s['source_type']!='BEST_SELLERS']]
    import amazon_es_bestseller.highspeed.three_contract as contract
    real=contract.verify_source;calls=[]
    def counted(*args,**kwargs):calls.append(args[0]['actual_source_url']);return real(*args,**kwargs)
    monkeypatch.setattr(contract,'verify_source',counted)
    with prerequisite_review_session():
        validate_plan_v4(p);count=len(calls);validate_plan_v4(p)
        assert len(calls)==count
        if changed=='html':body.write_text('changed TEST_ONLY body','utf8')
        else:
            rpath=Path(reference['path']).parent.parent/'requests/req_TEST_ONLY.reserved.json'
            q=read_sealed(rpath);q['source_id']='changed TEST_ONLY reservation'
            rpath.write_bytes(canonical_bytes(sealed(q)))
        with pytest.raises(ValueError):validate_plan_v4(p)
        assert len(calls)>count
