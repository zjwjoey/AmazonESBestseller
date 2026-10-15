"""Bounded T2 diagnostics over the existing serial T1 engine. T3 stays closed."""
from .v4_runner import V4T1Runner
from .v4_capacity import audit_capacity, execution_for_store
from ..multisource.storage import immutable_json, digest
import hashlib


class V4T2Runner(V4T1Runner):
    stage='T2'

    def _record_approval(self,plan,approval):
        # Keep atomic temporary paths below Windows MAX_PATH in deep worktrees.
        path=self.store.root/'runs'/plan['run_id']/('approval_'+digest(approval)[:16]+'.json')
        if not path.exists():immutable_json(path,approval)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest(approval):
            raise ValueError('IMMUTABLE_EXECUTION_APPROVAL_CONFLICT')
        references=list(self.store.get_run(plan['run_id']).get('execution_approval_references',[]))
        ref={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        if ref not in references:references.append(ref)
        self.store.update_run(plan['run_id'],execution_approval_references=references)

    def run(self,plan,**kwargs):
        result=super().run(plan,**kwargs)
        execution=execution_for_store(self.store,plan['run_id'])
        capacity=audit_capacity(self.store.pages(plan['run_id']),list(self.store.iter_observations(plan['run_id'])),
            plan['sources'],execution)
        self.store.update_run(plan['run_id'],capacity_audit=capacity,
            quality_gate_passed=capacity['diagnostic_passed'],T3_execution_enabled=False)
        return {**result,'run':self.store.get_run(plan['run_id']),'capacity':capacity}
