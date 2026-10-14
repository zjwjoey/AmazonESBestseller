"""Re-audit actual page bytes and URL pools, not success labels."""
from collections import Counter
from pathlib import Path

from ..multisource.contracts import SOURCE_ROOTS, RESEARCH_CATEGORIES
from ..multisource.storage import digest, read_sealed
from .adapters import parse_page
from .links import candidate_id, normalize_product_url


def audit_run(store, run_id):
    run = store.get_run(run_id)
    if run.get('plan', {}).get('snapshot_contract_version') == 3:
        return _audit_run_v3(store, run_id, run)
    pages = store.pages(run_id)
    findings, cached = [], {}
    live = run.get("origin") == "LIVE_CAPTURE"
    sources = {s["source_id"]: s for s in run.get("sources", [])}
    types, categories = set(), set()
    normalized, groups = set(), set()
    receipts = {}
    if live:
        for indexed in store.iter_responses(run_id):
            try:
                receipt = read_sealed(indexed["evidence_path"])
                if receipt.get("run_id") != run_id or receipt.get("origin") != "LIVE_CAPTURE":
                    raise ValueError("LIVE_RECEIPT_RUN_OR_ORIGIN_CONFLICT")
                receipts.setdefault(receipt["html_path"], []).append(receipt)
            except (OSError, ValueError, KeyError) as exc:
                findings.append({"code": "LIVE_RECEIPT_INVALID", "severity": "ERROR", "reason": str(exc)})

    def fail(code, *, severity="ERROR", **context):
        findings.append({"code": code, "severity": severity, **context})

    def body(path, expected=None):
        if not path:
            raise ValueError("EVIDENCE_PATH_MISSING")
        if path not in cached:
            data = Path(path).read_bytes()
            cached[path] = (data, digest(data))
        data, hashed = cached[path]
        if expected and hashed != expected:
            raise ValueError("EVIDENCE_HASH_MISMATCH")
        return data.decode("utf-8")

    keys = ("raw_product_url", "ranking_position", "rank_context", "source_id", "source_type")
    def signature(row):
        return tuple(str(row.get(key)) for key in keys)

    for page in pages:
        try:
            journal = read_sealed(page["journal_path"])
            raw = journal["observations"]
            if page.get("seed_provenance_path"):
                from .continuation import validate_seed_provenance
                derived = validate_seed_provenance(page,run["plan"],store=store)
                parent_rows = derived["parent_rows"]
                if Counter(map(signature,raw)) != Counter(signature(r) for _,r in parent_rows):
                    raise ValueError("CONTINUATION_IMPORTED_RAW_ROWS_CHANGED")
                parents={b["parent_observation_id"]:r for b,r in parent_rows}
                for row in raw:
                    ids=row.get("parent_observation_ids",[])
                    if (len(ids)!=1 or ids[0] not in parents or row.get("origin")!="HISTORICAL_REPLAY"
                            or row.get("is_derivation") is not True or row.get("parent_run_id")!=run.get("parent_run_id")
                            or row.get("observed_at")!=parents[ids[0]].get("observed_at")
                            or row.get("product_title_raw")!=parents[ids[0]].get("product_title_raw")
                            or row.get("evidence_path")!=parents[ids[0]].get("evidence_path")):
                        raise ValueError("CONTINUATION_ROW_PROVENANCE_CHANGED")
                if page.get("status")!="COMPLETE":
                    fail("PAGE_COMPLETENESS_NOT_PROVEN",page_url=page["page_url"],status=page.get("status"))
                if page.get("source_type_verified") and page.get("source_context_valid"):
                    if any(normalize_product_url(r.get("raw_product_url"))["is_valid"] for r in raw):
                        types.add(page["source_type"])
                        if page.get("research_category") in RESEARCH_CATEGORIES: categories.add(page["research_category"])
                else:
                    fail("PAGE_SOURCE_OR_ACCESS_INVALID",page_url=page["page_url"])
                continue
            html = body(page.get("evidence_path"), page.get("evidence_sha256"))
            source = sources.get(page["source_id"]) if live else page.get("source") or sources.get(page["source_id"])
            if not source and not live:
                source = {"source_id": page["source_id"], "source_type": page["source_type"],
                    "actual_source_url": page["page_url"], "research_category": page.get("research_category")}
            if not source:
                raise ValueError("SOURCE_NOT_IN_REVIEWED_PLAN")
            http_status = page.get("http_status", 200)
            if live:
                proof = [r for r in receipts.get(page["evidence_path"], [])
                    if r.get("kind") == "PAGE" and r.get("request_url") == page["page_url"]
                    and r.get("status_code") == 200 and r.get("access_state") == "NORMAL"]
                if not proof or page["page_url"] not in source.get("page_urls", []):
                    raise ValueError("LIVE_PAGE_WITHOUT_EXACT_NORMAL_REQUEST_PROOF")
                http_status = proof[-1]["status_code"]
                body(page["evidence_path"], proof[-1]["sha256"])
            fragments, seen = [], set()
            for row in raw:
                path = row.get("evidence_path")
                if str(row.get("representation", "")).startswith("ACP") and path not in seen:
                    seen.add(path)
                    if live:
                        proof = [r for r in receipts.get(path, []) if r.get("kind") == "ACP"
                            and r.get("status_code") == 200 and r.get("access_state") == "NORMAL"
                            and r.get("page_key") == page["page_key"]]
                        if not proof:
                            raise ValueError("LIVE_ACP_WITHOUT_NORMAL_REQUEST_PROOF")
                        body(path, proof[-1]["sha256"])
                    fragments.append({"body": body(path, row.get("evidence_sha256")),
                        "status_code": 200, "evidence_path": path})
            parsed = parse_page(html, source=source, run_id=run_id,
                snapshot_id=run.get("snapshot_id", run_id), page_url=page["page_url"],
                page_number=page.get("page_number"), observed_at=page["observed_at"],
                evidence_path=page["evidence_path"], http_status=http_status,
                origin=run.get("origin", "OFFLINE_FIXTURE"), acp_responses=fragments)
            if Counter(map(signature, raw)) != Counter(map(signature, parsed["observations"])):
                fail("RAW_OBSERVATIONS_NOT_BOUND_TO_REPARSED_CARDS", page_url=page["page_url"])
            from .continuation import _verify_original_titles
            _verify_original_titles(raw)
            current_titles={(signature(r),r.get("representation"),r.get("representation_slot")):r.get("product_title_raw")
                for r in parsed["observations"]}
            legacy_defects=[r for r in raw if r.get("parser_version")=="link_first_adapters_v2.1"
                and r.get("product_title_raw")!=current_titles.get((signature(r),r.get("representation"),r.get("representation_slot")))]
            if legacy_defects:
                fail("KNOWN_LEGACY_TITLE_EXTRACTION_DEFECT",page_url=page["page_url"],
                    original_title_parser_version="link_first_adapters_v2.1",
                    corrected_title_parser_version=parsed["page"]["parser_version"],
                    affected_observations=len(legacy_defects),raw_titles_preserved=True,
                    correction_requires_separate_verified_projection=True)
            actual = parsed["page"]
            if actual.get("access_status") != "NORMAL" or not actual.get("source_context_valid") or not actual.get("source_type_verified"):
                fail("PAGE_SOURCE_OR_ACCESS_INVALID", page_url=page["page_url"])
            if actual.get("status") != "COMPLETE":
                fail("PAGE_COMPLETENESS_NOT_PROVEN", severity="ERROR" if live else "WARNING",
                    page_url=page["page_url"], status=actual.get("status"))
            if page.get("actual_count") != actual.get("actual_count"):
                fail("PAGE_COUNT_CONTRADICTS_RAW_EVIDENCE", page_url=page["page_url"])
            if any(normalize_product_url(r.get("raw_product_url"), base_url=page["page_url"])["is_valid"] for r in raw):
                types.add(page["source_type"])
                if page.get("research_category") in RESEARCH_CATEGORIES:
                    categories.add(page["research_category"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            fail("PAGE_EVIDENCE_AUDIT_FAILED", page_url=page.get("page_url"), reason=str(exc))

    for row in store.iter_observations(run_id):
        try:
            body(row.get("evidence_path"), row.get("evidence_sha256"))
            value = normalize_product_url(row.get("raw_product_url"), base_url=row.get("source_url") or "https://www.amazon.es")
            if value["is_valid"]:
                url = value["normalized_product_url"]
                if row.get("source_evidence_eligible") is not False:
                    normalized.add(url)
                    groups.add(value.get("possible_duplicate_key") or url)
                else:
                    fail("UNCONFIRMED_SOURCE_OBSERVATION_EXCLUDED_FROM_TARGET", observation_id=row["observation_id"])
                if row.get("candidate_id") != candidate_id(url) or row.get("normalized_product_url") != url:
                    fail("CANDIDATE_KEY_NOT_LINK_DERIVED", observation_id=row["observation_id"])
            elif row.get("candidate_id"):
                fail("INVALID_LINK_COUNTED", observation_id=row["observation_id"])
        except (OSError, ValueError, KeyError) as exc:
            fail("OBSERVATION_EVIDENCE_INVALID", observation_id=row.get("observation_id"), reason=str(exc))
    if len(normalized) != store.normalized_candidate_count(run_id):
        fail("NORMALIZED_URL_POOL_COUNT_MISMATCH")
    try:
        views=list(store.iter_candidates(run_id))
        reviewed=any(c.get("review_id") for c in views)
        if reviewed:
            groups={c["target_group_key"] for c in views}
        if len(groups) != store.candidate_count(run_id):
            fail("ELIGIBLE_LINK_POOL_COUNT_MISMATCH")
    except (OSError, ValueError, KeyError) as exc:
        views=[]
        fail("IDENTITY_REVIEW_EVIDENCE_INVALID",reason=str(exc))
    if store.summary(run_id).get("pending_page_journals"):
        fail("RAW_PAGE_JOURNALS_NOT_YET_INDEXED")
    requests = store.requests(run_id)
    unresolved = [r["request_id"] for r in requests if r["status"] == "RESERVED"]
    if unresolved:
        fail("AMBIGUOUS_UNFINISHED_REQUESTS", request_ids=unresolved)
    if live:
        if store.get_gate().get("state") != "NORMAL":
            fail("ACCESS_GATE_NOT_NORMAL")
        if types != set(SOURCE_ROOTS):
            fail("FIVE_LIST_CARD_COVERAGE_NOT_PROVEN", missing=sorted(set(SOURCE_ROOTS)-types))
        if run["stage"] == "T3" and categories != set(RESEARCH_CATEGORIES):
            fail("FIFTEEN_CATEGORY_COVERAGE_NOT_PROVEN", missing=sorted(set(RESEARCH_CATEGORIES)-categories))
    if not pages:
        fail("NO_PAGE_EVIDENCE")
    passed = not any(f["severity"] == "ERROR" for f in findings)
    return {"schema_version": 2, "run_id": run_id, "stage": run["stage"], "origin": run.get("origin"),
        "gate_passed": passed, "quality_gate_passed": passed, "findings": findings,
        "unique_valid_links": len(groups), "normalized_unique_urls": len(normalized),
        "strict_independent_buyable_products": sum(c.get("strict_buyable_confirmed",False) for c in views), "source_types_with_valid_cards": sorted(types),
        "research_categories_with_valid_cards": sorted(categories), "page_count": len(pages)}


def _audit_run_v3(store, run_id, run):
    """V3 exact URL pools and centrally reverified card/receipt evidence."""
    from .v3_links import normalize_product_url_v3
    from .v3_quality import audit_observations
    sources={s['source_id']:s for s in run.get('sources',run.get('plan',{}).get('sources',[]))}
    sources={k:{**v,'parser_profile':'V3'} for k,v in sources.items()}
    pages=store.pages(run_id);rows=list(store.iter_observations(run_id))
    findings=[];receipts={};row_pages={};reparsed={};types=set();categories=set()
    from collections import OrderedDict
    replay_receipt_cache=OrderedDict()
    live=run.get('origin')=='LIVE_CAPTURE'
    body_hashes={}
    def verify_body(path,sha):
        key=(path,sha)
        if key not in body_hashes:body_hashes[key]=digest(Path(path).read_bytes())==sha
        return body_hashes[key]
    def fail(code,**context):findings.append({'code':code,'severity':'ERROR',**context})
    def identity(row):
        return tuple(row.get(k) for k in ('raw_product_url','ranking_position','rank_context','representation','representation_slot','evidence_sha256','observed_at'))
    for indexed in store.iter_responses(run_id):
        try:
            proof=read_sealed(indexed['evidence_path'])
            for field in ('request_id','run_id','source_id','source_type','html_path','sha256','observed_at','kind','page_key'):
                if proof.get(field)!=indexed.get(field):raise ValueError('SEALED_INDEX_RECEIPT_CONFLICT:'+field)
            if proof.get('run_id')!=run_id:raise ValueError('RECEIPT_RUN_CONFLICT')
            if not proof.get('observed_at'):raise ValueError('RECEIPT_CAPTURE_TIME_MISSING')
            from .budgets import aware_time
            aware_time(proof['observed_at'])
            receipts.setdefault(proof['html_path'],[]).append(proof)
        except (OSError,ValueError,KeyError,TypeError) as exc:fail('V3_RECEIPT_INVALID',reason=str(exc))
    def receipt_for(row,source):
        if row.get('origin')=='HISTORICAL_REPLAY':
            from .v3_workflows import validate_replay_receipt
            return validate_replay_receipt(row,source,cache=replay_receipt_cache)
        if not live or row.get('origin') not in {None,'LIVE_CAPTURE'} or row.get('is_derivation') is True:return False
        binding=row_pages.get(row.get('observation_id'))
        if not binding or identity(row) not in reparsed.get(binding['page_key'],set()):return False
        kind='PAGE' if row.get('representation')=='SERVER' else 'ACP'
        for proof in receipts.get(row.get('evidence_path'),[]):
            if (proof.get('origin')=='LIVE_CAPTURE' and proof.get('run_id')==run_id
                    and proof.get('source_id')==source['source_id'] and proof.get('source_type')==source['source_type']
                    and proof.get('kind')==kind and proof.get('status_code')==200 and proof.get('access_state')=='NORMAL'
                    and proof.get('sha256')==row.get('evidence_sha256') and proof.get('observed_at')==row.get('observed_at')
                    and proof.get('page_key')==binding['page_key']
                    and (kind!='PAGE' or proof.get('request_url')==row.get('page_url') in source.get('page_urls',[]))):
                return verify_body(proof['html_path'],proof['sha256'])
        return False
    def context_for(row,card,source):
        page=row_pages.get(row.get('observation_id'))
        return bool(page and identity(row) in reparsed.get(page['page_key'],set()))
    for page in pages:
        try:
            journal=read_sealed(page['journal_path'])
            if journal.get('run_id')!=run_id or journal.get('page_key')!=page['page_key']:raise ValueError('JOURNAL_RUN_OR_PAGE_CONFLICT')
            raw=journal['observations'];source=sources[page['source_id']]
            for row in raw:row_pages[row['observation_id']]=page
            html=Path(page['evidence_path']).read_text(encoding='utf8')
            if digest(html.encode('utf8'))!=page['evidence_sha256']:raise ValueError('SERVER_HTML_SHA_CONFLICT')
            fragments=[];seen=set()
            for row in raw:
                if not str(row.get('representation','')).startswith('ACP_') or row['evidence_path'] in seen:continue
                seen.add(row['evidence_path'])
                if row.get('origin')=='HISTORICAL_REPLAY':
                    from .v3_workflows import _read_ref
                    proof=_read_ref(row['capture_receipt_reference'])
                else:
                    matches=[p for p in receipts.get(row['evidence_path'],[]) if p.get('kind')=='ACP' and p.get('page_key')==page['page_key'] and p.get('source_id')==source['source_id'] and p.get('observed_at')==row.get('observed_at') and p.get('sha256')==row.get('evidence_sha256')]
                    if not matches:raise ValueError('ACP_WITHOUT_EXACT_CAPTURE_RECEIPT')
                    proof=matches[-1]
                text=Path(row['evidence_path']).read_text(encoding='utf8')
                if digest(text.encode('utf8'))!=proof['sha256']:raise ValueError('ACP_HTML_SHA_CONFLICT')
                fragments.append({**proof,'body':text,'evidence_path':row['evidence_path']})
            if live:
                from .budgets import aware_time
                # Zero-card or restricted actual ACP responses also belong to
                # this capture; dropping them could falsely imply COMPLETE.
                extras=[proof for values in receipts.values() for proof in values
                    if proof.get('kind')=='ACP' and proof.get('page_key')==page['page_key']
                    and proof.get('source_id')==source['source_id'] and proof.get('source_type')==source['source_type']
                    and proof.get('html_path') not in seen
                    and aware_time(proof['observed_at'])>=aware_time(page['observed_at'])]
                for proof in sorted(extras,key=lambda value:aware_time(value['observed_at'])):
                    path=proof['html_path']
                    if path in seen:continue
                    seen.add(path)
                    if not verify_body(path,proof['sha256']):raise ValueError('ACP_HTML_SHA_CONFLICT')
                    fragments.append({**proof,'body':Path(path).read_text(encoding='utf8'),'evidence_path':path})
            from .budgets import aware_time
            fragments.sort(key=lambda fragment:aware_time(fragment['observed_at']))
            parsed=parse_page(html,source=source,run_id=run_id,snapshot_id=run.get('snapshot_id',run_id),
                page_url=page['page_url'],page_number=page.get('page_number'),observed_at=page['observed_at'],
                evidence_path=page['evidence_path'],http_status=page.get('http_status',200),origin=run.get('origin','OFFLINE_FIXTURE'),acp_responses=fragments)
            actual=parsed['page'];reparsed[page['page_key']]=set(map(identity,parsed['observations']))
            if Counter(map(identity,raw))!=Counter(map(identity,parsed['observations'])):fail('V3_RAW_ROWS_NOT_BOUND_TO_FULL_REPARSE',page_url=page['page_url'])
            expected_rows={identity(r):r for r in parsed['observations']}
            field_keys=('product_title_raw','product_title_status','product_title_evidence','rating_raw','rating_value','rating_status','rating_source','rating_evidence','review_count_raw','review_count','review_count_status','review_count_evidence','rating_observed_at','rating_binding_evidence','rating_parser_version')
            for original in raw:
                expected=expected_rows.get(identity(original))
                if expected is None or any(original.get(key)!=expected.get(key) for key in field_keys):
                    fail('V3_JOURNAL_FIELDS_NOT_BOUND_TO_DOM',observation_id=original.get('observation_id'))
            if actual.get('access_status')!='NORMAL' or not actual.get('source_type_verified') or not actual.get('source_context_valid'):fail('PAGE_SOURCE_OR_ACCESS_INVALID',page_url=page['page_url'])
            if actual.get('status')!='COMPLETE':
                findings.append({'code':'PAGE_COMPLETENESS_NOT_PROVEN','severity':'ERROR' if live else 'WARNING','page_url':page['page_url'],'status':actual.get('status')})
            if page.get('status')=='COMPLETE' and actual.get('status')!='COMPLETE':fail('V3_FALSE_COMPLETE_PAGE',page_url=page['page_url'])
            if page.get('actual_count')!=actual.get('actual_count'):fail('PAGE_COUNT_CONTRADICTS_RAW_EVIDENCE',page_url=page['page_url'])
            if parsed['observations']:
                types.add(page['source_type'])
                if page.get('research_category') in RESEARCH_CATEGORIES:categories.add(page['research_category'])
        except (OSError,ValueError,KeyError,TypeError) as exc:fail('V3_PAGE_EVIDENCE_AUDIT_FAILED',page_url=page.get('page_url'),reason=str(exc))
    domain=audit_observations(rows,sources,receipt_validator=receipt_for,context_validator=context_for)
    findings.extend(domain['findings'])
    normalized=set()
    for row in rows:
        value=normalize_product_url_v3(row.get('raw_product_url'),base_url=row.get('page_url') or 'https://www.amazon.es')
        if value['is_valid'] and row.get('source_evidence_eligible') is not False:normalized.add(value['normalized_product_url'])
    if len(normalized)!=store.normalized_candidate_count(run_id):fail('NORMALIZED_URL_POOL_COUNT_MISMATCH')
    if len(normalized)!=store.candidate_count(run_id):fail('ELIGIBLE_LINK_POOL_COUNT_MISMATCH')
    if store.summary(run_id).get('pending_page_journals'):fail('RAW_PAGE_JOURNALS_NOT_YET_INDEXED')
    unfinished=[r['request_id'] for r in store.requests(run_id) if r['status']=='RESERVED']
    if unfinished:fail('AMBIGUOUS_UNFINISHED_REQUESTS',request_ids=unfinished)
    if live and store.get_gate().get('state')!='NORMAL':fail('ACCESS_GATE_NOT_NORMAL')
    missing=sorted(set(SOURCE_ROOTS)-types)
    if missing:findings.append({'code':'FIVE_LIST_CARD_COVERAGE_NOT_PROVEN','severity':'ERROR' if live else 'WARNING','missing':missing})
    if live and run.get('stage')=='T3' and categories!=set(RESEARCH_CATEGORIES):fail('FIFTEEN_CATEGORY_COVERAGE_NOT_PROVEN',missing=sorted(set(RESEARCH_CATEGORIES)-categories))
    if not pages:fail('NO_PAGE_EVIDENCE')
    passed=not any(f['severity']=='ERROR' for f in findings)
    return {'schema_version':3,'run_id':run_id,'stage':run.get('stage'),'origin':run.get('origin'),
        'gate_passed':passed,'quality_gate_passed':passed,'findings':findings,'unique_valid_links':len(normalized),
        'normalized_unique_urls':len(normalized),'strict_independent_buyable_products':0,
        'source_types_with_valid_cards':sorted(types),'research_categories_with_valid_cards':sorted(categories),
        'page_count':len(pages),'card_field_quality':domain}
