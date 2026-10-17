"""Explicitly approved three-plus-two lanes in one original browser context.

One persistent profile owner, queue, FIFO writer, cumulative budget and global
3.6-second dispatch clock. This is never two competing collector processes.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime,timezone

from . import detail_dual_runtime5500 as dual
from .detail_five5500 import FiveRequestScheduler
from . import detail_item_errors5500 as item_errors


def validate_parallel_mode(root, plan, auth):
    item_errors.read_policy(root,plan,auth)
    return dual._validate_mode(root, plan, auth, lanes=5, backlog=5,
        scope='FIVE_LANES_GROUPED_3_PLUS_2_SAME_FROZEN_RUN', groups=[3, 2])


class FiveCollector(dual.ParallelCollector):
    lane_count = 5
    max_uncommitted = 5
    execution_mode = 'FIVE_LANES_GROUPED_3_PLUS_2'
    scheduler_type = FiveRequestScheduler

    def __init__(self,bridge,auth,**kwargs):
        super().__init__(bridge,auth,**kwargs)
        self.item_error_policy=item_errors.read_policy(self.root,self.plan,auth)
        self.preserve_browser_on_fatal_error=bool(self.item_error_policy)

    def should_stop_for_result(self,result,meta):
        if result['status']=='SUCCESS':return False
        return not (self.item_error_policy and item_errors.eligible(result,meta))

    async def after_durable_result(self,task,result):
        if (self.item_error_policy and result['status'] in item_errors.STATUSES
                and result.get('reason') not in item_errors.GLOBAL_REASONS):
            meta=json.loads(Path(result['response_metadata_path']).read_text(encoding='utf8'))
            if item_errors.eligible(result,meta):
                await asyncio.to_thread(item_errors.persist_failure,self.bridge,self.auth,task,result)

    async def hold_failed_state(self,exc):
        """Block all new network but retain the process/pages on serious errors.

        Disk failure must not make a second status/export write close the browser.
        Explicit human closure or the original deadline still ends the hold.
        """
        self.scheduler.fail(exc)
        def diagnostic(message):
            try:print(message,file=sys.stderr,flush=True)
            except OSError:pass
        diagnostic(type(exc).__name__+': '+str(exc))
        try:
            from .quick5500_cli import export
            await asyncio.to_thread(export,self.root)
        except Exception as secondary:
            diagnostic('SECONDARY_EXPORT_ERROR: '+repr(secondary))
        try:
            dual.atomic_json(self.root/'worker_status.json',dict(
                phase='WAITING_MANUAL_ASSISTANCE' if self.scheduler.access_failure else 'PAUSED_ERROR_PRESERVED',
                updated_at=dual.utc(),error=type(exc).__name__+': '+str(exc),
                new_network_requests_blocked=True,automatic_resume=False,
                human_network_handoff_requires_review=True,network_parallelism=5,visible_browser=True))
        except Exception as secondary:
            diagnostic('SECONDARY_STATUS_WRITE_ERROR: '+repr(secondary))
        marker=self.root/'manual_assistance.close.requested'
        try:original=marker.read_bytes() if marker.exists() else None
        except OSError:original=None
        while (any(not page.is_closed() for page in self.pages)
                and datetime.now(timezone.utc)<datetime.fromisoformat(self.auth['deadline'])):
            try:
                if marker.exists() and marker.read_bytes()!=original:break
            except OSError:pass
            await asyncio.sleep(1)

    def validate_mode(self):
        return validate_parallel_mode(self.root, self.plan, self.auth)


async def run(root, authorization, profile):
    return await dual.run(root, authorization, profile,
                          collector_type=FiveCollector, mode_validator=validate_parallel_mode,
                          task_selector=item_errors.pending_tasks)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--profile', required=True)
    args=parser.parse_args()
    asyncio.run(run(args.root, args.authorization, args.profile))


if __name__ == '__main__': main()
