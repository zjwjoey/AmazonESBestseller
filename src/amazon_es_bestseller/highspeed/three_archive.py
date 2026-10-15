"""Read-only reuse of actual historical PAGE receipts for navigation only.

Old product observations and request budgets are never seeded into a campaign.
Historical contexts retain their original run/time/receipt and incomplete tree.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from ..multisource.storage import read_sealed,immutable_json
from .v4_adapters import parse_v4_page
from .v4_sources import RANKED,amazon_url
from .three_navigation import discover_navigation,CONTRACT


def historical_context(reference, *, expected_url=None, expected_type=None):
    path=Path(reference['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:
        raise ValueError('THREE_ARCHIVE_RECEIPT_SHA_MISMATCH')
    r=read_sealed(path);kind=r.get('source_type');url=r.get('request_url','')
    prefix='/gp/bestsellers' if kind=='BEST_SELLERS' else '/gp/new-releases'
    if (kind not in RANKED or not amazon_url(url)
            or not (urlsplit(url).path==prefix or urlsplit(url).path.startswith(prefix+'/'))
            or r.get('origin')!='LIVE_CAPTURE' or r.get('kind')!='PAGE' or r.get('status_code')!=200
            or r.get('access_state')!='NORMAL' or r.get('final_url')!=url
            or expected_url is not None and url!=expected_url or expected_type is not None and kind!=expected_type):
        raise ValueError('THREE_ARCHIVE_ACTUAL_NORMAL_RANKING_PAGE_REQUIRED')
    q=read_sealed(path.parent.parent/'requests'/(r['request_id']+'.reserved.json'))
    if any(q.get(k)!=v for k,v in {'run_id':r['run_id'],'url':url,'kind':'PAGE','source_id':r['source_id'],
                                  'observed_at':r['observed_at'],'origin':'LIVE_CAPTURE'}.items()):
        raise ValueError('THREE_ARCHIVE_RESERVATION_BINDING_MISMATCH')
    body=Path(r['html_path']).read_bytes()
    if hashlib.sha256(body).hexdigest()!=r['sha256']:
        raise ValueError('THREE_ARCHIVE_BODY_SHA_MISMATCH')
    source={'source_id':'v4_'+hashlib.sha256(url.encode()).hexdigest()[:24], 'actual_source_url':url,'url':url,
        'source_type':kind,'source_subtype':'BESTSELLER_SALES_RANKING' if kind=='BEST_SELLERS' else 'NEW_RELEASE_SALES_RANKING',
        'page_urls':[url],'parser_profile':'V4','enabled':False,'navigation_evidence':None,
        'historical_capture_reference':reference,'source_status':'HISTORICAL_NAVIGATION_CONTEXT_ONLY',
        'research_category':None,'category_path':[]}
    parsed=parse_v4_page(body.decode('utf8'),source=source,page_url=url,run_id=r['run_id'],
        observed_at=r['observed_at'],evidence_path=r['html_path'],http_status=200,origin='OFFLINE_FIXTURE')
    if parsed['page']['status']!='VERIFIED_WITH_PRODUCTS':
        raise ValueError('THREE_ARCHIVE_CURRENT_PARSER_SOURCE_UNVERIFIED')
    # These rows are intentionally discarded: only their actual type/context
    # establishes a saved navigation parent. They are never a new LIVE result.
    return source,parsed['page']


def inspect_archive(store_root,output):
    root=Path(store_root).resolve();db=root/'index.sqlite3'
    connection=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
    latest={};skipped=[]
    try:
        for text, in connection.execute("SELECT data_json FROM responses WHERE json_extract(data_json,'$.kind')='PAGE'"):
            r=json.loads(text);key=(r.get('source_type'),r.get('request_url'))
            if key[0] not in RANKED or r.get('status_code')!=200 or r.get('access_state')!='NORMAL':continue
            if key not in latest or r['observed_at']>latest[key]['observed_at']:latest[key]=r
    finally:connection.close()
    captures=[]
    for key,r in sorted(latest.items()):
        path=Path(r['evidence_path']);reference={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        try:
            source,page=historical_context(reference)
            captures.append({'source':source,'page':page,'navigation':discover_navigation(page,source),
                'original_capture_run_id':r['run_id'],'original_observed_at':r['observed_at'],
                'historical_products_added_to_target':0,'new_network_requests':0})
        except (ValueError,KeyError,OSError) as exc:
            skipped.append({'request_url':r.get('request_url'),'receipt_reference':reference,'reason':str(exc)})
    value={'schema_version':1,'contract':CONTRACT,'kind':'HISTORICAL_NAVIGATION_ONLY',
        'store_root':str(root),'captures':captures,'skipped':skipped,'new_network_requests':0,
        'historical_products_added_to_target':0,'approved':False,'global_tree_complete':False}
    immutable_json(output,value)
    return {'path':str(Path(output).resolve()),'sha256':hashlib.sha256(Path(output).read_bytes()).hexdigest(),
        'verified_historical_navigation_contexts':len(captures),'skipped':len(skipped),'new_network_requests':0}
