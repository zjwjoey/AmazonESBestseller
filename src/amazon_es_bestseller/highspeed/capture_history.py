"""Read-only binding of retained LIVE rows to their own immutable page captures."""
from pathlib import Path
from ..multisource.storage import digest,read_sealed


def live_observation_capture_pages(store,run_id,rows):
    """Resolve indexed revisions by original raw hash, never newest slot/parent.

    Identical journals for one capture are equivalent; different parent captures
    for the same raw hash are ambiguous and rejected. No evidence is rewritten.
    """
    from .v3_workflows import validate_live_receipt
    run=store.get_run(run_id)
    if run['plan'].get('snapshot_contract_version')!=3 or run['plan'].get('origin')!='LIVE_CAPTURE':
        raise ValueError('LIVE_CAPTURE_HISTORY_SCOPE_REQUIRED')
    sources={s['source_id']:s for s in run['plan']['sources']}
    needed={(r['source_id'],r['raw_sha256']) for r in rows}
    bindings={};bodies={}
    for path in sorted((store.root/'runs'/run_id/'pages').glob('*.json')):
        journal=read_sealed(path)
        if journal.get('run_id')!=run_id:raise ValueError('LIVE_CAPTURE_HISTORY_JOURNAL_RUN_CONFLICT')
        page=journal['page'];sid=page.get('source_id');source=sources.get(sid)
        relevant=[raw for raw in journal['observations'] if (sid,digest(raw)) in needed]
        if not relevant:continue
        if (not source or page.get('source_type')!=source['source_type']
                or page.get('page_url') not in source['page_urls'] or page.get('origin')!='LIVE_CAPTURE'):
            raise ValueError('LIVE_CAPTURE_HISTORY_SOURCE_CONFLICT')
        evidence=Path(page['evidence_path']).resolve();sha=page['evidence_sha256']
        if (str(evidence),sha) not in bodies:bodies[(str(evidence),sha)]=digest(evidence.read_bytes())==sha
        if not bodies[(str(evidence),sha)]:raise ValueError('LIVE_CAPTURE_HISTORY_PARENT_BODY_HASH_CONFLICT')
        parent={**page,'page_key':journal['page_key'],'journal_path':str(path.resolve())}
        capture=(sid,page['page_url'],str(evidence),sha,page['observed_at'],journal['page_key'])
        for raw in relevant:
            key=(sid,digest(raw))
            if (raw.get('source_id')!=sid or raw.get('source_type')!=source['source_type']
                    or raw.get('page_url')!=page['page_url'] or raw.get('origin')!='LIVE_CAPTURE'):
                raise ValueError('LIVE_CAPTURE_HISTORY_RAW_SOURCE_CONFLICT')
            if not validate_live_receipt(raw,source):raise ValueError('LIVE_CAPTURE_HISTORY_RECEIPT_CONFLICT')
            receipt=read_sealed(raw['capture_receipt_reference']['path'])
            if receipt.get('page_key')!=journal['page_key']:
                raise ValueError('LIVE_CAPTURE_HISTORY_RECEIPT_PAGE_CONFLICT')
            if raw.get('representation')=='SERVER' and (raw.get('evidence_sha256')!=sha
                    or Path(raw['evidence_path']).resolve()!=evidence or raw['observed_at']!=page['observed_at']):
                raise ValueError('LIVE_CAPTURE_HISTORY_SERVER_PARENT_CONFLICT')
            if str(raw.get('representation','')).startswith('ACP_'):
                descriptor=receipt.get('native_acp_descriptor') or {}
                binding=descriptor.get('ranking_context_binding') or {}
                if (descriptor.get('request_contract_parent_page_url')!=page['page_url']
                        or Path(descriptor.get('evidence_path','')).resolve()!=evidence
                        or binding.get('page_evidence_sha256')!=sha or binding.get('source_id')!=sid
                        or binding.get('ranking_context_url')!=raw.get('rank_context')):
                    raise ValueError('LIVE_CAPTURE_HISTORY_ACP_PARENT_CONFLICT')
            if key in bindings and bindings[key][0]!=capture:raise ValueError('LIVE_CAPTURE_HISTORY_AMBIGUOUS_PARENT')
            bindings[key]=(capture,parent,raw)
    result={}
    for row in rows:
        key=(row['source_id'],row['raw_sha256'])
        if key not in bindings:raise ValueError('LIVE_CAPTURE_HISTORY_ORIGINAL_JOURNAL_MISSING:'+row['observation_id'])
        _,parent,raw=bindings[key]
        if row['observation_id']!=raw['observation_id'] and row.get('parent_observation_id')!=raw['observation_id']:
            raise ValueError('LIVE_CAPTURE_HISTORY_REVISION_ID_CONFLICT')
        fields=('raw_product_url','ranking_position','rank_context','representation','representation_slot',
                'evidence_path','evidence_sha256','observed_at','source_id','source_type','page_url','capture_receipt_reference')
        if any(row.get(k)!=raw.get(k) for k in fields):raise ValueError('LIVE_CAPTURE_HISTORY_INDEX_RAW_CONFLICT')
        if not validate_live_receipt(row,sources[row['source_id']]):raise ValueError('LIVE_CAPTURE_HISTORY_INDEX_RECEIPT_CONFLICT')
        result[row['observation_id']]=parent
    return result
