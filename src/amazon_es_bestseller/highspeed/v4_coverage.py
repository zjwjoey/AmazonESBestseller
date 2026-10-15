"""Observed loading capability and finite component coverage, not source capacity."""
from collections import defaultdict
import json
from pathlib import Path
from .v4_adapters import parse_v4_page


def loading_coverage(pages,sources):
    from ..multisource.storage import read_sealed
    captured={}
    for current in pages:
        history=[]
        if current.get('journal_path'):
            path=Path(current['journal_path'])
            history=[read_sealed(p)['page'] for p in path.parent.glob(path.name.split('_',1)[0]+'_*.json')]
        for page in [*history,current]:
            key=(page['source_id'],page['evidence_sha256'],page['observed_at'])
            old=captured.get(key)
            if old is None or len(page.get('acp_captures',[]))>=len(old.get('acp_captures',[])):
                captured[key]=page
    by_source=defaultdict(list)
    source_map={s['source_id']:s for s in sources}
    components=[]
    for page in captured.values():
        s=source_map[page['source_id']]
        parsed=parse_v4_page(Path(page['evidence_path']).read_text(encoding='utf8'),source=s,
            page_url=page['page_url'],run_id=page['run_id'],observed_at=page['observed_at'],
            evidence_path=page['evidence_path'],http_status=page.get('http_status'),origin=page['origin'],
            acp_responses=[{**x,'body':Path(x['evidence_path']).read_text(encoding='utf8')} for x in page.get('acp_captures',[])],
            parser_profile_version=page['parser_version'])
        rows=parsed['observations']
        links={r['normalized_product_url'] for r in rows if r.get('is_valid')}
        by_source[s['source_id']].append((page,links,rows))
        for group in parsed['page'].get('legacy_page',{}).get('carousel_groups',[]):
            ranks={r['ranking_position'] for r in rows if r.get('rank_context')==group['ranking_context_url']
                and r.get('ranking_position') is not None and r.get('is_valid') and r.get('product_title_raw')}
            expected=set(range(1,group['expected_count']+1))
            components.append({'source_id':s['source_id'],'ranking_context_url':group['ranking_context_url'],
                'expected_metadata_slots':group['expected_count'],'observed_unique_ranks':len(ranks),
                'missing_ranks':sorted(expected-ranks),'outside_metadata_ranks':sorted(ranks-expected),
                'status':'COMPLETE_WITHIN_OBSERVED_METADATA' if ranks==expected else 'PARTIAL',
                'global_category_capacity_confirmed':False})
    capabilities={k:False for k in ('native_best_continuation','native_new_continuation','favorites_pagination','deals_loading','haul_loading')}
    evidence=[]
    for sid,frames in by_source.items():
        s=source_map[sid]
        for page,links,rows in frames:
            if s['source_type'] in {'BEST_SELLERS','NEW_RELEASES'}:
                max_rank=max((r.get('ranking_position') or 0 for r in rows if r.get('representation','').startswith('ACP')),default=0)
                if max_rank>16 and len(page.get('acp_captures',[]))>=2:
                    k='native_best_continuation' if s['source_type']=='BEST_SELLERS' else 'native_new_continuation'
                    capabilities[k]=True
                    evidence.append({'capability':k,'page_url':page['page_url'],'source_id':sid,
                        'maximum_actual_ACP_rank':max_rank,'evidence_sha256':page['evidence_sha256']})
            if page.get('rendered_derivation'):
                root=s['actual_source_url']
                derivation=page['rendered_derivation']
                initial=next((f for f in frames if not f[0].get('rendered_derivation') and f[0]['page_url']==root
                    and f[0]['evidence_sha256']==derivation['root_html_sha256']
                    and f[0]['observed_at']==derivation['root_observed_at']),None)
                if initial and links-initial[1]:
                    k='deals_loading' if s['source_type']=='DEALS' else 'haul_loading'
                    capabilities[k]=True
                    evidence.append({'capability':k,'source_id':sid,'new_links':len(links-initial[1]),
                        'evidence_sha256':page['evidence_sha256'],'dynamic_receipt_references':page['rendered_derivation']['dynamic_receipt_references']})
        if s['source_subtype']=='CUSTOMER_FAVORITES_FILTERED_SEARCH':
            for page,links,rows in frames:
                previous=[f for f in frames if f[0]['page_url']==s['actual_source_url'] and f[0]['observed_at']<=page['observed_at']]
                root=max(previous,key=lambda f:f[0]['observed_at']) if previous else None
                if root and page['page_url']!=s['actual_source_url'] and links-root[1]:
                    capabilities['favorites_pagination']=True
                    evidence.append({'capability':'favorites_pagination','source_id':sid,'page_url':page['page_url'],
                        'new_links':len(links-root[1]),'evidence_sha256':page['evidence_sha256']})
    return {'capabilities':capabilities,'all_loading_capabilities_observed':all(capabilities.values()),
        'finite_component_coverage':components,'evidence':evidence,
        'global_source_completeness':'UNKNOWN','capacity_verified':False}
