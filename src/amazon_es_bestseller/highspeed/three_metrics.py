"""A--G campaign accounting. Source targets are independent of global overlap."""
from collections import Counter, defaultdict
from .three_navigation import FAMILIES, CONTRACT
from .v3_links import normalize_product_url_v3
from .v4_quality import build_candidates_v4
from .adapters import _non_title_text
from .budgets import aware_time


def qualify_three(observations, run_id, *, allowed_run_ids=None):
    allowed={run_id} if allowed_run_ids is None else set(allowed_run_ids)
    if not allowed or run_id not in allowed:raise ValueError('THREE_EXPLICIT_VERIFIED_BATCH_IDS_REQUIRED')
    current=[]; excluded=0
    for row in observations:
        if row.get('run_id') not in allowed or row.get('origin')!='LIVE_CAPTURE':
            excluded+=1;continue
        if row.get('source_type') not in FAMILIES:raise ValueError('THREE_FOREIGN_SOURCE')
        current.append(row)
    qualified=[];invalid=[]
    for row in current:
        norm=normalize_product_url_v3(row.get('raw_product_url'),row['page_url'])
        if (norm['is_valid'] and norm['normalized_product_url']==row.get('normalized_product_url')
                and row.get('product_title_es') and not _non_title_text(row['product_title_es'])
                and not row.get('binding_doubts') and row.get('source_evidence')
                and row.get('source_evidence_eligible') is not False and row.get('target_eligible') is not False
                and row.get('snapshot_eligible') is not False
                and row.get('evidence_path') and row.get('evidence_sha256')):
            # Recompute conservative identity with the current normalizer;
            # never trust a persisted or externally supplied group-count flag.
            qualified.append({**row,'possible_duplicate_key':norm['possible_duplicate_key']})
        else:invalid.append(row)
    return current,qualified,invalid,excluded


def measure_three(observations, run_id, *, allowed_run_ids=None, target_policy=None):
    from .global_target import is_global_policy, source_targets
    global_mode=is_global_policy(target_policy)
    targets=source_targets(target_policy)
    global_target=target_policy['target'] if global_mode else None
    if global_mode and allowed_run_ids is not None and set(allowed_run_ids)!={run_id}:
        raise ValueError('GLOBAL_FRESH_SINGLE_RUN_REQUIRED')
    current,qualified,invalid,excluded=qualify_three(observations,run_id,allowed_run_ids=allowed_run_ids)
    source_reports={};all_groups=set();source_groups={};source_urls={}
    for kind in FAMILIES:
        raw=[r for r in current if r['source_type']==kind]
        good=[r for r in qualified if r['source_type']==kind]
        candidates=build_candidates_v4(good)
        groups={c['target_review_group'] for c in candidates if c['eligible_for_target']}
        urls={c['normalized_product_url'] for c in candidates}
        source_groups[kind]=groups;source_urls[kind]=urls;all_groups.update(groups)
        batches=defaultdict(set)
        for r in good:batches[aware_time(r['observed_at'])].add(r['possible_duplicate_key'])
        seen=set();samples=[]
        for t,keys in sorted(batches.items()):seen.update(keys);samples.append((t,len(seen)))
        seconds=(samples[-1][0]-samples[0][0]).total_seconds() if len(samples)>1 else 0
        delta=samples[-1][1]-samples[0][1] if samples else 0
        source_reports[kind]={'A_raw_observations':len(raw),
            'B_unique_raw_product_links':len({r['raw_product_url'] for r in raw if r.get('raw_product_url')}),
            'C_valid_normalized_URLs':len({r['normalized_product_url'] for r in raw if r.get('is_valid')}),
            'D_unique_normalized_URLs':len(urls),'E_qualified_review_groups':len(groups),
            'target':targets[kind],'shortfall':max(0,targets[kind]-len(groups)),'target_met':len(groups)>=targets[kind],
            'G_verified_independent_buyable_SKUs':0,'SKU_identity_status':'NOT_VERIFIED',
            'duplicate_observation_ratio':1-len(urls)/len(raw) if raw else None,
            'field_status_counts':{f:dict(Counter(r.get(f+'_status','UNVERIFIED') for r in raw))
                for f in ('rating','rating_count','monthly_bought','price','product_title')},
            'rating_valid_coverage':sum(r.get('rating_status')=='VALID' for r in raw)/len(raw) if raw else None,
            'rating_count_valid_coverage':sum(r.get('rating_count_status')=='VALID' for r in raw)/len(raw) if raw else None,
            'monthly_bought_valid_coverage':sum(r.get('monthly_bought_status')=='VALID' for r in raw)/len(raw) if raw else None,
            'sample_growth':delta,'sample_active_seconds':seconds,
            'sample_new_groups_per_hour':delta*3600/seconds if seconds>0 else None,
            'rate_scope':'First-to-last capture within this source; initial capture subtracted. No historical seed.'}
    for kind in FAMILIES:
        others=set().union(*(source_groups[k] for k in FAMILIES if k!=kind))
        source_reports[kind]['groups_overlapping_other_sources']=len(source_groups[kind]&others)
        source_reports[kind]['source_exclusive_groups']=len(source_groups[kind]-others)
    source_met=all(r['target_met'] for r in source_reports.values())
    met=source_met and (len(all_groups)>=global_target if global_mode else True)
    return {'schema_version':1,'contract':CONTRACT,'run_id':run_id,'sources':source_reports,
        'A_raw_observations':len(current),
        'B_unique_raw_product_links':len({r['raw_product_url'] for r in current if r.get('raw_product_url')}),
        'C_valid_normalized_URLs':len({r['normalized_product_url'] for r in current if r.get('is_valid')}),
        'D_global_qualified_normalized_URLs':len(set().union(*source_urls.values())),
        'E_sum_source_review_groups':sum(len(g) for g in source_groups.values()),
        'F_global_conservative_review_groups':len(all_groups),'G_verified_independent_buyable_SKUs':0,
        'SKU_identity_status':'NOT_VERIFIED','target_met':met,
        **({'target_mode':'GLOBAL_CONSERVATIVE_REVIEW_GROUPS','global_target':global_target,
            'global_shortfall':max(0,global_target-len(all_groups)),
            'source_targets_are_diagnostic_only':False,
            'quantity_goals':{**{k:{'target':targets[k],'actual':source_reports[k]['E_qualified_review_groups'],
                'shortfall':source_reports[k]['shortfall'],'met':source_reports[k]['target_met']} for k in FAMILIES},
                'GLOBAL':{'target':global_target,'actual':len(all_groups),'shortfall':max(0,global_target-len(all_groups)),'met':len(all_groups)>=global_target}}} if global_mode else {}),
        'acceptance':('QUANTITY_TARGET_MET_QA_STILL_REQUIRED' if met else
            f'NOT_ACCEPTED — GLOBAL_{global_target}_TARGET_NOT_MET' if global_mode else 'NOT_ACCEPTED — THREE_SOURCE_5001_TARGET_NOT_MET'),
        'excluded_noncurrent_or_nonlive_observations':excluded,'excluded_unqualified_observations':len(invalid),
        'global_source_completeness':'UNKNOWN','thirty_thousand_supply_demonstrated':False}
