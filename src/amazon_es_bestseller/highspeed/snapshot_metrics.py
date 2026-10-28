"""Append-only PAGE accounting. Final acceptance still recomputes full metrics.

No raw records are rewritten. Destination-reviewed runs use the original full
projection; this index applies only to V4 URL grouping without those reviews.
"""
from collections import Counter
from .three_metrics import qualify_three
from .three_navigation import FAMILIES, CONTRACT
from .global_target import is_global_policy, source_targets
from .budgets import aware_time


class SnapshotMetricsIndex:
    def __init__(self, run_id, policy):
        is_global_policy(policy)
        self.run_id = run_id; self.policy = policy; self.excluded = 0; self.invalid = 0
        self.rows = 0
        self.sources = {k: {'raw': 0, 'raw_urls': set(), 'valid_urls': set(), 'urls': set(), 'groups': set(),
            'times': {}, 'fields': {f: Counter() for f in ('rating', 'rating_count', 'monthly_bought', 'price', 'product_title')}}
            for k in FAMILIES}

    def add(self, rows):
        current, qualified, invalid, excluded = qualify_three(rows, self.run_id)
        self.rows += len(rows); self.excluded += excluded; self.invalid += len(invalid)
        for row in current:
            own = self.sources[row['source_type']]; own['raw'] += 1
            if row.get('raw_product_url'): own['raw_urls'].add(row['raw_product_url'])
            if row.get('is_valid'): own['valid_urls'].add(row['normalized_product_url'])
            for field, counts in own['fields'].items(): counts[row.get(field+'_status', 'UNVERIFIED')] += 1
        for row in qualified:
            own = self.sources[row['source_type']]
            group = row['possible_duplicate_key'] or row['normalized_product_url']
            own['urls'].add(row['normalized_product_url']); own['groups'].add(group)
            own['times'].setdefault(aware_time(row['observed_at']), set()).add(group)

    def measure(self):
        targets = source_targets(self.policy); reports = {}; global_groups = set()
        for kind, own in self.sources.items():
            raw = own['raw']; groups = own['groups']; global_groups.update(groups)
            times = own['times']; first = min(times) if times else None; last = max(times) if times else None
            seconds = (last-first).total_seconds() if len(times)>1 else 0
            delta = len(groups)-len(times[first]) if times else 0
            reports[kind] = {'A_raw_observations':raw,'B_unique_raw_product_links':len(own['raw_urls']),
                'C_valid_normalized_URLs':len(own['valid_urls']),'D_unique_normalized_URLs':len(own['urls']),
                'E_qualified_review_groups':len(groups),'target':targets[kind],
                'shortfall':max(0,targets[kind]-len(groups)),'target_met':len(groups)>=targets[kind],
                'G_verified_independent_buyable_SKUs':0,'SKU_identity_status':'NOT_VERIFIED',
                'duplicate_observation_ratio':1-len(own['urls'])/raw if raw else None,
                'field_status_counts':{f:dict(c) for f,c in own['fields'].items()},
                'rating_valid_coverage':own['fields']['rating']['VALID']/raw if raw else None,
                'rating_count_valid_coverage':own['fields']['rating_count']['VALID']/raw if raw else None,
                'monthly_bought_valid_coverage':own['fields']['monthly_bought']['VALID']/raw if raw else None,
                'sample_growth':delta,'sample_active_seconds':seconds,
                'sample_new_groups_per_hour':delta*3600/seconds if seconds>0 else None,
                'rate_scope':'First-to-last capture within this source; initial capture subtracted. No historical seed.'}
        for kind, own in self.sources.items():
            others=set().union(*(s['groups'] for k,s in self.sources.items() if k!=kind))
            reports[kind]['groups_overlapping_other_sources']=len(own['groups'] & others)
            reports[kind]['source_exclusive_groups']=len(own['groups'] - others)
        target=self.policy['target']; n=len(global_groups)
        met=n>=target and all(r['target_met'] for r in reports.values())
        return {'schema_version':1,'contract':CONTRACT,'run_id':self.run_id,'sources':reports,
            'A_raw_observations':sum(s['raw'] for s in self.sources.values()),
            'B_unique_raw_product_links':len(set().union(*(s['raw_urls'] for s in self.sources.values()))),
            'C_valid_normalized_URLs':len(set().union(*(s['valid_urls'] for s in self.sources.values()))),
            'D_global_qualified_normalized_URLs':len(set().union(*(s['urls'] for s in self.sources.values()))),
            'E_sum_source_review_groups':sum(len(s['groups']) for s in self.sources.values()),
            'F_global_conservative_review_groups':n,'G_verified_independent_buyable_SKUs':0,'SKU_identity_status':'NOT_VERIFIED',
            'target_met':met,'target_mode':'GLOBAL_CONSERVATIVE_REVIEW_GROUPS','global_target':target,
            'global_shortfall':max(0,target-n),'source_targets_are_diagnostic_only':False,
            'quantity_goals':{**{k:{'target':targets[k],'actual':reports[k]['E_qualified_review_groups'],
                'shortfall':reports[k]['shortfall'],'met':reports[k]['target_met']} for k in FAMILIES},
                'GLOBAL':{'target':target,'actual':n,'shortfall':max(0,target-n),'met':n>=target}},
            'acceptance':'QUANTITY_TARGET_MET_QA_STILL_REQUIRED' if met else f'NOT_ACCEPTED — GLOBAL_{target}_TARGET_NOT_MET',
            'excluded_noncurrent_or_nonlive_observations':self.excluded,'excluded_unqualified_observations':self.invalid,
            'global_source_completeness':'UNKNOWN','thirty_thousand_supply_demonstrated':False}


def update_from_store(index, store, run_id, cursor):
    import json
    pages = {p['page_key']: p for p in store.pages(run_id)}
    rows = []
    for row in store.db.execute('SELECT rowid,data_json,page_key FROM observations WHERE run_id=? AND rowid>? ORDER BY rowid', (run_id,cursor)):
        cursor = row[0]; value=json.loads(row[1]); page=pages.get(row[2],{})
        value.setdefault('source_evidence_eligible', page.get('access_status')=='NORMAL' and
            page.get('source_type_verified') is True and page.get('source_context_valid') is True)
        if value['source_evidence_eligible'] is False:value['target_eligible']=False
        rows.append(value)
    index.add(rows)
    return cursor
