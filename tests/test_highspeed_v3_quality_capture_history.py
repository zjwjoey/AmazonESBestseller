"""Retained captures need their own immutable parent, not the latest page."""
from pathlib import Path
from amazon_es_bestseller.highspeed.v3_workflows import readonly_store
from amazon_es_bestseller.highspeed.quality import audit_run
from amazon_es_bestseller.highspeed.adapters import parse_page
from amazon_es_bestseller.highspeed.store import RunStore
from amazon_es_bestseller.multisource.storage import digest
from test_highspeed_v3_workflows import old_capture


def test_live_v3_retained_old_capture_is_reverified_against_its_original_parent(tmp_path):
    root,_=old_capture(tmp_path,v3=True)
    store=RunStore(root)
    try:
        source=store.get_run('old')['plan']['sources'][0]
        observed='2026-10-14T00:01:00Z';url=source['actual_source_url']
        html='<h1>Los más vendidos</h1><div id="zg"><div id="gridItemRoot"><span class="zg-bdg-text">#1</span><a href="/product/two"><div data-product-title>Second original title</div></a></div></div>'
        request=store.reserve_request('old',{'kind':'PAGE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
        receipt=store.save_response('old',request,{'text':html,'status_code':200,'access_state':'NORMAL','origin':'LIVE_CAPTURE','url':url,'source_id':'home','source_type':'BEST_SELLERS','observed_at':observed,'page_key':'page'})
        parsed=parse_page(html,source=source,run_id='old',page_url=url,page_number=1,observed_at=observed,evidence_path=receipt['html_path'],origin='LIVE_CAPTURE')
        for row in parsed['observations']:
            row.update(origin='LIVE_CAPTURE',capture_run_id='old',source_completeness_status='UNKNOWN',capture_receipt_reference={'path':receipt['evidence_path'],'sha256':digest(Path(receipt['evidence_path']).read_bytes())})
        store.commit_page('old','page',{**parsed['page'],'page_key':'page','source':source},parsed['observations'])
    finally:store.close()
    with readonly_store(root) as store:
        result=audit_run(store,'old')
        assert len(list(store.iter_observations('old')))==2
    assert result['card_field_quality']['ok'],result['card_field_quality']['findings']
    assert result['card_field_quality']['counts']['dom_verified_observations']==2
    assert result['card_field_quality']['counts']['capture_receipts_verified']==2
    assert result['page_count']==1
    assert result['capture_page_count']==2
    assert result['retained_prior_capture_page_count']==1
    assert result['quality_gate_passed'] is False
