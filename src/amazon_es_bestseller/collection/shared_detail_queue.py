"""Durable, exclusive ASIN claims over an already verified frozen detail plan.

No browser, request, identity promotion, retry or access recovery lives here.
An unresolved or restricted claim stays with its original worker.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from .runtime_io import read_json


class SharedDetailQueue:
    def __init__(self, root, tasks, *, is_success, blocked=()):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.tasks=list(tasks);self.is_success=is_success;self.blocked=set(blocked)
        self.lock=threading.RLock();self.position=0
        asins=[t['asin'] for t in self.tasks]
        if len(asins)!=len(set(asins)) or any(not re.fullmatch(r'[A-Z0-9]{10}',a) for a in asins):
            raise ValueError('DUPLICATE_OR_INVALID_PRODUCT_IDENTITY')
        body=json.dumps(self.tasks,ensure_ascii=False,sort_keys=True).encode('utf-8')
        digest=hashlib.sha256(body).hexdigest();path=self.root/'frozen_tasks.json'
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('FROZEN_TASKS_CHANGED')
        else:self._create(path,body)
        for name in ['claims','results']:(self.root/name).mkdir(exist_ok=True)
        self.plan_hash=digest

    @staticmethod
    def _create(path,body):
        with path.open('xb') as handle:
            handle.write(body);handle.flush();os.fsync(handle.fileno())

    def claim(self,worker):
        if worker not in (1,2,3):raise ValueError('INVALID_WORKER')
        with self.lock:
            while self.position<len(self.tasks):
                task=self.tasks[self.position];self.position+=1
                asin=task['asin'];path=self.root/'claims'/(asin+'.json')
                if asin in self.blocked or self.is_success(asin) or path.exists():continue
                record={'worker':worker,'asin':asin,'plan_hash':self.plan_hash,'task':task}
                try:self._create(path,json.dumps(record,ensure_ascii=False).encode('utf-8'))
                except FileExistsError:continue
                return task
        return None

    def finish(self,asin,worker,status):
        with self.lock:
            claim=read_json(self.root/'claims'/(asin+'.json'))
            if claim['worker']!=worker:raise ValueError('CLAIM_OWNER_MISMATCH')
            if status=='SUCCESS' and not self.is_success(asin):raise ValueError('SUCCESS_EVIDENCE_REQUIRED')
            result={'asin':asin,'worker':worker,'status':status,'plan_hash':self.plan_hash}
            self._create(self.root/'results'/(asin+'.json'),json.dumps(result).encode('utf-8'))
