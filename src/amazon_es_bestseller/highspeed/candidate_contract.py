"""V3 link candidates: immutable observations and explicit aggregation policy."""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from .links import candidate_id

CONTRACT_VERSION = 'candidate_snapshot_v3.2'
RATING_SELECTION_RULE = 'LATEST_VALID_OBSERVATION_WITH_PAIRED_REVIEW_FALLBACK_v2'

def _time(row):
    try:
        value=datetime.fromisoformat(row.get('observed_at','').replace('Z','+00:00'))
        if value.tzinfo is None:return None
        return value.astimezone(timezone.utc)
    except (TypeError,ValueError,AttributeError):return None

def build_candidate_records(observations, *, batch_id, snapshot_id):
    """Group only identical canonical URLs; do not imply independent SKU identity.

    All membership and rating observations remain available. A conflicting
    history is explicit even when latest valid evidence supplies a chosen value.
    Same-time different ratings/counts have no chosen observation or value.
    """
    groups=defaultdict(list)
    for original in observations:
        row=deepcopy(original)
        url=row.get('normalized_product_url')
        if url:groups[url].append(row)
    results=[]
    for url,rows in sorted(groups.items()):
        ordered=sorted(rows,key=lambda r:(_time(r) or datetime.min.replace(tzinfo=timezone.utc),r.get('observation_id','')))
        representative=ordered[-1]
        dated=[r for r in ordered if _time(r) is not None]
        valid=[r for r in dated if r.get('rating_status')=='VALID' and isinstance(r.get('rating_value'),(int,float)) and not isinstance(r.get('rating_value'),bool) and 0<=r['rating_value']<=5]
        chosen=None
        ties=[]
        if valid:
            latest=max(_time(r) for r in valid)
            ties=[r for r in valid if _time(r)==latest]
            if len({(r['rating_value'],r.get('review_count'),r.get('review_count_status')) for r in ties})==1:
                chosen=sorted(ties,key=lambda r:r.get('observation_id',''))[0]
        selection_decision='LATEST_VALID_RATING' if chosen else 'NO_VALID_RATING'
        review_tie_conflict=False
        if not valid:
            reviews=[r for r in dated if r.get('rating_status')!='VALID' and r.get('review_count_status')=='VALID' and type(r.get('review_count')) is int and r['review_count']>=0]
            if reviews:
                latest=max(_time(r) for r in reviews)
                review_ties=[r for r in reviews if _time(r)==latest]
                review_tie_conflict=len({r['review_count'] for r in review_ties})>1
                if not review_tie_conflict:
                    chosen=sorted(review_ties,key=lambda r:r.get('observation_id',''))[0]
                    selection_decision='LATEST_VALID_REVIEW_WITHOUT_VALID_RATING_PAIRED_FALLBACK_v1'
        values={r['rating_value'] for r in valid}
        conflict=len(values)>1 or (valid and chosen is None)
        history=[{k:deepcopy(r.get(k)) for k in ('observation_id','run_id','snapshot_id','raw_product_url','normalized_product_url','representation','representation_slot','ranking_position','rank_raw','rank_context','parser_version','source_type','source_id','source_url','observed_at','rating_binding_evidence','rating_raw','rating_value','rating_status','rating_source','rating_evidence','review_count_raw','review_count','review_count_status','review_count_evidence','rating_observed_at','evidence_path','evidence_sha256')} for r in ordered]
        titles=[r for r in ordered if r.get('product_title_raw') and r.get('product_title_status') in {'SOURCE_VISIBLE','IMAGE_ALT','TRUNCATED'} and r.get('product_title_evidence',{}).get('raw_product_url')==r.get('raw_product_url')]
        title=titles[-1] if titles else None
        memberships=[{k:deepcopy(r.get(k)) for k in ('observation_id','run_id','snapshot_id','raw_product_url','normalized_product_url','product_title_raw','product_title_evidence','product_title_status','parser_version','source_type','source_id','source_url','page_url','rank_raw','ranking_position','rank_semantics','rank_context','category_path','amazon_category','browse_node_id','research_category','category_mapping_status','observed_at','representation','representation_slot','evidence_path','evidence_sha256')} for r in ordered]
        record={'contract_version':CONTRACT_VERSION,'candidate_id':candidate_id(url),
            'product_title_es':title['product_title_raw'] if title else None,
            'product_title_status':title['product_title_status'] if title else 'UNVERIFIED',
            'product_title_chosen_observation_id':title.get('observation_id') if title else None,
            'raw_product_url':representative.get('raw_product_url'),'normalized_product_url':url,
            'url_identity_status':representative.get('url_identity_status','IDENTITY_UNCONFIRMED'),
            'dedup_status':'EXACT_NORMALIZED_URL_GROUP','source_memberships':memberships,
            'rating_history':history,'rating_selection_rule':RATING_SELECTION_RULE,
            'rating_chosen_observation_id':chosen.get('observation_id') if chosen else None,
            'rating_selection_decision':selection_decision,
            'rating_conflicts':history if conflict else [],
            'rating_raw':chosen.get('rating_raw') if chosen else None,
            'rating_value':chosen.get('rating_value') if chosen and chosen.get('rating_status')=='VALID' else None,
            'rating_status':'SOURCE_CONFLICT' if conflict else chosen.get('rating_status','UNVERIFIED') if chosen else (representative.get('rating_status','UNVERIFIED') if representative.get('rating_status')!='VALID' else 'UNVERIFIED'),
            'rating_source':chosen.get('rating_source') if chosen else None,
            'rating_observed_at':chosen.get('rating_observed_at') if chosen else None,
            'rating_evidence':deepcopy(chosen.get('rating_evidence')) if chosen else [],
            'review_count_raw':chosen.get('review_count_raw') if chosen else None,
            'review_count':chosen.get('review_count') if chosen and chosen.get('review_count_status')=='VALID' else None,
            'review_count_status':chosen.get('review_count_status','UNVERIFIED') if chosen else 'SOURCE_CONFLICT' if conflict or review_tie_conflict else representative.get('review_count_status','UNVERIFIED'),
            'review_count_evidence':deepcopy(chosen.get('review_count_evidence')) if chosen else [],
            'review_count_conflicts':history if review_tie_conflict else [],
            'first_observed_at':dated[0].get('observed_at') if dated else None,
            'latest_observed_at':dated[-1].get('observed_at') if dated else None,
            'observation_count':len(rows),'snapshot_id':snapshot_id,'batch_id':batch_id,
            'parser_version':representative.get('parser_version'),
            'evidence_reference':[{k:r.get(k) for k in ('observation_id','evidence_path','evidence_sha256')} for r in ordered],
            'raw_observations':ordered,'selection_rules_version':None,'selection_audit_status':'NOT_RUN'}
        for key in ('source_type','source_url','ranking_position','rank_semantics','category_path','browse_node_id','research_category','category_mapping_status'):
            record[key]=deepcopy(representative.get(key))
        record['original_amazon_category']=representative.get('amazon_category')
        results.append(record)
    return results
