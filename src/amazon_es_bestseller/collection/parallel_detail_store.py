"""Durable evidence for the single-owner parallel detail executor.

No original checkpoint or HTML is overwritten. A reservation without a terminal
outcome remains unresolved after restart and requires review, never a new GET.
"""
from __future__ import annotations

from pathlib import Path
import json

from ..models import is_valid_asin
from ..multisource.storage import digest, immutable_bytes, immutable_json, read_sealed, sealed


class ParallelDetailStore:
    def __init__(self,root,plan,budget):
        self.root=Path(root).resolve()
        self.plan,self.budget=plan,budget
        self._reservation_count=None
        self.tasks={t['task_id']:dict(t) for t in plan['tasks']}
        if len(self.tasks)!=len(plan['tasks']):raise ValueError('DUPLICATE_TASK_ID')
        asins=[t['asin'] for t in self.tasks.values()]
        if len(asins)!=len(set(asins)) or any(not is_valid_asin(a) for a in asins):
            raise ValueError('DUPLICATE_OR_INVALID_PRODUCT_IDENTITY')
        for key in ('max_requests','prior_requests','budget_holds'):
            if type(budget.get(key)) is not int or budget[key]<0:
                raise ValueError('INVALID_CUMULATIVE_BUDGET')
        if budget['prior_requests']+budget['budget_holds']>budget['max_requests']:
            raise ValueError('CUMULATIVE_BUDGET_EXHAUSTED')
        immutable_json(self.root/'parallel_contract.json',sealed({'plan':plan,'budget':budget}))

    def directory(self,task):
        if self.tasks.get(task['task_id'])!=task:raise ValueError('TASK_PLAN_BINDING_CHANGED')
        return self.root/'parallel_tasks'/digest(task['task_id'].encode())

    @property
    def used_requests(self):
        if self._reservation_count is not None:
            return self.budget['prior_requests']+self.budget['budget_holds']+self._reservation_count
        reserved=0
        for task in self.tasks.values():
            path=self.directory(task)/'reservation.json'
            if path.exists():
                value=read_sealed(path)
                if value.get('task')!=task or value.get('run_id')!=self.plan['run_id']:
                    raise ValueError('RESERVATION_EVIDENCE_CHANGED')
                reserved+=1
        self._reservation_count=reserved
        return self.budget['prior_requests']+self.budget['budget_holds']+reserved

    def reserve(self,task,lane):
        directory=self.directory(task)
        if (directory/'reservation.json').exists() or (directory/'outcome.json').exists():
            raise ValueError('TASK_ALREADY_RESERVED_NO_RETRY')
        if self.used_requests>=self.budget['max_requests']:
            raise ValueError('CUMULATIVE_BUDGET_EXHAUSTED')
        fresh=immutable_json(directory/'reservation.json',sealed({'run_id':self.plan['run_id'],
            'task':task,'lane':lane,'conservative_budget_charged':True}))
        if not fresh:raise ValueError('DUPLICATE_TASK_RESERVATION')
        self._reservation_count+=1

    def save_capture(self,task,body,metadata):
        directory=self.directory(task)
        if not (directory/'reservation.json').exists():raise ValueError('UNRESERVED_CAPTURE')
        immutable_bytes(directory/'response.html',body)
        receipt={'run_id':self.plan['run_id'],'task':task,'html_sha256':digest(body),
                 'capture':metadata}
        immutable_json(directory/'receipt.json',sealed(receipt))
        return {'html_path':str((directory/'response.html').relative_to(self.root)),
                'html_sha256':digest(body),
                'receipt_path':str((directory/'receipt.json').relative_to(self.root)),
                'receipt_sha256':digest((directory/'receipt.json').read_bytes())}

    def _verify(self,task,result):
        if result.get('origin')=='LEGACY_CHECKPOINT_VERIFIED_SHA':
            from .detail_executor import _usable_record
            refs=result['history_references']
            if not {'checkpoint','html'}<=set(refs):raise ValueError('LEGACY_EVIDENCE_MISSING')
            for ref in refs.values():
                path=(self.root/ref['path']).resolve()
                if not path.is_relative_to(self.root) or digest(path.read_bytes())!=ref['sha256']:
                    raise ValueError('LEGACY_EVIDENCE_CHANGED')
            cp=json.loads((self.root/refs['checkpoint']['path']).read_text(encoding='utf8'))
            if (cp.get('status')!='success' or cp.get('record')!=result.get('record')
                    or not _usable_record(cp.get('record'),task['asin'])):
                raise ValueError('LEGACY_SUCCESS_NOT_VERIFIED')
            return
        for key in ('html','receipt'):
            relative=result.get(key+'_path')
            if not relative:
                if result.get('status') in ('UNSENT','SYSTEM_ERROR'):continue
                raise ValueError('TERMINAL_EVIDENCE_MISSING')
            path=(self.root/relative).resolve()
            if not path.is_relative_to(self.directory(task)):
                raise ValueError('EVIDENCE_PATH_OUTSIDE_TASK')
            if digest(path.read_bytes())!=result.get(key+'_sha256'):
                raise ValueError('SAVED_EVIDENCE_CHANGED')
        if result.get('receipt_path'):
            receipt=read_sealed(self.root/result['receipt_path'])
            if (receipt.get('task')!=task or receipt.get('run_id')!=self.plan['run_id']
                    or receipt.get('html_sha256')!=result.get('html_sha256')):
                raise ValueError('RECEIPT_TASK_EVIDENCE_CHANGED')

    def finish(self,task,result):
        directory=self.directory(task)
        if not (directory/'reservation.json').exists():raise ValueError('UNRESERVED_RESULT')
        if (directory/'outcome.json').exists():raise ValueError('TERMINAL_OUTCOME_IMMUTABLE')
        self._verify(task,result)
        immutable_json(directory/'outcome.json',sealed({'run_id':self.plan['run_id'],
                                                       'task':task,'result':result}))

    def outcomes(self):
        result=[]
        for task in self.tasks.values():
            directory=self.directory(task);path=directory/'outcome.json'
            if path.exists():
                value=read_sealed(path)
                if value.get('task')!=task or value.get('run_id')!=self.plan['run_id']:
                    raise ValueError('TASK_STATE_EVIDENCE_CHANGED')
                self._verify(task,value['result'])
                result.append(value)
            elif (directory/'reservation.json').exists():
                raise ValueError('UNRESOLVED_RESERVATION_NO_AUTO_RETRY')
        return result

    def pending(self):
        outcomes=self.outcomes()
        if any(row['result'].get('status') in ('ACCESS_STOP','SYSTEM_ERROR') for row in outcomes):
            from ..access.detector import AccessStopError
            raise AccessStopError('PERSISTED_GLOBAL_STOP_REQUIRES_EXPLICIT_RECOVERY')
        consumed={row['task']['task_id'] for row in outcomes}
        return [task for key,task in self.tasks.items() if key not in consumed]

    def import_legacy_successes(self):
        """Hash-bind readable V1/V2 successes; never rewrite their old files.

        Saved evidence without a usable success checkpoint blocks admission
        rather than being silently fetched again. It needs offline review.
        """
        from .detail_executor import _usable_record
        prepared=[]
        existing=sum(row['result'].get('origin')=='LEGACY_CHECKPOINT_VERIFIED_SHA'
                     for row in self.outcomes())
        for task in self.tasks.values():
            cp_path=self.root/'checkpoints'/(task['asin']+'.json')
            html=self.root/'html'/(task['asin']+'.html')
            outcome=self.directory(task)/'outcome.json'
            if outcome.exists():continue
            if not cp_path.exists() and not html.exists():continue
            if not cp_path.exists() or not html.exists():raise ValueError('LEGACY_SAVED_EVIDENCE_REVIEW_REQUIRED')
            cp=json.loads(cp_path.read_text(encoding='utf8'))
            if cp.get('status')!='success' or not _usable_record(cp.get('record'),task['asin']):
                raise ValueError('LEGACY_FAILED_TASK_NO_AUTO_RETRY')
            refs={key:{'path':str(path.relative_to(self.root)),'sha256':digest(path.read_bytes())}
                  for key,path in (('checkpoint',cp_path),('html',html))}
            meta=html.with_suffix('.meta.json')
            if meta.exists():refs['metadata']={'path':str(meta.relative_to(self.root)),'sha256':digest(meta.read_bytes())}
            result={'status':'SUCCESS','origin':'LEGACY_CHECKPOINT_VERIFIED_SHA',
                    'record':cp['record'],'history_references':refs}
            self._verify(task,result)
            prepared.append((outcome,{'run_id':self.plan['run_id'],'task':task,'result':result}))
        if existing+len(prepared)>self.budget['prior_requests']:
            raise ValueError('LEGACY_SUCCESSES_EXCEED_INHERITED_PAID_BUDGET')
        for outcome,value in prepared:
            self._verify(value['task'],value['result'])
            immutable_json(outcome,sealed(value))
        return len(prepared)
