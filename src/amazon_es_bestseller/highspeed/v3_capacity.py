"""Read-only historical supply evidence, never a forecast of current supply."""
from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
from ..multisource.contracts import SOURCE_ROOTS
from ..multisource.storage import immutable_json, sealed, digest
from .v3_links import normalize_product_url_v3
from .v3_workflows import readonly_store


def capacity_evidence(plan, pages, observations, *, new_source_ids=()):
    """Keep distinct URLs global; do not sum overlapping per-list supplies."""
    links=defaultdict(set); all_links=set(); raw=Counter(); page_states=defaultdict(Counter)
    for row in observations:
        kind=row.get('source_type');raw[kind]+=1
        value=normalize_product_url_v3(row.get('raw_product_url') or '',row.get('source_url') or 'https://www.amazon.es')
        if value['is_valid']:
            links[kind].add(value['normalized_product_url']);all_links.add(value['normalized_product_url'])
    for page in pages:
        kind=page.get('source_type') or page.get('source',{}).get('source_type')
        state='SOURCE_EMPTY' if page.get('source_empty') else 'NAVIGATION_ONLY' if page.get('source_navigation_only') else page.get('page_content_state') or page.get('status') or 'UNVERIFIED'
        page_states[kind][state]+=1
    new=set(new_source_ids); cumulative=0; reach=[]; types=Counter()
    for ordinal,source in enumerate(plan['sources'],1):
        types[source['source_type']]+=1
        cumulative+=len(source.get('page_urls',[]))
        if source['source_id'] in new:
            reach.append({'source_type':source['source_type'],'source_id':source['source_id'],
                'source_ordinal':ordinal,'declared_page_url_ordinal_end':cumulative,
                'within_page_only_budget_prefix':cumulative<=plan['max_pages_per_run']})
    first={kind:next((r for r in reach if r['source_type']==kind),None) for kind in SOURCE_ROOTS}
    return {'origin':'OFFLINE_HISTORICAL_CAPACITY_EVIDENCE','live_requests':0,
        'historical_indexed_observations':sum(raw.values()),'historical_global_distinct_url_candidates':len(all_links),
        'historical_per_type':[{'source_type':kind,'raw_observations':raw[kind],
            'distinct_link_candidates':len(links[kind]),'latest_page_state_counts':dict(page_states[kind])} for kind in SOURCE_ROOTS],
        'pending_source_counts':dict(types),'new_literal_sources':len(new),
        'first_new_source_in_each_type':first,'target_unique_links':plan['target_unique_links'],
        'max_requests_per_run':plan['max_requests_per_run'],'max_pages_per_run':plan['max_pages_per_run'],
        'current_verified_unique_capacity':None,'current_capacity_lower_bound':0,'current_capacity_upper_bound':None,
        'target_supply_sufficiency':'UNVERIFIED','fresh_30000_with_basic_QA_within_10h':'NOT_PROVEN',
        'scope_notes':[
            'Historical counts describe saved evidence, not current LIVE availability or permission.',
            'Per-list candidate sets overlap and must not be summed as independent supply.',
            'New literal navigation URLs prove discovery only; their product capacity is unknown.',
            'Page-prefix reach assumes no earlier stop, target hit or ACP budget consumption; it is not guaranteed execution.',
            'Failed, partial and unknown pages retain their states; they do not prove zero source capacity.',
            'T1 must verify current cards/context/ratings; T2 must measure distinct fresh yield and full wall-clock QA before T3 approval.']}


def export_capacity(historical_root, historical_run_id, plan_path, pending_source_pack, output):
    plan_path=Path(plan_path);pack_path=Path(pending_source_pack)
    plan=json.loads(plan_path.read_text(encoding='utf-8-sig'));pack=json.loads(pack_path.read_text(encoding='utf-8-sig'))
    entries=pack.get('sources',pack.get('entries',[]))
    with readonly_store(historical_root) as store:
        pages=store.pages(historical_run_id)
        result=capacity_evidence(plan,pages,store.iter_observations(historical_run_id),new_source_ids=[s['source_id'] for s in entries])
    result.update(historical_run_id=historical_run_id,
        plan_reference={'path':str(plan_path.resolve()),'sha256':digest(plan_path.read_bytes())},
        pending_source_pack_reference={'path':str(pack_path.resolve()),'sha256':digest(pack_path.read_bytes())})
    immutable_json(Path(output),sealed(result));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for flag in ('historical-root','historical-run-id','plan','pending-source-pack','out'):
        parser.add_argument('--'+flag,required=True)
    args=parser.parse_args()
    report=export_capacity(args.historical_root,args.historical_run_id,args.plan,args.pending_source_pack,args.out)
    print(json.dumps({'output':args.out,'historical_global_distinct_url_candidates':report['historical_global_distinct_url_candidates'],
        'target_supply_sufficiency':report['target_supply_sufficiency'],'live_requests':0},ensure_ascii=False))
