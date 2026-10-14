"""Real saved-page T0 evidence cannot be mistaken for LIVE acceptance."""
import hashlib
import json
from pathlib import Path
import pytest
from amazon_es_bestseller.highspeed.contracts import new_plan
from amazon_es_bestseller.highspeed.v3_links import NORMALIZER_VERSION
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.highspeed.runner import HighSpeedRunner
from amazon_es_bestseller.highspeed.quality import audit_run
from amazon_es_bestseller.highspeed.cli import export_gate
from amazon_es_bestseller.highspeed.authorization import verified_stage_artifact


def saved_t0(tmp_path):
    fixture=Path('tests/fixtures/highspeed/real_v3_new_releases_root.html').resolve()
    provenance=json.loads(fixture.with_suffix('.provenance.json').read_text(encoding='utf8'))
    assert hashlib.sha256(fixture.read_bytes()).hexdigest()==provenance['sha256']
    url=provenance['page_url']; config=Path('configs/highspeed/v3/category_mapping.reviewed.json').resolve()
    source={'source_id':'real_new','source_type':'NEW_RELEASES','actual_source_url':url,
            'page_urls':[url],'research_category':None,'parser_profile':'V3',
            'offline_pages':{url:str(fixture)},'fixture_original_capture_provenance':provenance}
    plan=new_plan('real_t0',stage='T0',origin='OFFLINE_FIXTURE',snapshot_contract_version=3,
                  normalizer_version=NORMALIZER_VERSION,target_unique_links=36,sources=[source],
                  max_requests_per_run=1,max_pages_per_run=1,max_requests_per_day=1,
                  category_mapping_config_reference={'path':str(config),'sha256':hashlib.sha256(config.read_bytes()).hexdigest()})
    store=RunStore(tmp_path/'store');HighSpeedRunner(store).run(plan)
    return store


def test_real_saved_t0_gate_proves_fields_without_claiming_live(tmp_path):
    store=saved_t0(tmp_path)
    try:
        quality=audit_run(store,'real_t0')
        assert quality['gate_passed'] is True
        assert quality['unique_valid_links']==36
        assert quality['card_field_quality']['receipt_verification_scope']=='SAME_RUN_OFFLINE_FIXTURE_EXECUTION'
        assert quality['card_field_quality']['counts']['offline_fixture_execution_receipts_verified']==36
        assert quality['card_field_quality']['counts']['actual_live_capture_receipts_verified']==0
        result=export_gate(store,'real_t0',tmp_path/'gates')
        assert result['passed'] is True
        verified_stage_artifact(result['gate'],'T0')
        assert any(f['code']=='PAGE_COMPLETENESS_NOT_PROVEN' and f['severity']=='WARNING' for f in quality['findings'])
    finally:store.close()


@pytest.mark.parametrize('field,value',[('run_id','other_run'),('sha256','0'*64),
    ('observed_at','2000-01-01T00:00:00Z'),('source_id','other_source'),
    ('source_type','MOST_GIFTED'),('origin','LIVE_CAPTURE'),('kind','ACP')])
def test_t0_receipt_forgery_never_passes(tmp_path,monkeypatch,field,value):
    store=saved_t0(tmp_path)
    try:
        original=store.iter_responses
        monkeypatch.setattr(store,'iter_responses',lambda run:iter([{**r,field:value} for r in original(run)]))
        assert audit_run(store,'real_t0')['gate_passed'] is False
    finally:store.close()


def test_t0_fixture_receipts_cannot_prove_live_stage(tmp_path,monkeypatch):
    store=saved_t0(tmp_path)
    try:
        original=store.get_run
        monkeypatch.setattr(store,'get_run',lambda run:{**original(run),'stage':'T1','origin':'LIVE_CAPTURE'})
        quality=audit_run(store,'real_t0')
        assert quality['gate_passed'] is False
        assert any(f['code']=='CAPTURE_RECEIPT_UNVERIFIED' for f in quality['findings'])
    finally:store.close()
