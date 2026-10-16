"""Serial link-first runner with immutable receipts before any parsing."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import uuid
from datetime import datetime, timezone
from contextlib import nullcontext, ExitStack
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from ..access.detector import detect_access_status
from ..multisource.storage import file_lock, read_sealed, atomic_json, immutable_json, sealed, digest
from .authorization import authorize_request, validate_authorization
from .budgets import aware_time, budget_reason, utc_text, validate_plan
from .performance import RollingProgress
from .links import NORMALIZER_VERSION, normalize_product_url
from ..multisource.contracts import SOURCE_ROOTS, canonical_page_url


def _next_acp_pending(pending, excluded, *, v3=False):
    rows = pending if isinstance(pending, list) else [pending] if pending else []
    return next((row for row in rows if not (v3 and (row.get("metadata") or {}).get("carousel")
        and (row.get("metadata") or {}).get("ranking_context_url") in excluded)), None)

def source_navigation_only(source, url, parsed):
    """A verified empty root may expose navigation, never valid product output."""
    page = parsed["page"]
    if (page.get("parser_version", "").startswith("link_first_adapters_v3.")
            and page.get("page_content_state") != "NAVIGATION_ONLY"):
        return False
    actual = canonical_page_url(url).rstrip("/")
    root = SOURCE_ROOTS.get(source.get("source_type"), "").rstrip("/")
    eligible = bool(page.get("status") == "FAILED"
        and page.get("access_status") == "NORMAL" and page.get("source_type_verified") is True
        and page.get("source_context_valid") is True and not parsed.get("observations")
        and page.get("raw_card_count", 0) == 0
        and set(page.get("flags", [])) == {"PAGE_CONTENT_MISSING"}
        and page.get("expected_count") is None
        and not (parsed.get("acp_pending") or page.get("acp_pending")))
    if not eligible:
        return False
    if actual == root:
        return True
    # A category landing page needs affirmative, saved navigation evidence.
    # Missing cards alone must never suppress an ordinary leaf parser failure.
    try:
        html = Path(page["evidence_path"]).read_text(encoding="utf-8")
    except (OSError, KeyError, UnicodeError):
        return False
    if hashlib.sha256(html.encode("utf-8")).hexdigest() != page.get("evidence_sha256"):
        return False
    soup = BeautifulSoup(html, "lxml")
    left, right = soup.select_one("#zg-left-col"), soup.select_one("#zg-right-col")
    if (left is None or right is None or right.find(True) is not None
            or right.get_text(strip=True)
            or soup.select_one("[data-expected-count], [data-client-recs-list], [data-acp-path]")):
        return False
    return any(anchor.get_text(strip=True)
        and canonical_page_url(urljoin(url, anchor["href"])).rstrip("/").startswith(actual+"/")
        for anchor in left.select("a[href]"))

def continuation_unavailable(parsed, receipt, reason, gate):
    """A bounded native discovery miss is source incompleteness, not success."""
    page = parsed["page"]
    return bool(reason == "ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED"
        and receipt.get("status_code") == 200 and receipt.get("access_state") == "NORMAL"
        and gate.get("state") == "NORMAL" and page.get("access_status") == "NORMAL"
        and page.get("source_type_verified") is True and page.get("source_context_valid") is True
        and parsed.get("observations") and page.get("actual_count", 0) > 0)

def empty_continuation_unavailable(parsed, receipt, reason, gate):
    """Zero capacity stays FAILED after a proven bounded native discovery miss."""
    page=parsed["page"]
    return bool(reason=="ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED"
        and receipt.get("status_code")==200 and receipt.get("access_state")=="NORMAL"
        and gate.get("state")=="NORMAL" and page.get("access_status")=="NORMAL"
        and page.get("source_type_verified") is True and page.get("source_context_valid") is True
        and not parsed.get("observations") and page.get("actual_count")==0
        and page.get("raw_card_count")==0 and (parsed.get("acp_pending") or page.get("acp_pending")))

def explicit_source_empty(parsed):
    """Accept only a documented official empty list component."""
    page=parsed["page"]
    phrases={"MOST_WISHED_FOR":"de los más deseados","MOST_GIFTED":"de los más regalados",
        "BEST_SELLERS":"más vendidos"}
    if (page.get("source_type") not in phrases or page.get("status")!="FAILED"
            or page.get("access_status")!="NORMAL" or page.get("source_type_verified") is not True
            or page.get("source_context_valid") is not True or parsed.get("observations")
            or page.get("actual_count")!=0 or page.get("raw_card_count")!=0
            or page.get("expected_count") is not None or parsed.get("acp_pending") or page.get("acp_pending")
            or set(page.get("flags",[]))!={"PAGE_CONTENT_MISSING"}):
        return False
    try:
        html=Path(page["evidence_path"]).read_text(encoding="utf-8")
        if hashlib.sha256(html.encode("utf-8")).hexdigest()!=page.get("evidence_sha256"):return False
        soup=BeautifulSoup(html,"lxml")
        grids=soup.select("#zg-right-col .p13n-desktop-grid")
        if len(grids)!=1:return False
        grid=grids[0]
        if json.loads(grid.get("data-client-recs-list","null"))!=[]:return False
    except (OSError,UnicodeError,ValueError,KeyError):
        return False
    message="Lo sentimos, no hay productos "+phrases[page["source_type"]]+" disponibles en esta categoría. Vuelve más tarde."
    if (not any(" ".join(h.get_text(" ",strip=True).split())==message for h in grid.find_all("h4",recursive=False))
            or grid.select_one("#endOfList") is None
            or grid.select_one("[data-expected-count], [data-acp-path], .zg-bdg-text, [data-ranking-card], .ranking-card, #gridItemRoot")):
        return False
    return not any(normalize_product_url(a["href"],base_url=page["page_url"])["is_valid"] for a in grid.select("a[href]"))

def _probe_scope_summary(plan, bundle, committed, reason):
    expected = {(s['source_id'],url) for s in plan['sources'] for url in s['page_urls']}
    expected_keys = {hashlib.sha256((sid+'\0'+url).encode()).hexdigest() for sid,url in expected}
    attempted = {(r.get('source_id'),r.get('request_url')) for r in bundle.get('responses', [])
        if r.get('kind') == 'PAGE' and (r.get('source_id'),r.get('request_url')) in expected}
    processed = {pair for pair in expected if hashlib.sha256((pair[0]+'\0'+pair[1]).encode()).hexdigest() in committed}
    complete_pages = {p['page_key'] for p in bundle.get('pages', [])
        if p.get('status') == 'COMPLETE' and p.get('page_key') in expected_keys}
    return {'expected_page_count':len(expected),'attempted_page_count':len(attempted),
        'processed_page_count':len(processed),'complete_page_count':len(complete_pages),
        'complete':attempted == expected and processed == expected and reason == 'SOURCE_PLAN_EXHAUSTED',
        'missing_page_scopes':[{'source_id':sid,'page_url':url} for sid,url in sorted(expected-processed)],
        'scope_note':'Probe completion is separate from COMPLETE page status and stage QA; restrictions and unknown/parser failures still stop.'}


def reviewed_t1_reverification(store, plan, authorization, gate, *, now=None):
    """Read-only validation of one explicit same-run fresh PAGE review."""
    reference=(authorization or {}).get('t1_reverification_review')
    if not reference:return None
    if (plan.get('snapshot_contract_version')!=3 or plan.get('stage')!='T1'
            or plan.get('origin')!='LIVE_CAPTURE' or plan.get('probe_all_reviewed_page_urls') is not True
            or gate.get('state')!='NORMAL'):
        raise ValueError('T1_REVERIFICATION_SCOPE_OR_GATE_INVALID')
    def exact(reference):
        if not isinstance(reference,dict) or not reference.get('path') or not reference.get('sha256'):
            raise ValueError('T1_REVERIFICATION_REFERENCE_REQUIRED')
        path=Path(reference['path']).resolve()
        if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:
            raise ValueError('T1_REVERIFICATION_REFERENCE_HASH_MISMATCH')
        return path
    review=read_sealed(exact(reference));current=aware_time(now)
    run=store.get_run(plan['run_id'])
    flags=('preserve_evidence','preserve_cumulative_budget','fresh_PAGE_once_per_source',
        'no_source_expansion','reserve_PAGE_budget_for_all_reviewed_scopes')
    if (review.get('kind')!='T1_FRESH_REVERIFICATION_APPROVAL' or review.get('approved') is not True
            or review.get('approved_by')!='user_in_current_conversation' or not review.get('approval_id')
            or review.get('run_id')!=plan['run_id'] or review.get('store_root')!=str(store.root.resolve())
            or any(review.get(k) is not True for k in flags)
            or not aware_time(review['reviewed_at'])<=current<aware_time(review['expires_at'])<=aware_time(authorization['expires_at'])
            or review.get('original_started_at')!=run.get('started_at')
            or aware_time(review['reviewed_at'])<aware_time(run['started_at'])
            or run.get('plan')!=plan or review.get('plan_sha256')!=digest(plan)):
        raise ValueError('T1_REVERIFICATION_USER_OR_IMMUTABLE_RUN_BINDING_INVALID')
    plan_path=exact(review.get('plan_reference'))
    raw=json.loads(plan_path.read_text(encoding='utf-8-sig'))
    if ('payload' in raw and read_sealed(plan_path)!=plan) or ('payload' not in raw and raw!=plan):
        raise ValueError('T1_REVERIFICATION_PLAN_REFERENCE_MISMATCH')
    for field in ('max_requests_per_run','max_pages_per_run'):
        if type(review.get(field)) is not int or review[field]!=plan[field] or authorization.get(field)!=plan[field]:
            raise ValueError('T1_REVERIFICATION_CUMULATIVE_CAP_MISMATCH')
    expected={(s['source_id'],s['source_type'],tuple(s['page_urls'])) for s in plan['sources']}
    scopes=review.get('source_scopes',[])
    reviewed={(s.get('source_id'),s.get('source_type'),tuple(s.get('page_urls',[]))) for s in scopes}
    pairs={(sid,url) for sid,kind,urls in expected for url in urls}
    if reviewed!=expected or len(scopes)!=len(expected) or len(pairs)!=9:
        raise ValueError('T1_REVERIFICATION_EXACT_NINE_SOURCE_SCOPES_REQUIRED')
    prior_refs=review.get('prior_page_journal_references',[]);prior={}
    for item in prior_refs:
        journal=read_sealed(exact(item));page=journal.get('page',{})
        pair=(page.get('source_id'),page.get('page_url'))
        source=next((s for s in plan['sources'] if s['source_id']==pair[0]),None)
        if (pair not in pairs or pair in prior or not source or journal.get('run_id')!=plan['run_id']
                or page.get('source_type')!=source['source_type']
                or journal.get('page_key')!=hashlib.sha256((pair[0]+'\0'+pair[1]).encode()).hexdigest()
                or aware_time(page['observed_at'])>=aware_time(review['reviewed_at'])):
            raise ValueError('T1_REVERIFICATION_PRIOR_JOURNAL_SCOPE_INVALID')
        prior[pair]=(journal,item)
    if set(prior)!=pairs:raise ValueError('T1_REVERIFICATION_PRIOR_NINE_JOURNALS_REQUIRED')
    sources={s['source_id']:s for s in plan['sources']};rules={}
    for rule in review.get('retained_failed_source_rules',[]):
        pair=(rule.get('source_id'),rule.get('page_url'));source=sources.get(pair[0])
        if (pair in rules or pair not in prior or not source or source['source_type']!='MOVERS_SHAKERS'
                or rule.get('source_type')!='MOVERS_SHAKERS' or pair[1]!=SOURCE_ROOTS['MOVERS_SHAKERS']
                or rule.get('retained_state')!='PARSER_EMPTY_UNVERIFIED'
                or rule.get('prior_failure_journal_reference')!=prior[pair][1]):
            raise ValueError('T1_REVERIFICATION_RETAINED_FAILURE_SCOPE_INVALID')
        old=prior[pair][0]['page'];body_path=exact(rule.get('prior_html_reference'))
        if (Path(old.get('evidence_path','')).resolve()!=body_path or old.get('evidence_sha256')!=rule['prior_html_reference']['sha256']
                or old.get('status')!='FAILED' or old.get('page_content_state')!='PARSER_EMPTY_UNVERIFIED'
                or old.get('actual_count')!=0 or old.get('page_complete') is not False):
            raise ValueError('T1_REVERIFICATION_OLD_FAILURE_EVIDENCE_INVALID')
        from .adapters import parse_page
        parsed=parse_page(body_path.read_text(encoding='utf-8'),source=source,run_id=plan['run_id'],
            page_url=pair[1],page_number=1,observed_at=old['observed_at'],evidence_path=str(body_path),
            origin='LIVE_CAPTURE',http_status=200)
        if not _same_reviewed_move_failure(parsed) or not _same_move_empty_dom(body_path.read_text(encoding='utf-8')):
            raise ValueError('T1_REVERIFICATION_OLD_FAILURE_REPARSE_INVALID')
        prior_receipts=store.load_run(plan['run_id']).get('responses',[])
        if not any(r.get('kind')=='PAGE' and r.get('origin')=='LIVE_CAPTURE'
                and r.get('source_id')==pair[0] and r.get('request_url')==pair[1] and r.get('final_url')==pair[1]
                and r.get('status_code')==200 and r.get('access_state')=='NORMAL'
                and r.get('observed_at')==old['observed_at']
                and (r.get('sha256') or r.get('html_sha256'))==rule['prior_html_reference']['sha256'] for r in prior_receipts):
            raise ValueError('T1_REVERIFICATION_OLD_FAILURE_NORMAL_CAPTURE_REQUIRED')
        rules[pair]={**rule,'reviewed_shell_text':_move_shell_text(body_path.read_text(encoding='utf-8'))}
    bundle=store.load_run(plan['run_id']);attempted=set();fresh_receipts={}
    for request in bundle.get('requests',[]):
        if request.get('kind')=='PAGE' and aware_time(request['observed_at'])>=aware_time(review['reviewed_at']):
            pair=(request.get('source_id'),request.get('url'))
            if pair in pairs:attempted.add(pair)
    for receipt in bundle.get('responses',[]):
        if receipt.get('kind')=='PAGE' and aware_time(receipt['observed_at'])>=aware_time(review['reviewed_at']):
            pair=(receipt.get('source_id'),receipt.get('request_url'))
            if pair in pairs:
                attempted.add(pair)
                if receipt.get('status_code')==200 and receipt.get('access_state')=='NORMAL':fresh_receipts[pair]=receipt
    return {'review':review,'reference':reference,'pairs':pairs,'attempted':attempted,
        'fresh_receipts':fresh_receipts,'retained_rules':rules}


def _move_shell_text(html):
    root=BeautifulSoup(html,'lxml').select_one('#zg')
    return ' '.join(root.get_text(' ',strip=True).split()) if root is not None else None


def _same_move_empty_dom(html):
    """The reviewed real MOVE empty shell, not a generic parser miss."""
    soup=BeautifulSoup(html,'lxml')
    roots=soup.select('#zg');right=soup.select('#zg-right-col');left=soup.select('#zg-left-col')
    if len(roots)!=1 or len(right)!=1 or len(left)!=1:return False
    if right[0].find(recursive=False) is not None or right[0].get_text(' ',strip=True):return False
    if ' '.join(left[0].get_text(' ',strip=True).split())!='Cualquier departamento (Current)' or left[0].select('a[href]'):return False
    for node in [roots[0],*roots[0].find_all(True)]:
        if any(k.startswith(('data-acp','data-a-carousel','data-client-recs','data-ranking','data-expected-count','data-asin')) for k in node.attrs):return False
    if roots[0].select('.zg-bdg-text,#gridItemRoot,.ranking-card,.rank-card'):return False
    known_shell_metrics={'p13n-zg-nav-tab-all_zeitgeist-lists_0',
        'p13n-zg-banner-landing-page-header_zeitgeist-lists_3',
        'p13n-zg-nav-tree-all_zeitgeist-lists_1','p13n-zg-banner-landing-page-footer_zeitgeist-lists_4'}
    if any(n.get('data-card-metrics-id') not in known_shell_metrics for n in roots[0].select('[data-card-metrics-id]')):return False
    return not any(normalize_product_url(a['href'],base_url=SOURCE_ROOTS['MOVERS_SHAKERS'])['is_valid'] for a in roots[0].select('a[href]'))


def _same_reviewed_move_failure(parsed):
    p=parsed['page']
    return (p.get('access_status')=='NORMAL' and p.get('source_type_verified') is True
        and p.get('source_context_valid') is True and p.get('status')=='FAILED'
        and p.get('page_content_state')=='PARSER_EMPTY_UNVERIFIED' and p.get('actual_count')==0
        and p.get('raw_card_count')==0 and p.get('page_complete') is False
        and set(p.get('flags',[]))=={'PAGE_CONTENT_MISSING'} and not parsed.get('observations') and not parsed.get('acp_pending'))


def reviewed_t1_diagnostic_failures(store, plan, authorization, gate, *, now=None):
    """Validate exact user-reviewed failed-page exclusions for this T1 probe.

    This returns scheduling decisions only. Failed pages, original journals,
    completeness, target counts and stage QA are never changed or promoted.
    """
    references=(authorization or {}).get('t1_diagnostic_failure_reviews',[])
    if not references:return []
    if (plan.get('snapshot_contract_version')!=3 or plan.get('stage')!='T1'
            or plan.get('origin')!='LIVE_CAPTURE' or plan.get('probe_all_reviewed_page_urls') is not True
            or gate.get('state')!='NORMAL'):
        raise ValueError('T1_DIAGNOSTIC_REVIEW_SCOPE_OR_GATE_INVALID')
    current=aware_time(now);auth_expiry=aware_time(authorization['expires_at'])
    pages={p['page_key']:p for p in store.pages(plan['run_id'])}
    sources={s['source_id']:s for s in plan['sources']}
    receipts=list(store.iter_responses(plan['run_id']))
    def exact_ref(reference):
        if not isinstance(reference,dict) or not reference.get('path') or not reference.get('sha256'):
            raise ValueError('T1_DIAGNOSTIC_REFERENCE_REQUIRED')
        path=Path(reference['path']).resolve()
        if hashlib.sha256(path.read_bytes()).hexdigest()!=reference['sha256']:
            raise ValueError('T1_DIAGNOSTIC_REFERENCE_HASH_MISMATCH')
        return path
    result=[];seen=set()
    for reference in references:
        review=read_sealed(exact_ref(reference))
        if (review.get('kind')!='T1_DIAGNOSTIC_FAILED_PAGE_RETAINED_APPROVAL'
                or review.get('approved') is not True or review.get('approved_by')!='user_in_current_conversation'
                or not review.get('approval_id') or review.get('run_id')!=plan['run_id']
                or str(Path(review.get('store_root') or '').resolve())!=str(store.root.resolve())
                or any(review.get(k) is not True for k in ('preserve_failed_unknown','no_quality_gate_relaxation','no_extra_source_scope'))
                or not aware_time(review['reviewed_at'])<=current<aware_time(review['expires_at'])<=auth_expiry):
            raise ValueError('T1_DIAGNOSTIC_USER_REVIEW_INVALID')
        page=pages.get(review.get('page_key'));source=sources.get(review.get('source_id'))
        if (not page or not source or review['page_key'] in seen or source['source_type']!='MOVERS_SHAKERS'
                or review.get('source_type')!=source['source_type'] or page.get('source_id')!=source['source_id']
                or page.get('source_type')!=source['source_type'] or page.get('origin')!='LIVE_CAPTURE'
                or review.get('page_url')!=page.get('page_url') or review['page_url'] not in source['page_urls']
                or review['page_url']!=SOURCE_ROOTS['MOVERS_SHAKERS']
                or page.get('status')!='FAILED' or page.get('page_content_state')!='PARSER_EMPTY_UNVERIFIED'
                or page.get('page_complete') is not False or page.get('actual_count')!=0
                or page.get('raw_card_count')!=0 or set(page.get('flags',[]))!={'PAGE_CONTENT_MISSING'}
                or page.get('source_type_verified') is not True or page.get('source_context_valid') is not True
                or page.get('access_status')!='NORMAL'):
            raise ValueError('T1_DIAGNOSTIC_FAILED_PAGE_BINDING_INVALID')
        journal_path=exact_ref(review.get('original_page_journal_reference'))
        html_path=exact_ref(review.get('html_reference'))
        receipt_path=exact_ref(review.get('capture_receipt_reference'))
        if (journal_path!=Path(page['journal_path']).resolve() or html_path!=Path(page['evidence_path']).resolve()
                or review['html_reference']['sha256']!=page['evidence_sha256']):
            raise ValueError('T1_DIAGNOSTIC_ORIGINAL_EVIDENCE_BINDING_INVALID')
        journal=read_sealed(journal_path);proof=read_sealed(receipt_path)
        indexed=next((r for r in receipts if Path(r['evidence_path']).resolve()==receipt_path),None)
        if (journal.get('run_id')!=plan['run_id'] or journal.get('page_key')!=page['page_key']
                or journal.get('observations')!=[] or not indexed
                or any(journal['page'].get(k)!=page.get(k) for k in ('source_id','source_type','page_url','evidence_path','evidence_sha256','observed_at','status','actual_count','raw_card_count','origin'))
                or any(proof.get(k)!=indexed.get(k) for k in ('run_id','source_id','source_type','html_path','sha256','observed_at','kind','page_key','origin'))
                or proof.get('run_id')!=plan['run_id'] or proof.get('origin')!='LIVE_CAPTURE'
                or proof.get('source_id')!=source['source_id'] or proof.get('source_type')!=source['source_type']
                or proof.get('page_key')!=page['page_key'] or proof.get('kind')!='PAGE'
                or proof.get('request_url')!=review['page_url'] or proof.get('final_url')!=review['page_url']
                or proof.get('status_code')!=200 or proof.get('access_state')!='NORMAL'
                or proof.get('observed_at')!=page['observed_at'] or Path(proof['html_path']).resolve()!=html_path
                or proof.get('sha256')!=review['html_reference']['sha256']):
            raise ValueError('T1_DIAGNOSTIC_ORIGINAL_CAPTURE_INVALID')
        from .adapters import parse_page
        checked=parse_page(html_path.read_text(encoding='utf8'),source=source,run_id=plan['run_id'],
            snapshot_id=plan.get('snapshot_id',plan['run_id']),page_url=page['page_url'],
            page_number=page.get('page_number'),observed_at=page['observed_at'],
            evidence_path=str(html_path),origin='LIVE_CAPTURE',http_status=proof['status_code'])
        actual=checked['page']
        if (actual.get('access_status')!='NORMAL' or actual.get('source_type_verified') is not True
                or actual.get('source_context_valid') is not True or actual.get('status')!='FAILED'
                or actual.get('page_content_state')!='PARSER_EMPTY_UNVERIFIED' or actual.get('actual_count')!=0
                or actual.get('raw_card_count')!=0 or set(actual.get('flags',[]))!={'PAGE_CONTENT_MISSING'}
                or actual.get('page_complete') is not False or checked.get('observations') or checked.get('acp_pending')):
            raise ValueError('T1_DIAGNOSTIC_OFFLINE_REPARSE_NOT_EXACT_FAILURE')
        seen.add(page['page_key'])
        result.append({'page_key':page['page_key'],'source_id':source['source_id'],'source_type':source['source_type'],
            'page_url':page['page_url'],'approval_reference':dict(reference),'approval_id':review['approval_id'],
            'original_page_journal_reference':review['original_page_journal_reference'],
            'capture_receipt_reference':review['capture_receipt_reference'],'html_reference':review['html_reference'],
            'preserved_status':'FAILED','preserved_completeness':'UNKNOWN','quality_gate_relaxed':False})
    return result


class HighSpeedRunner:
    def __init__(self, store, *, clock=None, sleep=None, parser=None, gate_reader=None, progress=None, qa_auditor=None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep or time.sleep
        self.parser = parser
        self.gate_reader = gate_reader
        self.progress = progress
        self.qa_auditor = qa_auditor

    def _gate(self, plan):
        if self.gate_reader:
            shared = self.gate_reader()
        elif plan.get("shared_access_gate_path"):
            path = Path(plan["shared_access_gate_path"])
            if not path.exists():
                raise ValueError("SHARED_GLOBAL_ACCESS_GATE_MISSING")
            shared = read_sealed(path)
        elif plan["stage"] == "T0":
            shared = {"state": "NORMAL"}
        else:
            raise ValueError("SHARED_GLOBAL_ACCESS_GATE_REQUIRED")
        local = self.store.get_gate() if hasattr(self.store, "get_gate") else {}
        return local if local.get("state", "NORMAL") != "NORMAL" else shared

    def run(self, plan, *, authorization=None, transport=None, resume=False, preheld_lock=False):
        if plan.get('snapshot_contract_version') == 4:
            raise ValueError('V4_REQUIRES_EXPLICIT_V4_T1_RUNNER')
        entry_time = utc_text(self.clock())
        validate_plan(plan)
        if plan.get("snapshot_contract_version") == 3 and self.parser is None:
            reference=plan.get("category_mapping_config_reference")
            if not reference or hashlib.sha256(Path(reference["path"]).read_bytes()).hexdigest()!=reference.get("sha256"):
                raise ValueError("V3_MAPPING_CONFIG_REFERENCE_REQUIRED")
        origin = plan.get("origin", "OFFLINE_FIXTURE" if plan["stage"] == "T0" else "LIVE_CAPTURE")
        if (plan["stage"] == "T0" and origin not in {"OFFLINE_FIXTURE", "HISTORICAL_REPLAY"}) or (plan["stage"] != "T0" and origin != "LIVE_CAPTURE"):
            raise ValueError("STAGE_ORIGIN_MISMATCH")
        validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
        if origin == "LIVE_CAPTURE" and transport is None:
            raise ValueError("VALIDATED_TRANSPORT_REQUIRED")
        run_id = plan["run_id"]
        # Only the CLI holding this store's lock for the whole browser lifetime
        # uses preheld_lock; ordinary API calls always acquire their own lock.
        lock = nullcontext() if preheld_lock else self.store.run_lock(run_id) if hasattr(self.store, "run_lock") else file_lock(Path(self.store.root) / "highspeed_runner.lock")
        with ExitStack() as locks:
            locks.enter_context(lock)
            if origin == "LIVE_CAPTURE" and not preheld_lock and plan.get("shared_access_gate_path"):
                locks.enter_context(file_lock(Path(plan["shared_access_gate_path"]).parent / "highspeed_live.lock"))
            validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
            if (authorization or {}).get('t1_reverification_review') and not resume:
                raise ValueError('T1_REVERIFICATION_REQUIRES_SAME_RUN_RESUME')
            run = self.store.get_run(run_id) if resume else self.store.begin_run(plan, started_at=entry_time)
            if not run:
                raise ValueError("RUN_NOT_FOUND")
            # Resume must use precisely the frozen plan, never larger limits.
            if run.get("plan") and run["plan"] != plan:
                raise ValueError("IMMUTABLE_RUN_PLAN_MISMATCH")
            reverification=reviewed_t1_reverification(self.store,plan,authorization,self._gate(plan),now=self.clock())
            self._t1_reverification_context=reverification
            previous_run_status = run.get("status")
            self.store.update_run(run_id, origin=origin, status="RUNNING")
            started = run.get("started_at") or run.get("created_at")
            if not started:
                started = utc_text(self.clock())
                self.store.update_run(run_id, started_at=started)
            bundle = self.store.load_run(run_id)
            seed_cache = {}
            for page in bundle.get("pages", []):
                if page.get("seed_provenance_path"):
                    from .continuation import validate_seed_provenance
                    seed = validate_seed_provenance(page,plan,store=self.store)
                    seed["imported_page"] = page
                    seed_cache[page["page_url"]] = seed
            committed = {p["page_key"] for p in bundle.get("pages", [])
                if p.get("status") == "COMPLETE"
                or (p.get("status") == "PARTIAL" and p.get("terminal_for_snapshot") is True)
                or (p.get("status") == "FAILED" and p.get("source_navigation_only") is True
                    and source_navigation_only(p.get("source", {}), p.get("page_url", ""), {"page":p}))}
            if resume:
                for decision in reviewed_t1_diagnostic_failures(self.store,plan,authorization,self._gate(plan),now=self.clock()):
                    committed.add(decision['page_key'])
                    self.store.append_event(run_id,{'kind':'T1_DIAGNOSTIC_FAILED_PAGE_RETAINED',**decision,
                        'scope_note':'Explicit user-reviewed T1 scheduling exclusion only; original failed evidence and strict stage QA retained.'})
            cached = {}
            for url,seed in seed_cache.items():
                original = seed["latest_page"]
                receipt = next(r for r in seed["receipts"] if r["kind"] == "PAGE"
                    and r["html_path"] == original["evidence_path"] and r["observed_at"] == original["observed_at"])
                cached[url] = receipt
            for receipt in bundle.get("responses", []):
                if receipt.get("access_state") == "NORMAL" and receipt.get("kind", receipt.get("request_kind", "PAGE")) == "PAGE":
                    cached[receipt.get("request_url", receipt.get("url"))] = receipt
            # Re-audit a previously recorded zero-card native miss offline. A
            # cached failure must not repeatedly refresh the same source forever.
            for page in ([] if reverification else bundle.get("pages", [])):
                if (page.get("seed_provenance_path") or page.get("status") not in {"FAILED","PARTIAL"}
                        or not ("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED" in page.get("flags",[])
                            or set(page.get("flags",[]))=={"PAGE_CONTENT_MISSING"}
                            or (page.get("source_type")=="MOST_GIFTED" and set(page.get("flags",[]))=={"WRONG_PAGE_TYPE"}))
                        or page.get("actual_count")!=0 or page.get("raw_card_count")!=0
                        or page.get("access_status")!="NORMAL" or (page.get("source_type_verified") is not True
                            and not (page.get("source_type")=="MOST_GIFTED" and set(page.get("flags",[]))=={"WRONG_PAGE_TYPE"}))
                        or page.get("source_context_valid") is not True):
                    continue
                source=next((s for s in plan["sources"] if s["source_id"]==page.get("source_id")),None)
                receipt=cached.get(page.get("page_url"))
                if not source or not receipt or receipt.get("html_path")!=page.get("evidence_path"):
                    continue
                try:
                    from .adapters import parse_page
                    html=Path(receipt["html_path"]).read_text(encoding="utf-8")
                    hashed=hashlib.sha256(html.encode("utf-8")).hexdigest()
                    if hashed!=page.get("evidence_sha256") or (receipt.get("sha256") and hashed!=receipt["sha256"]):
                        continue
                    checked=parse_page(html,source=source,run_id=run_id,snapshot_id=plan.get("snapshot_id",run_id),
                        page_url=page["page_url"],page_number=page.get("page_number"),observed_at=page["observed_at"],
                        evidence_path=page["evidence_path"],http_status=receipt.get("status_code"),origin=page["origin"])
                except (OSError,ValueError,KeyError):
                    continue
                official_empty=(receipt.get("status_code")==200 and receipt.get("access_state")=="NORMAL"
                    and explicit_source_empty(checked))
                native_empty=("ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED" in page.get("flags",[])
                    and empty_continuation_unavailable(checked,receipt,"ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED",self._gate(plan)))
                if self._gate(plan).get("state")=="NORMAL" and (official_empty or native_empty):
                    if not (page.get("terminal_for_snapshot") is True and page.get("status")=="FAILED"):
                        page={**page,**(checked["page"] if official_empty else {}),"status":"FAILED","completeness":"UNKNOWN","page_complete":False,
                            "continuation_unavailable":native_empty,"source_empty":official_empty,"terminal_for_snapshot":True}
                        self.store.commit_page(run_id,page["page_key"],{k:v for k,v in page.items() if k!="journal_path"},[])
                        self.store.append_event(run_id,{"kind":"SOURCE_EMPTY" if official_empty else "SOURCE_CONTINUATION_UNAVAILABLE","stage":"source_incompleteness",
                            "page_url":page["page_url"],"source_id":source["source_id"],"source_type":source["source_type"],
                            "actual_cards":0,"reason":"OFFICIAL_EMPTY_STATE" if official_empty else "ACP_NATIVE_REQUEST_CONTRACT_NOT_OBSERVED","cached_failure_reaudited":True,
                            "note":"Zero capacity retained FAILED; frozen approved queue continues without re-requesting this source."})
                    committed.add(page["page_key"])
            if reverification:
                for sid,url in reverification['pairs']:
                    key=hashlib.sha256((sid+'\0'+url).encode()).hexdigest()
                    fresh=reverification['fresh_receipts'].get((sid,url))
                    fresh_page=next((p for p in bundle.get('pages',[]) if p.get('page_key')==key
                        and fresh and p.get('observed_at')==fresh.get('observed_at')
                        and p.get('evidence_path')==fresh.get('html_path')),None)
                    if fresh_page is None:
                        committed.discard(key)
                    if (sid,url) not in reverification['attempted']:
                        cached.pop(url,None)
                    elif fresh:
                        cached[url]=fresh
                    else:
                        cached.pop(url,None)
                    if fresh_page and (sid,url) in reverification['retained_rules']:
                        source=next(s for s in plan['sources'] if s['source_id']==sid)
                        from .adapters import parse_page
                        check=parse_page(Path(fresh['html_path']).read_text(encoding='utf-8'),source=source,
                            run_id=run_id,page_url=url,page_number=1,observed_at=fresh['observed_at'],
                            evidence_path=fresh['html_path'],origin=origin,http_status=fresh['status_code'])
                        fresh_text=Path(fresh['html_path']).read_text(encoding='utf-8')
                        if (_same_reviewed_move_failure(check) and _same_move_empty_dom(fresh_text)
                                and _move_shell_text(fresh_text)==reverification['retained_rules'][(sid,url)]['reviewed_shell_text']):committed.add(key)
                self.store.append_event(run_id,{'kind':'T1_FRESH_REVERIFICATION_SCOPE',
                    'approval_reference':reverification['reference'],'reviewed_at':reverification['review']['reviewed_at'],
                    'expected_PAGE_scopes':len(reverification['pairs']),
                    'already_attempted_PAGE_scopes':len(reverification['attempted']),
                    'remaining_fresh_PAGE_scopes':len(reverification['pairs']-reverification['attempted']),
                    'original_started_at_preserved':started,'cumulative_budget_preserved':True})
            last_request = max((aware_time(r["observed_at"]) for r in bundle.get("requests", []) if r.get("observed_at")), default=None)
            if hasattr(self.store, "latest_request_time"):
                global_last = self.store.latest_request_time()
                if global_last:
                    last_request = max(filter(None, (last_request, aware_time(global_last))))
            reason = None
            rolling = RollingProgress(plan["target_unique_links"])
            progress_history = [e for e in bundle.get("events", []) if e.get("kind") == "PROGRESS"]
            normalizer_version = self.store.normalizer_version(run_id) if hasattr(self.store,"normalizer_version") else plan.get("normalizer_version",NORMALIZER_VERSION)
            comparable = [e for e in progress_history if e.get("normalizer_version") == normalizer_version]
            current_elapsed = (aware_time(self.clock()) - aware_time(started)).total_seconds()
            current_count = self.store.candidate_count(run_id)
            rebase_reason = "NO_CURRENT_NORMALIZER_PROGRESS" if not comparable else None
            if comparable:
                try:
                    for event in comparable:
                        rolling.record(event["elapsed_seconds"], event["unique_links"])
                    if rolling.samples[-1][0] > current_elapsed or rolling.samples[-1][1] > current_count:
                        rebase_reason = "CURRENT_PROGRESS_NO_LONGER_COMPARABLE"
                except ValueError:
                    rebase_reason = "CURRENT_PROGRESS_NO_LONGER_COMPARABLE"
            if rebase_reason:
                # The run wall clock retains its original start. Only the rate
                # baseline changes; historical counts and events remain intact.
                rolling.start_epoch(current_elapsed, current_count)
                if resume or progress_history:
                    self.store.append_event(run_id, {"kind":"NORMALIZATION_PROGRESS_REBASE",
                        "normalizer_version":normalizer_version, "reason":rebase_reason,
                        "elapsed_seconds":current_elapsed, "unique_links":current_count,
                        "preserved_history_count":len(progress_history),
                        "previous_progress":progress_history[-1] if progress_history else None})
            if resume:
                # Offline checkpoint verification above is startup work. A new
                # recent-rate sample must not span a prior stop or user pause.
                # The run clock, cumulative pool and original events are intact.
                rolling.start_epoch(current_elapsed, current_count)
                self.store.append_event(run_id, {"kind": "PROGRESS_SAMPLING_EPOCH",
                    "reason": "RESUME_AFTER_CHECKPOINT_VALIDATION",
                    "normalizer_version": normalizer_version,
                    "elapsed_seconds": current_elapsed, "unique_links": current_count,
                    "preserved_history_count": len(progress_history),
                    "previous_run_status": previous_run_status,
                    "sampling_scope": "NEW_YIELD_AFTER_RESUME_EXCLUDING_PRIOR_PAUSE_AND_STARTUP_VALIDATION"})
            from .schedule import source_order
            ordered_sources = source_order(plan["sources"], policy=plan.get("source_order_policy", "PLAN_ORDER"))
            for source in ordered_sources:
                for page_number, url in enumerate(source.get("page_urls", []), 1):
                    page_key = hashlib.sha256((source["source_id"] + "\0" + url).encode()).hexdigest()
                    if page_key in committed:
                        continue
                    if (not plan.get('probe_all_reviewed_page_urls', False)
                            and self.store.candidate_count(run_id) >= plan["target_unique_links"]):
                        reason = "TARGET_UNIQUE_LINKS_REACHED"
                        break
                    try:
                        if (reverification and (source['source_id'],url) in reverification['attempted']
                                and (source['source_id'],url) not in reverification['fresh_receipts']):
                            raise ValueError('AMBIGUOUS_T1_REVERIFICATION_PAGE_ATTEMPT_NO_REPEAT')
                        if url in cached:
                            receipt = cached[url]
                            response = {**receipt, "text": Path(receipt["html_path"]).read_text(encoding="utf-8")}
                        else:
                            response, receipt, last_request = self._request(plan, authorization, transport,
                                source, url, "PAGE", started, last_request, page_key=page_key)
                        html = response.get("text", "")
                        acp = [{**item, "body": Path(item["html_path"]).read_text(encoding="utf-8"), "evidence_path": item["html_path"],
                                "capture_receipt_reference":self._receipt_ref(item)}
                            for item in bundle.get("responses", []) if item.get("kind") == "ACP" and item.get("page_key") == page_key and item.get("access_state") == "NORMAL"
                            and (not reverification or aware_time(item['observed_at'])>=aware_time(reverification['review']['reviewed_at']))]
                        historical = url in seed_cache and receipt.get("run_id") == plan.get("parent_run_id")
                        if historical:
                            acp = [{**r,"body":Path(r["html_path"]).read_text(encoding="utf-8"),"evidence_path":r["html_path"]}
                                for r in seed_cache[url]["receipts"] if r["kind"] == "ACP"]
                        parsed = self._parse(plan, source, url, page_number, receipt, html, acp,
                            "HISTORICAL_REPLAY" if historical else origin)
                        attempted = set()
                        excluded_carousels = set()
                        carousel_failures = {}
                        v3 = plan.get("snapshot_contract_version") == 3
                        pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                        needs_context = getattr(transport, "requires_ajax_page_context", None)
                        native_context_unrestored=bool(reverification and pending and url in cached
                            and callable(needs_context) and needs_context(url))
                        if native_context_unrestored:
                            parsed['page'].update(status='PARTIAL',completeness='UNKNOWN',page_complete=False)
                            parsed['page'].setdefault('flags',[]).append('T1_REVERIFICATION_NATIVE_CONTEXT_NOT_RELOADED')
                            self.store.append_event(run_id,{'kind':'T1_REVERIFICATION_NATIVE_CONTEXT_NOT_RELOADED',
                                'source_id':source['source_id'],'page_url':url,
                                'note':'Fresh PAGE already attempted; no duplicate navigation to restore ACP context.'})
                        if (not native_context_unrestored and pending and origin == "LIVE_CAPTURE" and url in cached
                                and (historical or (callable(needs_context) and needs_context(url)))):
                            response, receipt, last_request = self._request(plan, authorization, transport,
                                source, url, "PAGE", started, last_request, page_key=page_key)
                            historical = False
                            html = response.get("text", "")
                            parsed = self._parse(plan, source, url, page_number, receipt, html, [], origin)
                            pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                        while pending and origin == "LIVE_CAPTURE" and not native_context_unrestored:
                            if reverification:
                                if not aware_time(reverification['review']['reviewed_at'])<=aware_time(self.clock())<aware_time(reverification['review']['expires_at']):
                                    raise ValueError('T1_REVERIFICATION_REVIEW_EXPIRED')
                                counters=self.store.summary(run_id)
                                from .budgets import madrid_day
                                remaining=min(plan['max_requests_per_run']-counters.get('requests',0),
                                    plan['max_requests_per_day']-counters.get('requests_by_day',{}).get(madrid_day(self.clock()),0))
                                pending_pages=len(reverification['pairs']-reverification['attempted'])
                                if remaining<=pending_pages:
                                    parsed['page'].update(status='PARTIAL',completeness='UNKNOWN',page_complete=False)
                                    parsed['page'].setdefault('flags',[]).append('T1_ACP_BUDGET_RESERVED_FOR_PAGE_PROBES')
                                    self.store.append_event(run_id,{'kind':'T1_ACP_BUDGET_RESERVED_FOR_PAGE_PROBES',
                                        'source_id':source['source_id'],'page_url':url,'remaining_requests':remaining,
                                        'remaining_fresh_PAGE_scopes':pending_pages,'approval_reference':reverification['reference']})
                                    break
                            descriptor = _next_acp_pending(pending, excluded_carousels, v3=v3)
                            if descriptor is None:
                                break
                            descriptor = dict(descriptor)
                            if v3:
                                descriptor["source_id"] = source["source_id"]
                            scope = authorization["sources"][source["source_id"]]
                            if not (scope.get("allow_acp") or scope.get("allow_native_acp")):
                                break
                            prepare = getattr(transport, "prepare_ajax_descriptor", None)
                            if callable(prepare):
                                prepare_start = time.perf_counter()
                                prepare_success = False
                                try:
                                    validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
                                    descriptor = prepare(descriptor, referer=url)
                                    if isinstance(descriptor.get("payload"), str):
                                        descriptor["request_payload_sha256"] = hashlib.sha256(descriptor["payload"].encode("utf-8")).hexdigest()
                                    prepare_success = True
                                except Exception as exc:
                                    # The initial normal page is real evidence even
                                    # when its lazy request contract is unavailable.
                                    prepare_error = str(exc) if isinstance(exc, ValueError) else "ACP_PREPARE_FAILED:"+type(exc).__name__
                                    empty_unavailable = empty_continuation_unavailable(parsed,receipt,prepare_error,self._gate(plan))
                                    parsed["page"].update(status="FAILED" if empty_unavailable else "PARTIAL", completeness="PARTIAL", page_complete=False)
                                    parsed["page"].setdefault("flags", []).append(prepare_error)
                                    unavailable = empty_unavailable or continuation_unavailable(parsed, receipt, prepare_error, self._gate(plan))
                                    if (v3 and descriptor.get("metadata", {}).get("carousel")
                                            and prepare_error in {"ACP_CAROUSEL_OWNER_EVIDENCE_UNVERIFIED", "ACP_CAROUSEL_NATIVE_CONTRACT_AMBIGUOUS"}
                                            and receipt.get("status_code") == 200 and receipt.get("access_state") == "NORMAL"
                                            and self._gate(plan).get("state") == "NORMAL"
                                            and parsed["page"].get("source_type_verified") is True
                                            and parsed["page"].get("source_context_valid") is True and parsed.get("observations")):
                                        unavailable = True
                                    if unavailable:
                                        parsed["page"].update(completeness="UNKNOWN", continuation_unavailable=True,
                                            terminal_for_snapshot=True)
                                    self.store.commit_page(run_id, page_key,
                                        {**parsed["page"], "page_key": page_key, "source": source,
                                         "evidence_path": receipt["html_path"], "origin": origin}, parsed["observations"])
                                    if unavailable:
                                        self.store.append_event(run_id, {"kind":"SOURCE_CONTINUATION_UNAVAILABLE",
                                            "stage":"source_incompleteness", "source_id":source["source_id"],
                                            "source_type":source["source_type"], "page_url":url,
                                            "actual_cards":parsed["page"]["actual_count"], "reason":prepare_error,
                                            "note":"Source retained FAILED at zero capacity or PARTIAL with saved cards; no endpoint/body invented. Continue the frozen approved queue."})
                                        if v3 and descriptor.get("metadata", {}).get("carousel"):
                                            excluded_carousels.add(descriptor["metadata"]["ranking_context_url"])
                                            carousel_failures[descriptor["metadata"]["ranking_context_url"]] = prepare_error
                                            continue
                                        pending = None
                                        break
                                    raise ValueError(prepare_error) from exc
                                finally:
                                    self.store.append_event(run_id, {"kind": "ACP_PREPARE", "stage": "acp_prepare",
                                        "duration_seconds": time.perf_counter()-prepare_start, "success": prepare_success,
                                        "source_type": source["source_type"], "research_category": source.get("research_category")})
                                    if v3 and hasattr(transport,"last_prepare_browser_wait_seconds"):
                                        self.store.append_event(run_id,{"kind":"BROWSER_WAIT","stage":"browser_wait",
                                            "duration_seconds":transport.last_prepare_browser_wait_seconds,
                                            "measurement_scope":"Explicit native contract wait; subset of ACP_PREPARE, not additive total."})
                            key = json.dumps(descriptor, sort_keys=True, default=str)
                            if key in attempted:
                                parsed["page"].setdefault("flags", []).append("ACP_NO_PROGRESS")
                                if v3 and descriptor.get("metadata", {}).get("carousel"):
                                    excluded_carousels.add(descriptor["metadata"]["ranking_context_url"])
                                    carousel_failures[descriptor["metadata"]["ranking_context_url"]] = "ACP_NO_PROGRESS"
                                    continue
                                break
                            attempted.add(key)
                            try:
                                item, saved, last_request = self._request(plan, authorization, transport,
                                    source, descriptor["url"], "ACP", started, last_request,
                                    descriptor=descriptor, page_key=page_key)
                            except (ValueError, OSError) as exc:
                                # Preserve all successfully observed cards even when a
                                # continuation fails; never call this a complete page.
                                parsed["page"]["completeness"] = "PARTIAL"
                                parsed["page"]["status"] = "PARTIAL"
                                parsed["page"]["page_complete"] = False
                                parsed["page"].setdefault("flags", []).append(str(exc))
                                self.store.commit_page(run_id, page_key,
                                    {**parsed["page"], "page_key": page_key, "source": source,
                                     "evidence_path": receipt["html_path"], "origin": origin}, parsed["observations"])
                                raise
                            acp.append({**item, "body": item.get("text", ""), "evidence_path": saved["html_path"]})
                            if v3:
                                acp[-1]["capture_receipt_reference"] = self._receipt_ref(saved)
                            previous = parsed["page"].get("actual_count", len(parsed["observations"]))
                            parsed = self._parse(plan, source, url, page_number, receipt, html, acp, origin)
                            if carousel_failures:
                                parsed["page"].setdefault("flags", []).extend(carousel_failures.values())
                            pending = parsed.get("acp_pending") or parsed["page"].get("acp_pending")
                            if parsed["page"].get("actual_count", len(parsed["observations"])) <= previous and pending:
                                parsed["page"].setdefault("flags", []).append("ACP_NO_PROGRESS")
                                if v3 and descriptor.get("metadata", {}).get("carousel"):
                                    excluded_carousels.add(descriptor["metadata"]["ranking_context_url"])
                                    carousel_failures[descriptor["metadata"]["ranking_context_url"]] = "ACP_NO_PROGRESS"
                                    continue
                                break
                        if carousel_failures:
                            parsed["page"].update(carousel_contract_failures=carousel_failures,
                                page_complete=False, completeness="UNKNOWN",
                                terminal_for_snapshot=_next_acp_pending(pending, excluded_carousels, v3=True) is None)
                        persist_start = aware_time(self.clock())
                        navigation_only = source_navigation_only(source, url, parsed)
                        official_empty = (receipt.get("status_code")==200 and receipt.get("access_state")=="NORMAL"
                            and self._gate(plan).get("state")=="NORMAL" and explicit_source_empty(parsed))
                        if historical:
                            imported = seed_cache[url]["imported_page"]
                            parsed["page"] = {k:v for k,v in imported.items() if k != "journal_path"}
                            parsed["observations"] = read_sealed(imported["journal_path"])["observations"]
                        if navigation_only:
                            parsed["page"]["source_navigation_only"] = True
                        if official_empty:
                            parsed["page"].update(source_empty=True,terminal_for_snapshot=True,completeness="UNKNOWN",page_complete=False)
                            self.store.append_event(run_id,{"kind":"SOURCE_EMPTY","stage":"source_incompleteness",
                                "source_id":source["source_id"],"source_type":source["source_type"],"page_url":url,
                                "actual_cards":0,"reason":"OFFICIAL_EMPTY_STATE","evidence_path":receipt["html_path"]})
                        self.store.commit_page(run_id, page_key,
                            {**parsed["page"], "page_key": page_key, "source": source,
                             "evidence_path": receipt["html_path"], "origin": "HISTORICAL_REPLAY" if historical else origin}, parsed["observations"])
                        self.store.append_event(run_id, {"kind": "PERSIST", "stage": "persistence",
                            "duration_seconds": (aware_time(self.clock())-persist_start).total_seconds(),
                            "source_type": source["source_type"], "research_category": source.get("research_category")})
                        if parsed["page"].get("status") in {"FAILED", "BLOCKED"}:
                            retained_move=bool(reverification and (source['source_id'],url) in reverification['retained_rules']
                                and receipt.get('status_code')==200 and receipt.get('access_state')=='NORMAL'
                                and self._gate(plan).get('state')=='NORMAL' and _same_reviewed_move_failure(parsed)
                                and hashlib.sha256(Path(receipt['html_path']).read_bytes()).hexdigest()==receipt.get('sha256')
                                and _same_move_empty_dom(Path(receipt['html_path']).read_text(encoding='utf-8'))
                                and _move_shell_text(Path(receipt['html_path']).read_text(encoding='utf-8'))==reverification['retained_rules'][(source['source_id'],url)]['reviewed_shell_text'])
                            if retained_move:
                                self.store.append_event(run_id,{'kind':'T1_DIAGNOSTIC_FAILED_PAGE_RETAINED',
                                    'source_id':source['source_id'],'page_url':url,'page_key':page_key,
                                    'approval_reference':reverification['reference'],'capture_receipt_reference':self._receipt_ref(receipt),
                                    'fresh_html_reference':{'path':receipt['html_path'],'sha256':receipt.get('sha256')},
                                    'note':'This fresh exact MOVE failure is explicitly reviewed; FAILED/UNKNOWN/0 cards and QA unchanged.'})
                            elif parsed["page"].get("status")=="FAILED" and parsed["page"].get("terminal_for_snapshot") is True and (parsed["page"].get("continuation_unavailable") is True or official_empty):
                                pass
                            elif navigation_only:
                                self.store.append_event(run_id, {"kind": "SOURCE_NAVIGATION_ONLY", "stage": "source_navigation",
                                    "source_id": source["source_id"], "source_type": source["source_type"],
                                    "page_url": url, "evidence_path": receipt["html_path"],
                                    "note": "Verified navigation page has no cards; quality remains FAILED. Continue only the frozen approved URL queue."})
                            else:
                                raise ValueError("PAGE_PARSE_OR_ACCESS_FAILED")
                        committed.add(page_key)
                        elapsed = (aware_time(self.clock()) - aware_time(started)).total_seconds()
                        dedup_start = time.perf_counter()
                        count = self.store.candidate_count(run_id)
                        self.store.append_event(run_id, {"kind":"DEDUP", "stage":"dedup",
                            "duration_seconds":time.perf_counter()-dedup_start,
                            "source_type":source["source_type"], "research_category":source.get("research_category")})
                        progress = {"kind": "PROGRESS", "normalizer_version":normalizer_version,
                            **rolling.record(elapsed, count)}
                        self.store.append_event(run_id, progress)
                        if self.progress:
                            self.progress(progress)
                    except (ValueError, OSError) as exc:
                        reason = str(exc)
                        self.store.append_event(run_id, {"kind": "STOP", "reason": reason, "observed_at": utc_text(self.clock())})
                        break
                if reason:
                    break
            qa_start = aware_time(self.clock())
            bundle = self.store.load_run(run_id)
            # Re-audit evidence, observations and link pool; finishing a plan is
            # separate from proving completeness or passing a stage gate.
            if self.qa_auditor is None:
                from .quality import audit_run
                auditor = audit_run
            else:
                auditor = self.qa_auditor
            quality = auditor(self.store, run_id)
            errors = quality.get("errors", [finding for finding in quality.get("findings", []) if finding.get("severity") == "ERROR"])
            elapsed = (aware_time(self.clock()) - aware_time(started)).total_seconds()
            reason = reason or ("TARGET_UNIQUE_LINKS_REACHED" if not plan.get('probe_all_reviewed_page_urls', False)
                and self.store.candidate_count(run_id) >= plan["target_unique_links"] else "SOURCE_PLAN_EXHAUSTED")
            if plan.get('probe_all_reviewed_page_urls', False):
                probe_scope = _probe_scope_summary(plan,bundle,committed,reason)
                self.store.append_event(run_id, {'kind':'T1_PROBE_SCOPE', **probe_scope})
                self.store.update_run(run_id, probe_scope=probe_scope)
            self.store.append_event(run_id, {"kind": "QA", "stage": "qa", "duration_seconds": (aware_time(self.clock())-qa_start).total_seconds(), "errors": errors})
            self.store.update_run(run_id, status="STOPPED" if errors or reason not in {"TARGET_UNIQUE_LINKS_REACHED", "SOURCE_PLAN_EXHAUSTED"} else "FINISHED",
                stop_reason=reason, elapsed_seconds=elapsed, within_duration_target=elapsed <= plan["max_duration_hours"]*3600,
                completed_at=utc_text(self.clock()), finished_at=utc_text(self.clock()), origin=origin,
                qa=quality, quality_gate_passed=quality.get("gate_passed") is True)
            return self.store.load_run(run_id)

    def _parse(self, plan, source, url, number, receipt, html, acp, origin):
        if self.parser is None:
            from .adapters import parse_page
            parser = parse_page
        else:
            parser = self.parser
        before = aware_time(self.clock())
        parent_source=source
        category_mapping = None
        if plan.get("snapshot_contract_version") == 3 and self.parser is None:
            from .category_mapping import load_mapping_rules, map_category, map_observation_category
            reference = plan.get("category_mapping_config_reference")
            if not reference or hashlib.sha256(Path(reference["path"]).read_bytes()).hexdigest() != reference.get("sha256"):
                raise ValueError("V3_MAPPING_CONFIG_REFERENCE_REQUIRED")
            key = (reference["path"],reference["sha256"])
            cache = self.__dict__.setdefault("_v3_mapping_rules", {})
            if key not in cache:
                cache[key] = load_mapping_rules(reference["path"])
            category_mapping = map_category(source,cache[key])
            source = {**source,**category_mapping}
        parsed = parser(html, source=source, run_id=plan["run_id"], snapshot_id=plan["run_id"],
            page_url=url, page_number=number, observed_at=receipt.get("observed_at") or utc_text(before),
            evidence_path=receipt["html_path"], http_status=receipt.get("status_code"), origin=origin, acp_responses=acp)
        for observation in parsed["observations"]:
            if observation.get("origin", origin) != origin:
                raise ValueError("OBSERVATION_ORIGIN_MISMATCH")
            observation.setdefault("origin", origin)
            if category_mapping is not None:
                representation=observation.get("representation", "SERVER")
                captured=receipt if representation=="SERVER" else acp[int(representation.split("_")[1])]
                observation.update(capture_receipt_reference=captured.get("capture_receipt_reference") or self._receipt_ref(captured),
                    capture_run_id=captured.get("run_id",plan["run_id"]),source_completeness_status="UNKNOWN")
                mapping_cache=self.__dict__.setdefault("_v3_card_category_cache",{})
                try:
                    observation.update(map_observation_category(observation,parent_source,cache[key],
                        parent_html_path=receipt["html_path"],parent_html_sha256=receipt.get("sha256") or hashlib.sha256(html.encode()).hexdigest(),
                        cache=mapping_cache))
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    observation.update(research_category=None,category_mapping_status="UNKNOWN",
                        category_mapping_reason=str(exc),category_mapping_evidence=[],category_mapping_candidates=[])
        self.store.append_event(plan["run_id"], {"kind": "PARSE", "stage": "parse", "duration_seconds": (aware_time(self.clock())-before).total_seconds(),
            "raw_card_count": len(parsed["observations"]), "page_url": url,
            "source_type": source["source_type"], "research_category": source.get("research_category")})
        return parsed

    @staticmethod
    def _receipt_ref(receipt):
        path=Path(receipt["evidence_path"])
        return {"path":str(path.resolve()),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}

    def _request(self, plan, authorization, transport, source, url, kind, started, last_request, *, descriptor=None, page_key=None):
        live = plan["stage"] != "T0"
        if live:
            validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
            authorize_request(url, source, authorization, kind=kind, descriptor=descriptor)
            if last_request is not None:
                delay = plan["request_pacing_seconds"] - (aware_time(self.clock())-last_request).total_seconds()
                pacing_started=time.perf_counter()
                if delay > 0:
                    self.sleep(delay)
                if plan.get("snapshot_contract_version") == 3:
                    self.store.append_event(plan["run_id"],{"kind":"PACING","stage":"pacing",
                        "duration_seconds":time.perf_counter()-pacing_started,"requested_sleep_seconds":max(0,delay)})
            # Approval, gate and elapsed budget may change while pacing.
            validate_authorization(plan, authorization, self._gate(plan), now=self.clock(), store_root=self.store.root)
        if plan.get('target_policy'):
            from .task_clock import request_stop_reason
            reason=request_stop_reason(plan,authorization,self.store,now=self.clock())
            if reason:raise ValueError(reason)
        if plan.get('collection_contract')=='V4_THREE_SOURCE_5001':
            from .three_contract import family_budget_for_store
            reason=family_budget_for_store(self.store,plan,source,kind)
            if reason:raise ValueError(reason)
            counters=self.store.budget_counters(plan['run_id'])
        else:
            counters = self.store.summary(plan["run_id"])
        reverification=getattr(self,'_t1_reverification_context',None)
        if reverification and not aware_time(reverification['review']['reviewed_at'])<=aware_time(self.clock())<aware_time(reverification['review']['expires_at']):
            raise ValueError('T1_REVERIFICATION_REVIEW_EXPIRED')
        reason = budget_reason(plan, counters, now=self.clock(), started_at=started, kind=kind, authorization=authorization)
        if reason:
            raise ValueError(reason)
        observed = aware_time(self.clock())
        origin = plan.get("origin", "LIVE_CAPTURE" if live else "OFFLINE_FIXTURE")
        request = {"kind": kind, "url": url, "source_id": source["source_id"], "source_type": source["source_type"],
            "observed_at": utc_text(observed), "limits": plan, "page_key": page_key,
            "origin": origin}
        if reverification and kind=='PAGE':
            pair=(source['source_id'],url)
            if pair in reverification['attempted']:
                raise ValueError('T1_REVERIFICATION_FRESH_PAGE_ALREADY_ATTEMPTED')
        reservation = self.store.reserve_request(plan["run_id"], request)
        if reverification and kind=='PAGE':
            reverification['attempted'].add(pair)
        before = aware_time(self.clock())
        transaction_started=time.perf_counter()
        try:
            if not live:
                entries = source.get("offline_pages", {})
                path = entries.get(url)
                if not path:
                    raise ValueError("OFFLINE_SOURCE_HTML_REQUIRED")
                body = Path(path).read_bytes()
                if origin == "HISTORICAL_REPLAY":
                    metadata = source.get("offline_metadata", {}).get(url, {})
                    if metadata.get("page_url") != url or metadata.get("html_sha256") != hashlib.sha256(body).hexdigest():
                        raise ValueError("HISTORICAL_CAPTURE_METADATA_REQUIRED")
                    observed = aware_time(metadata["observed_at"])
                response = {"status_code": 200, "url": url, "text": body.decode("utf-8"), "access_state": "NORMAL"}
            else:
                obj = transport.fetch_page(url) if kind == "PAGE" else transport.fetch_ajax(url, method=descriptor["method"],
                    referer=descriptor.get("referer"), payload=descriptor.get("payload"), headers=descriptor.get("headers"))
                response = dict(obj) if isinstance(obj, dict) else dict(vars(obj))
            state = detect_access_status(response.get("status_code"), response.get("text", "")).value
        except Exception as exc:
            response = {"status_code": None, "url": None, "text": "", "error": type(exc).__name__ + ": " + str(exc)}
            diagnostics=getattr(exc,'transport_diagnostics',None)
            if isinstance(diagnostics,dict):response['transport_diagnostics']=dict(diagnostics)
            state = "NETWORK_ERROR"
            observed_status=(diagnostics or {}).get('HTTP_status_observed') if isinstance(diagnostics,dict) else None
            if observed_status in {403,429} and type(observed_status) is int:
                response['status_code']=observed_status
                state=detect_access_status(observed_status,'').value
        response.update(access_state=state, observed_at=utc_text(observed), request_url=url,
            duration_seconds=time.perf_counter()-transaction_started if plan.get("snapshot_contract_version") == 3 else (aware_time(self.clock())-before).total_seconds(), origin=request["origin"],
            kind=kind, page_key=page_key, source_id=source["source_id"], source_type=source["source_type"],
            research_category=source.get("research_category"), final_url=response.get("url"),
            request_method="GET" if kind == "PAGE" else descriptor["method"],
            request_payload=None if descriptor is None else descriptor.get("payload"))
        if descriptor is not None:
            response["native_acp_descriptor"] = dict(descriptor)
            if descriptor.get("ranking_context_binding"):
                response["ranking_context_binding"] = dict(descriptor["ranking_context_binding"])
        sensitive = {"authorization", "cookie", "proxy-authorization", "set-cookie"}
        for field in ("headers", "request_headers"):
            response[field] = {str(k): str(v) for k,v in (response.get(field) or {}).items() if str(k).casefold() not in sensitive}
        if response.get("native_acp_descriptor"):
            response["native_acp_descriptor"]["headers"] = {str(k):str(v) for k,v in (descriptor.get("headers") or {}).items() if str(k).casefold() not in sensitive}
        save_start = aware_time(self.clock())
        receipt = self.store.save_response(plan["run_id"], reservation, response)
        acknowledge=getattr(transport,'acknowledge_native_response',None)
        if live and plan.get('snapshot_contract_version') in {3,4} and kind=='ACP' and callable(acknowledge):
            acknowledge(receipt)
        self.store.append_event(plan["run_id"], {"kind": "HTMLSAVE", "stage": "html_save",
            "duration_seconds": (aware_time(self.clock())-save_start).total_seconds(),
            "source_type": source["source_type"], "research_category": source.get("research_category")})
        self.store.append_event(plan["run_id"], {"kind": "REQUEST", "stage": "navigation" if kind == "PAGE" else "acp", "request_kind": kind,
            "duration_seconds": response["duration_seconds"], "access_state": state, "url": url,
            "source_type": source["source_type"], "research_category": source.get("research_category")})
        if plan.get("snapshot_contract_version") == 3:
            self.store.append_event(plan["run_id"],{"kind":"NETWORK_WITH_BROWSER_WAIT","stage":"network_with_browser_wait",
                "duration_seconds":response["duration_seconds"],"transport_timings":response.get("timings",{}),
                "pure_network_seconds":None,"measurement_scope":"Full fetch operation; browser/network/body wait overlap is not decomposed."})
            measured=(response.get("timings") or {}).get("network_request_response_seconds")
            if isinstance(measured,(int,float)) and not isinstance(measured,bool) and 0<=measured<=response["duration_seconds"]:
                self.store.append_event(plan["run_id"],{"kind":"NETWORK_REQUEST_RESPONSE_PHASE","stage":"network_only",
                    "duration_seconds":measured,"measurement_source":response["timings"].get("network_measurement_source"),
                    "measurement_scope":"Browser requestStart→responseEnd including server wait; subset of fetch, not physical wire-only time."})
            if state != "NORMAL":
                self.store.append_event(plan["run_id"],{"kind":"REQUEST_FAILURE","stage":"request_failure",
                    "duration_seconds":response["duration_seconds"],"access_state":state,
                    "measurement_scope":"Failed fetch transaction; subset of request time, not additive total."})
        if state != "NORMAL":
            if hasattr(self.store, "set_gate"):
                self.store.set_gate(state, evidence_path=receipt["evidence_path"], run_id=plan["run_id"])
            else:
                self.store.update_run(plan["run_id"], access_state=state)
            if live:
                self._persist_shared_stop(plan, state, receipt)
            raise ValueError(state)
        if live:
            try:
                if plan.get('snapshot_contract_version')==4:
                    from .response_alias import authorize_v4_response
                    authorize_v4_response(receipt,source,authorization)
                else:
                    authorize_request(response.get("url", url), source, authorization, kind=kind, descriptor=descriptor)
            except ValueError:
                if hasattr(self.store, "set_gate"):
                    self.store.set_gate("UNKNOWN", evidence_path=receipt["evidence_path"], run_id=plan["run_id"], reason="UNREVIEWED_REDIRECT")
                self._persist_shared_stop(plan, "UNKNOWN", receipt)
                raise
        return response, receipt, aware_time(self.clock())

    def _persist_shared_stop(self, plan, state, receipt):
        path = plan.get("shared_access_gate_path")
        if not path:
            return  # Injected fake gates in offline tests have no filesystem.
        path = Path(path)
        with file_lock(path.with_suffix(path.suffix + ".v2.lock")):
            previous = read_sealed(path)
            immutable_json(path.parent / "highspeed_gate_history" / (uuid.uuid4().hex + ".json"), sealed(previous))
            atomic_json(path, sealed({**previous, "state": state, "run_id": plan["run_id"],
                "evidence_path": receipt["evidence_path"], "stopped_at": utc_text(self.clock()),
                "reason": "HIGH_SPEED_V2_ACCESS_STOP"}), backup=True)

