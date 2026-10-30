"""Durable claims and disabled slots for the reviewed three-window PAGE path."""
from __future__ import annotations
import json
import hashlib
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
from ..multisource.contracts import canonical_page_url
from ..multisource.storage import read_sealed


def request_identity(url):
    """Only reorder unique byte-identical query tokens; never drop scope."""
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc not in {'www.amazon.es','amazon.es'} or not p.query:
        return url
    parts=p.query.split('&')
    if any(not item or '=' not in item for item in parts):return url
    if len({item.split('=',1)[0] for item in parts})!=len(parts):return url
    return urlunsplit((p.scheme,p.netloc,p.path,'&'.join(sorted(parts)),p.fragment))


class ParallelPageLedger:
    """Serialize claims and budget before any injected worker fetches a page."""

    def __init__(self, path, *, run_id, original_plan_sha256, deadline, page_budget, min_interval=3.0,
                 prior_consumed=0, prior_last_start=None, family_limits=None, prior_family_consumed=None,
                 cooldown_seconds=0, prior_category=None, prior_cooldown_until=0):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        limits=dict(family_limits or {}); prior=dict(prior_family_consumed or {})
        if set(limits)!=set(prior) or any(type(v) is not int or v<0 for v in (*limits.values(),*prior.values())):
            raise ValueError('EXACT_SOURCE_FAMILY_BUDGET_REQUIRED')
        if any(prior[k]>limits[k] for k in limits) or limits and sum(prior.values())!=prior_consumed:
            raise ValueError('PRIOR_FAMILY_BUDGET_MISMATCH')
        self.identity=(run_id,original_plan_sha256,float(deadline),int(page_budget),float(min_interval),int(prior_consumed),
                       None if prior_last_start is None else float(prior_last_start),
                       json.dumps(limits,sort_keys=True),json.dumps(prior,sort_keys=True),
                       int(cooldown_seconds),prior_category,float(prior_cooldown_until))
        if self.identity[3] < 1 or self.identity[4] < 3 or not 0<=self.identity[5]<=self.identity[3] or self.identity[9]<0:
            raise ValueError('BOUNDED_BUDGET_AND_GLOBAL_PACING_REQUIRED')
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL, deadline REAL NOT NULL, page_budget INTEGER NOT NULL,
                    min_interval REAL NOT NULL, prior_consumed INTEGER NOT NULL, prior_last_start REAL,
                    family_limits TEXT NOT NULL, prior_family_consumed TEXT NOT NULL,
                    cooldown_seconds INTEGER NOT NULL, prior_category TEXT, prior_cooldown_until REAL NOT NULL,
                    current_category TEXT, cooldown_until REAL NOT NULL DEFAULT 0,
                    stop_reason TEXT, next_start REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, canonical TEXT NOT NULL, category TEXT NOT NULL, family TEXT NOT NULL,
                    state TEXT NOT NULL, worker INTEGER, receipt TEXT);
                CREATE INDEX IF NOT EXISTS pages_canonical ON pages(canonical);
                CREATE TABLE IF NOT EXISTS claims(id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE,
                    worker INTEGER NOT NULL, claimed_at REAL NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(alias_url TEXT PRIMARY KEY, primary_url TEXT NOT NULL,
                    evidence_path TEXT NOT NULL, evidence_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS disabled_workers(worker INTEGER PRIMARY KEY,
                    reason TEXT NOT NULL, disabled_at REAL NOT NULL);
            ''')
            current=db.execute('SELECT run_id,plan_sha256,deadline,page_budget,min_interval,prior_consumed,prior_last_start,family_limits,prior_family_consumed,cooldown_seconds,prior_category,prior_cooldown_until FROM config WHERE id=1').fetchone()
            if current is None:
                next_start=0 if prior_last_start is None else float(prior_last_start)+float(min_interval)
                db.execute('INSERT INTO config(id,run_id,plan_sha256,deadline,page_budget,min_interval,prior_consumed,prior_last_start,family_limits,prior_family_consumed,cooldown_seconds,prior_category,prior_cooldown_until,current_category,cooldown_until,next_start) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(*self.identity,prior_category,float(prior_cooldown_until),next_start))
            elif tuple(current)!=self.identity:
                raise ValueError('ORIGINAL_RUN_PLAN_CLOCK_BUDGET_MISMATCH')

    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        db.execute('PRAGMA busy_timeout=30000')
        try:
            yield db
        finally:
            if db.in_transaction:db.rollback()
            db.close()

    def seed(self, pages):
        """New work or already recorded historical pages; never reset a state."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in pages:
                state=item.get('state','PENDING')
                if state not in {'PENDING','COMMITTED','SAVED','HELD'}:
                    raise ValueError('INVALID_SEED_STATE')
                key=request_identity(item['url'])
                row=db.execute('SELECT category,family,state FROM pages WHERE identity=?',(key,)).fetchone()
                if row is not None:
                    if row[:2]!=(item['category'],item['family']): raise ValueError('PAGE_CONTEXT_CONFLICT')
                    if state!='PENDING' and row[2]!=state:raise ValueError('HISTORICAL_STATE_COLLISION_REQUIRES_REVIEW')
                    continue
                host=urlsplit(item['url']).hostname
                canonical=canonical_page_url(item['url']) if host in {'amazon.es','www.amazon.es'} else key
                if not canonical:raise ValueError('INVALID_CANONICAL_PAGE')
                canonical=request_identity(canonical)
                conflicts=db.execute('SELECT url,state FROM pages WHERE canonical=?',(canonical,)).fetchall()
                if conflicts and state=='PENDING':
                    state='HELD'
                    db.execute("UPDATE pages SET state='HELD' WHERE canonical=? AND state='PENDING'",(canonical,))
                elif conflicts and state!='COMMITTED':
                    raise ValueError('CANONICAL_HISTORY_CONFLICT_REQUIRES_REVIEW')
                db.execute('INSERT INTO pages(url,identity,canonical,category,family,state,receipt) VALUES (?,?,?,?,?,?,?)',
                           (item['url'],key,canonical,item['category'],item['family'],state,item.get('receipt')))
            db.commit()

    def claim(self, worker, *, now=None, eligible=None):
        if worker not in (1,2,3): raise ValueError('EXACT_THREE_FIXED_WORKERS_REQUIRED')
        now=time.time() if now is None else float(now)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            deadline,budget,interval,prior,stop,next_start,limits_json,prior_json,category=db.execute(
                'SELECT deadline,page_budget,min_interval,prior_consumed,stop_reason,next_start,family_limits,prior_family_consumed,current_category FROM config WHERE id=1').fetchone()
            if stop or now>=deadline:
                db.commit();return None
            if db.execute('SELECT 1 FROM disabled_workers WHERE worker=?',(worker,)).fetchone():
                db.commit();return None
            if now<next_start:
                db.commit();return None
            if prior+db.execute('SELECT count(*) FROM claims').fetchone()[0]>=budget:
                db.commit();return None
            if db.execute("SELECT 1 FROM pages WHERE state='RESERVED' AND worker=? LIMIT 1",(worker,)).fetchone():
                db.commit();return None
            # A saved response must be replayed first; a held request is never auto retried.
            if db.execute("SELECT 1 FROM pages WHERE state='SAVED' LIMIT 1").fetchone():
                db.commit();return None
            limits=json.loads(limits_json); family_prior=json.loads(prior_json)
            # Category cooldown was removed for PAGE snapshots. Keep historical
            # config/deadline bytes as provenance, without using them as a gate.
            # The shared request interval and all unsettled-claim barriers above
            # and below remain enforced.
            if category is None:
                first=db.execute("SELECT category FROM pages WHERE state='PENDING' ORDER BY rowid LIMIT 1").fetchone()
                if first is None:db.commit();return None
                category=first[0];db.execute('UPDATE config SET current_category=? WHERE id=1',(category,))
            row=None
            for url,family in db.execute("SELECT url,family FROM pages WHERE state='PENDING' AND category=? ORDER BY rowid",(category,)).fetchall():
                if eligible is not None and not eligible(url,family):
                    db.execute("UPDATE pages SET state='SOURCE_SKIPPED' WHERE url=?",(url,));continue
                if limits:
                    if family not in limits:raise ValueError('UNAPPROVED_SOURCE_FAMILY')
                    spent=db.execute('SELECT count(*) FROM claims JOIN pages USING(url) WHERE pages.family=?',(family,)).fetchone()[0]
                    if family_prior[family]+spent>=limits[family]:
                        db.execute("UPDATE pages SET state='BUDGET_SKIPPED' WHERE url=?",(url,));continue
                row=(url,);break
            if row is None:
                # Do not cross categories while a response or held claim remains unresolved.
                unsettled=db.execute("SELECT 1 FROM pages WHERE category=? AND state IN ('RESERVED','SAVED','HELD') LIMIT 1",(category,)).fetchone()
                nxt=db.execute("SELECT category FROM pages WHERE state='PENDING' AND category<>? ORDER BY rowid LIMIT 1",(category,)).fetchone()
                if not unsettled and nxt:
                    db.execute('UPDATE config SET current_category=? WHERE id=1',(nxt[0],))
                db.commit();return None
            url=row[0]
            db.execute("UPDATE pages SET state='RESERVED',worker=? WHERE url=? AND state='PENDING'",(worker,url))
            db.execute("INSERT INTO claims(url,worker,claimed_at,outcome) VALUES (?,?,?,'RESERVED')",(url,worker,now))
            db.execute('UPDATE config SET next_start=? WHERE id=1',(now+interval,))
            db.commit();return url

    def complete(self, worker, url, *, state, receipt, stop_scope='all'):
        if stop_scope not in {'worker','all'}:raise ValueError('EXPLICIT_ACCESS_STOP_SCOPE_REQUIRED')
        if state not in {'SAVED','HELD','ACCESS_STOP'} or not receipt:
            raise ValueError('DURABLE_RECEIPT_AND_OUTCOME_REQUIRED')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state,worker FROM pages WHERE url=?',(url,)).fetchone()
            if row!=('RESERVED',worker):raise ValueError('EXACT_ACTIVE_CLAIM_REQUIRED')
            outcome='HELD' if state=='ACCESS_STOP' else state
            db.execute('UPDATE pages SET state=?,receipt=? WHERE url=?',(outcome,receipt,url))
            db.execute('UPDATE claims SET outcome=? WHERE url=?',(state,url))
            if state=='ACCESS_STOP' and stop_scope=='all':db.execute("UPDATE config SET stop_reason='ACCESS_STOP' WHERE id=1")
            db.commit()

    def disable_worker(self, worker, reason, *, hold_claim=True):
        """Reserve ambiguous work permanently; never reassign it to another slot."""
        if worker not in (1,2,3) or not reason:raise ValueError('EXACT_WORKER_AND_REASON_REQUIRED')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO disabled_workers VALUES (?,?,?)',(worker,reason,time.time()))
            if hold_claim:
                db.execute("UPDATE pages SET state='HELD' WHERE worker=? AND state='RESERVED'",(worker,))
                db.execute("UPDATE claims SET outcome='HELD_AMBIGUOUS' WHERE worker=? AND outcome='RESERVED'",(worker,))
            db.commit()

    def disabled_workers(self):
        with self._connect() as db:
            return dict(db.execute('SELECT worker,reason FROM disabled_workers'))

    def replay_committed(self, url, receipt, *, verify):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state,receipt FROM pages WHERE url=?',(url,)).fetchone()
            if row!=('SAVED',receipt):raise ValueError('EXACT_SAVED_RECEIPT_REQUIRED')
            if not callable(verify) or verify(url,receipt) is not True:
                raise ValueError('SAVED_RECEIPT_AND_PAGE_AUDIT_REQUIRED')
            db.execute("UPDATE pages SET state='COMMITTED' WHERE url=?",(url,));db.commit()

    def resolve_tracking_path_aliases(self, urls, evidence):
        """Explicitly resolve same-category /ref= aliases while retaining raw URLs.

        Narrow offline prototype: never conflates query parameters, category,
        family, in-flight requests or a missing/tampered review record.
        """
        if len(urls)<2 or len(set(urls))!=len(urls):raise ValueError('EXACT_ALIAS_SET_REQUIRED')
        path=Path(evidence.get('path','__missing__'))
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=evidence.get('sha256'):
            raise ValueError('ALIAS_REVIEW_EVIDENCE_REQUIRED')
        review=json.loads(path.read_text(encoding='utf8'))
        if set(review.get('urls',[]))!=set(urls):raise ValueError('ALIAS_REVIEW_SCOPE_MISMATCH')
        if (review.get('kind') not in {'TEST_ONLY_TRACKING_PATH_ALIAS_REVIEW','TRACKING_PATH_ALIAS_REVIEW_V1'}
                or review.get('run_id')!=self.identity[0]
                or review.get('original_plan_sha256')!=self.identity[1]):
            raise ValueError('ALIAS_REVIEW_RUN_BINDING_REQUIRED')
        if review.get('kind')=='TRACKING_PATH_ALIAS_REVIEW_V1' and (
                review.get('evidence_reviewed') is not True or not review.get('reviewed_by') or
                review.get('review_basis')!='EXACT_HOST_PATH_AND_SEALED_NAVIGATION'):
            raise ValueError('EXPLICIT_ALIAS_EVIDENCE_REVIEW_REQUIRED')
        refs=review.get('navigation_receipts',[])
        if not isinstance(refs,list) or {r.get('url') for r in refs}!=set(urls):
            raise ValueError('ALIAS_NAVIGATION_PROOF_REQUIRED')
        for ref in refs:
            nav=Path(ref.get('path','__missing__'))
            if not nav.is_file() or hashlib.sha256(nav.read_bytes()).hexdigest()!=ref.get('sha256'):
                raise ValueError('ALIAS_NAVIGATION_PROOF_CHANGED')
            item=read_sealed(nav)
            if item.get('page_url')!=ref['url'] or item.get('plan_sha256')!=self.identity[1]:
                raise ValueError('ALIAS_NAVIGATION_PROOF_BINDING_REQUIRED')
        parts=[urlsplit(url) for url in urls]
        if any(p.scheme!='https' or p.hostname not in {'amazon.es','www.amazon.es'} or p.query or p.fragment or '/ref=' not in p.path for p in parts):
            raise ValueError('TRACKING_PATH_ALIAS_SCOPE_REQUIRED')
        if len({(p.netloc,p.path.rsplit('/ref=',1)[0]) for p in parts})!=1:
            raise ValueError('EXACT_UNTRACKED_PATH_REQUIRED')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=[db.execute('SELECT category,family,state,canonical FROM pages WHERE url=?',(url,)).fetchone() for url in urls]
            if any(row is None for row in rows) or len({(r[0],r[1],r[3]) for r in rows})!=1:
                raise ValueError('ALIAS_SOURCE_CONTEXT_REQUIRED')
            if any(r[2] not in {'HELD','COMMITTED'} for r in rows) or sum(r[2]=='COMMITTED' for r in rows)>1:
                raise ValueError('ALIAS_MUST_NOT_BE_IN_FLIGHT')
            primary=next((url for url,row in zip(urls,rows) if row[2]=='COMMITTED'),urls[0])
            if rows[urls.index(primary)][2]=='HELD':db.execute("UPDATE pages SET state='PENDING' WHERE url=?",(primary,))
            for url in urls:
                if url==primary:continue
                db.execute("UPDATE pages SET state='ALIASED' WHERE url=?",(url,))
                db.execute('INSERT INTO aliases VALUES (?,?,?,?)',(url,primary,str(path.resolve()),evidence['sha256']))
            db.commit();return primary

    def hold_inflight_after_restart(self):
        """A lost in-flight fetch is ambiguous and must never auto-retry."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            had_inflight=db.execute("SELECT 1 FROM pages WHERE state='RESERVED' LIMIT 1").fetchone()
            db.execute("UPDATE pages SET state='HELD' WHERE state='RESERVED'")
            db.execute("UPDATE claims SET outcome='HELD_AMBIGUOUS' WHERE outcome='RESERVED'")
            if had_inflight:
                db.execute("UPDATE config SET stop_reason=COALESCE(stop_reason,'AMBIGUOUS_RESTART') WHERE id=1")
            db.commit()

    def stop(self, reason):
        if reason not in {'USER_PAUSED','ACCESS_STOP','JOURNAL_FAILURE','CONTROL_STOP'}:raise ValueError('EXPLICIT_STOP_REASON_REQUIRED')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE config SET stop_reason=COALESCE(stop_reason,?) WHERE id=1',(reason,));db.commit()

    def pacing_deadline(self):
        with self._connect() as db:
            return db.execute('SELECT next_start FROM config WHERE id=1').fetchone()[0]

    def scheduler_state(self):
        with self._connect() as db:
            category=db.execute('SELECT current_category FROM config WHERE id=1').fetchone()[0]
            return category,0  # Historical cooldown_until is not an effective wait.

    def acknowledge_user_resume(self,store_root):
        from ..multisource.storage import digest
        root=Path(store_root);pause=json.loads((root/'snapshot_pause.json').read_text(encoding='utf8'))
        release=json.loads((root/'snapshot_pause_release.json').read_text(encoding='utf8'))
        if any(record.get('run_id')!=self.identity[0] or record.get('plan_sha256')!=self.identity[1]
                for record in (pause,release)) or release.get('pause_sha256')!=digest(pause):
            raise ValueError('EXACT_USER_PAUSE_RELEASE_REQUIRED')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            reason=db.execute('SELECT stop_reason FROM config WHERE id=1').fetchone()[0]
            if reason!='USER_PAUSED':raise ValueError('ONLY_USER_PAUSE_MAY_BE_RELEASED')
            if db.execute("SELECT 1 FROM pages WHERE state IN ('RESERVED','HELD','SAVED') LIMIT 1").fetchone():
                raise ValueError('UNRESOLVED_REQUESTS_MUST_NOT_BE_RELEASED')
            db.execute('CREATE TABLE IF NOT EXISTS user_resumes(release_sha256 TEXT PRIMARY KEY,release_json TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO user_resumes VALUES (?,?)',(digest(release),json.dumps(release,sort_keys=True)))
            db.execute('UPDATE config SET stop_reason=NULL WHERE id=1')
            db.commit()

    def persist_actual_dispatch_deadline(self, next_start):
        """Persist conservative pacing for a single coordinator; never reset."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous,stop=db.execute('SELECT next_start,stop_reason FROM config WHERE id=1').fetchone()
            if stop or float(next_start)<previous:
                raise ValueError('PACING_STOP_OR_RESET_FORBIDDEN')
            db.execute('UPDATE config SET next_start=? WHERE id=1',(float(next_start),));db.commit()

    def snapshot(self):
        with self._connect() as db:
            return {'config':db.execute('SELECT stop_reason,next_start FROM config WHERE id=1').fetchone(),
                    'pages':db.execute('SELECT url,category,family,state,worker,receipt FROM pages ORDER BY rowid').fetchall(),
                    'claims':db.execute('SELECT url,worker,claimed_at,outcome FROM claims ORDER BY id').fetchall(),
                    'aliases':db.execute('SELECT alias_url,primary_url,evidence_path,evidence_sha256 FROM aliases ORDER BY alias_url').fetchall()}
