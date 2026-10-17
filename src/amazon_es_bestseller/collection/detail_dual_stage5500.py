"""Frozen candidate transaction limits, distinct from success or FINAL quotas."""
import json
from pathlib import Path
from .quick_selection import file_sha
from ..multisource.storage import digest


def reference(root, ref):
    path = Path(ref['path'])
    if path.resolve().parent != Path(root).resolve() or file_sha(path) != ref['sha256']:
        raise ValueError('DUAL_STAGE_REFERENCE_CHANGED_OR_OUTSIDE_RUN')
    return json.loads(path.read_text(encoding='utf8'))


class Stage:
    def __init__(self, document):
        self.document = document
        self.tasks = document['cohort']
        self.claimed = set()
        self.by_id = {task['task_id']: task for task in self.tasks}

    def claim(self, task):
        task_id = task['task_id']
        if (self.by_id.get(task_id) != task or task_id in self.claimed
                or len(self.claimed) >= self.document['transaction_limit']):
            raise ValueError('DUAL_STAGE_COHORT_OR_TRANSACTION_LIMIT')
        self.claimed.add(task_id)


def load_stage(root, plan, auth, pending):
    try:
        doc = reference(root, auth['dual_stage_reference'])
        cumulative = doc['cumulative_limit']
        if isinstance(cumulative, bool) or cumulative not in (100, 500, 1000):
            raise ValueError('DUAL_STAGE_INVALID_CUMULATIVE_LIMIT')
        previous = {100: 0, 500: 100, 1000: 500}[cumulative]
        limit = cumulative-previous
        if (doc.get('approved') is not True or doc.get('run_id') != plan['run_id']
                or doc.get('runtime_root') != str(Path(root).resolve())
                or doc.get('plan_sha256') != digest(plan)
                or doc.get('code_sha') != auth['code_sha']
                or doc.get('previous_cumulative_limit') != previous
                or doc.get('transaction_limit') != limit
                or doc.get('no_successful_task_replay') is not True
                or len(pending) < limit or doc.get('cohort') != pending[:limit]
                or len({t['task_id'] for t in doc['cohort']}) != limit):
            raise ValueError('DUAL_STAGE_EXACT_FROZEN_PENDING_COHORT_REQUIRED')
        if previous:
            accepted = reference(root, doc['previous_acceptance_reference'])
            if (accepted.get('passed') is not True
                    or accepted.get('independent_full_record_audit') is not True
                    or accepted.get('run_id') != plan['run_id']
                    or accepted.get('plan_sha256') != digest(plan)
                    or accepted.get('cumulative_limit') != previous
                    or accepted.get('no_successful_task_replay') is not True):
                raise ValueError('DUAL_STAGE_PREVIOUS_INDEPENDENT_ACCEPTANCE_REQUIRED')
        marker = Path(root)/f'dual_stage_{cumulative}.drained.json'
        if marker.exists(): raise ValueError('DUAL_STAGE_ALREADY_EXECUTED_NO_REPLAY')
        return Stage(doc)
    except (KeyError, TypeError) as exc:
        raise ValueError('EXPLICIT_BOUNDED_DUAL_STAGE_REQUIRED') from exc


def stage_result(stage, progress, authorization_sha256):
    return dict(phase='DUAL_STAGE_DRAINED_AWAITING_INDEPENDENT_REVIEW', passed=False,
        not_final_acceptance=True, run_id=stage.document['run_id'],
        plan_sha256=stage.document['plan_sha256'], cumulative_limit=stage.document['cumulative_limit'],
        transaction_limit=stage.document['transaction_limit'],
        attempted_transactions=len(stage.claimed), task_ids=sorted(stage.claimed),
        cohort_sha256=digest(stage.tasks), authorization_sha256=authorization_sha256,
        progress=progress, automatic_next_stage=False)
