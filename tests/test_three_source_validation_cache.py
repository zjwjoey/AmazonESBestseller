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
from copy import deepcopy


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


def test_navigation_session_cache_does_not_share_mutable_rows_or_skip_byte_checks(tmp_path):
    from amazon_es_bestseller.highspeed.three_navigation import verify_navigation_reference
    reference,body=envelope(tmp_path)
    parent,page=historical_context(reference)
    with prerequisite_review_session():
        first=discover_navigation(page,parent)
        original=deepcopy(first)
        first[0]['label_raw']='TEST_ONLY caller mutation'
        assert discover_navigation(page,parent)==original
        proof=next(r for r in original if '/kitchen/' in r['url'])
        verify_navigation_reference(proof,parent);verify_navigation_reference(proof,parent)
        body.write_text('changed TEST_ONLY cached HTML','utf8')
        with pytest.raises(ValueError):verify_navigation_reference(proof,parent)
        with pytest.raises(ValueError):discover_navigation(page,parent)


def test_mutable_shared_gate_does_not_reparse_immutable_prior_stage_evidence(tmp_path,monkeypatch):
    from amazon_es_bestseller.highspeed.v4_authorization import _verify_prerequisite
    from amazon_es_bestseller.multisource.storage import digest,atomic_json
    reference,body=envelope(tmp_path/'archive')
    shared=tmp_path/'control'/'access_gate.json';atomic_json(shared,sealed({'state':'NETWORK_ERROR'}))
    manifest=tmp_path/'TEST_ONLY_prior_stage.json';manifest.write_text('{}','utf8')
    stage={'pages':[{'evidence_path':str(body)}],
        'capacity_execution':{'plan':{'shared_access_gate_path':str(shared)}}}
    calls=[]
    def verify(*args):calls.append(args);return deepcopy(stage)
    monkeypatch.setattr('amazon_es_bestseller.highspeed.v4_quality.verify_stage_reference',verify)
    prior={'path':str(manifest),'sha256':digest(manifest.read_bytes())}
    with prerequisite_review_session():
        _verify_prerequisite(prior,'T2');assert len(calls)==1
        atomic_json(shared,sealed({'state':'NORMAL','agent_handled':True}))
        _verify_prerequisite(prior,'T2');assert len(calls)==1
        # Creating a live lock alongside the mutable Gate is also control state.
        (shared.parent/'highspeed_live.lock').write_bytes(b'')
        _verify_prerequisite(prior,'T2');assert len(calls)==1
        body.write_bytes(body.read_bytes()+b'\n<!-- TEST_ONLY changed evidence -->')
        _verify_prerequisite(prior,'T2');assert len(calls)==2


def test_cached_prior_proof_never_relaxes_current_gate_validation(tmp_path,monkeypatch):
    from test_three_source_runner import execution_scope
    from test_highspeed_v4_execution import NOW
    from amazon_es_bestseller.highspeed.v4_authorization import validate_v4_authorization
    p,a=execution_scope(tmp_path,monkeypatch)
    with prerequisite_review_session():
        validate_v4_authorization(p,a,{'state':'NORMAL'},now=NOW,store_root=a['store_root'])
        for state in ('NETWORK_ERROR','CHALLENGE','UNKNOWN'):
            with pytest.raises(ValueError,match='GLOBAL_ACCESS_GATE_BLOCKED'):
                validate_v4_authorization(p,a,{'state':state},now=NOW,store_root=a['store_root'])
