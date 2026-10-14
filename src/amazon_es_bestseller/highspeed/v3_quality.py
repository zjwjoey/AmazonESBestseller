"""Independent V3 DOM quality audit; never upgrades source completeness."""
from __future__ import annotations
from collections import Counter, OrderedDict
import hashlib
import json
from pathlib import Path
import re
from bs4 import BeautifulSoup
from ..access.detector import detect_access_status
from .adapters import _cards, _scope, _title_from_card_v3, parse_page
from .links import normalize_product_url, candidate_id
from .ratings import validate_rating_fields
from .sources import same_source_category, source_type_from_url

QUALITY_VERSION='snapshot_v3_dom_quality_v1'

def audit_observations(observations, sources, *, evidence_loader=None, receipt_validator=None,
                       require_receipts=True, cache_size=32, context_validator=None):
    """Validate each original observation against its exact saved representation.

    evidence_loader(row) returns saved body text, not an unverified card. Optional
    receipt_validator(row,source) must independently prove original capture and
    request receipt/run/representation linkage. With no such check, required
    receipt QA stays unresolved. Caches are scoped to source plan, run, URL and
    representation, and bounded to avoid retaining the whole 30k DOM corpus.
    """
    source_map=sources if isinstance(sources,dict) else {s['source_id']:s for s in sources}
    cache=OrderedDict(); counts=Counter(); by_source={}; findings=[]
    rows=list(observations)
    def issue(row,code):
        findings.append({'observation_id':row.get('observation_id'),'source_id':row.get('source_id'),'code':code,'severity':'ERROR'})
    for row in rows:
        counts['observations']+=1
        scope=(row.get('source_type'),row.get('source_id'))
        stats=by_source.setdefault(scope,Counter());stats['observations']+=1
        for prefix in ('rating','review_count'):
            status=row.get(prefix+'_status','UNVERIFIED')
            counts[prefix+'_'+status]+=1;stats[prefix+'_'+status]+=1
        counts['source_completeness_'+str(row.get('source_completeness_status','UNKNOWN'))]+=1
        source=source_map.get(row.get('source_id'))
        if source is None:issue(row,'SOURCE_NOT_IN_PLAN');continue
        if source.get('source_type')!=row.get('source_type'):issue(row,'SOURCE_TYPE_MISMATCH');continue
        page_url=row.get('page_url') or row.get('source_url')
        actual=source.get('actual_source_url') or source.get('source_url')
        if not actual or not page_url or not same_source_category(page_url,actual) or source_type_from_url(page_url)!=row.get('source_type'):
            issue(row,'SOURCE_CONTEXT_MISMATCH');continue
        try:
            receipt_ok=receipt_validator is not None and receipt_validator(row,source) is True
        except (OSError,ValueError,KeyError,TypeError):
            receipt_ok=False
        if receipt_ok:
            counts['capture_receipts_verified']+=1;stats['capture_receipts_verified']+=1
        elif require_receipts:
            issue(row,'CAPTURE_RECEIPT_UNVERIFIED')
        source_signature=json.dumps(source,sort_keys=True,ensure_ascii=False,default=str)
        key=(row.get('evidence_path'),row.get('evidence_sha256'),source_signature,row.get('run_id'),
            row.get('snapshot_id'),page_url,row.get('representation'),row.get('observed_at'),row.get('page_number'))
        try:
            if key not in cache:
                body=evidence_loader(row) if evidence_loader else Path(row['evidence_path']).read_text(encoding='utf8')
                if not isinstance(body,str):raise ValueError('SAVED_BODY_MISSING')
                if hashlib.sha256(body.encode('utf8')).hexdigest()!=row.get('evidence_sha256'):raise ValueError('HTML_SHA_MISMATCH')
                if detect_access_status(row.get('http_status',200),body).value!='NORMAL':raise ValueError('ACCESS_NOT_NORMAL')
                soup=BeautifulSoup(body,'lxml')
                representation=row.get('representation')
                if representation=='SERVER':
                    page=parse_page(body,source={**source,'parser_profile':'V3'},run_id=row.get('run_id') or 'qa',
                        snapshot_id=row.get('snapshot_id'),page_url=page_url,page_number=row.get('page_number',1),
                        observed_at=row['observed_at'],evidence_path=row['evidence_path'])['page']
                    if not page['source_type_verified'] or not page['source_context_valid']:raise ValueError('PAGE_TYPE_OR_CONTEXT_UNVERIFIED')
                elif not re.fullmatch(r'ACP_\d+',representation or ''):raise ValueError('REPRESENTATION_UNVERIFIED')
                cache[key]=(_cards(soup,fragment=representation!='SERVER'),page if representation=='SERVER' else None)
                while len(cache)>max(1,cache_size):cache.popitem(last=False)
            cache.move_to_end(key)
            cards,page=cache[key];slot=row.get('representation_slot')
            if row.get('source_completeness_status')=='COMPLETE' and (page is None or not page.get('page_complete')):
                issue(row,'SOURCE_COMPLETENESS_NOT_PROVEN')
            if type(slot) is not int or slot<0 or slot>=len(cards):raise ValueError('CARD_SLOT_NOT_FOUND')
            card=cards[slot];raw=row.get('raw_product_url')
            if not any(a.get('href')==raw for a in card.select('a[href]')):raise ValueError('CARD_PRODUCT_HREF_MISMATCH')
            badge=card.select_one('.zg-bdg-text, span.a-badge-text, [data-ranking-position]')
            badge_text=badge.get_text(' ',strip=True) if badge else None
            match=re.search(r'#\s*(\d+)',badge_text or '')
            rank=int(match.group(1)) if match else int(badge.get('data-ranking-position')) if badge and str(badge.get('data-ranking-position')).isdigit() else None
            if rank!=row.get('ranking_position') or badge_text!=row.get('rank_raw'):raise ValueError('CARD_RANK_MISMATCH')
            if source.get('parser_profile') == 'V3' or str(row.get('parser_version','')).startswith('link_first_adapters_v3.'):
                from .v3_links import normalize_product_url_v3
                normalized=normalize_product_url_v3(raw,base_url=page_url)
            else:
                normalized=normalize_product_url(raw,base_url=page_url)
            if normalized['normalized_product_url']!=row.get('normalized_product_url'):raise ValueError('NORMALIZED_URL_MISMATCH')
            if normalized['is_valid'] and row.get('candidate_id')!=candidate_id(normalized['normalized_product_url']):raise ValueError('CANDIDATE_ID_MISMATCH')
            if row.get('rank_context') != _scope(card,page_url):
                if context_validator is None or context_validator(row,card,source) is not True:raise ValueError('RANK_CONTEXT_MISMATCH')
            binding=row.get('rating_binding_evidence',{})
            for key in ('raw_product_url','evidence_path','evidence_sha256','representation','representation_slot','rank_raw'):
                if binding.get(key)!=row.get(key):raise ValueError('RATING_OBSERVATION_BINDING_MISMATCH')
            if row.get('rating_observed_at')!=row.get('observed_at'):raise ValueError('RATING_CAPTURE_TIME_MISMATCH')
            if not validate_rating_fields(row,card):raise ValueError('RATING_DOM_OR_BINDING_MISMATCH')
            title,proof=_title_from_card_v3(card,raw)
            if title!=row.get('product_title_raw') or proof.get('title_status')!=row.get('product_title_status'):raise ValueError('TITLE_DOM_MISMATCH')
            for k,v in proof.items():
                if row.get('product_title_evidence',{}).get(k)!=v:raise ValueError('TITLE_PROOF_MISMATCH')
            counts['dom_verified_observations']+=1;stats['dom_verified_observations']+=1
            if row.get('rating_status')=='VALID':counts['dom_verified_filled_ratings']+=1
            if row.get('review_count_status')=='VALID':counts['dom_verified_filled_review_counts']+=1
        except (OSError,ValueError,KeyError,TypeError) as exc:issue(row,str(exc))
    for prefix in ('rating','review_count'):
        for status in ('VALID','NOT_DISPLAYED','PARSE_FAILED','SOURCE_CONFLICT','UNVERIFIED'):
            counts.setdefault(prefix+'_'+status,0)
            for stats in by_source.values():stats.setdefault(prefix+'_'+status,0)
    return {'quality_version':QUALITY_VERSION,'ok':not findings,'counts':dict(counts),
        'metric_denominator':'all supplied observations including missing, failed, conflicting and UNKNOWN completeness',
        'source_scope':[{'source_type':k[0],'source_id':k[1],**dict(v)} for k,v in sorted(by_source.items(),key=lambda i:str(i[0]))],
        'findings':findings,'capture_receipts_required':require_receipts,
        'not_confirmed':['independent_purchasable_SKU','parent_child_identity','page_completeness','new_LIVE_capture_without_receipt_verification']}
