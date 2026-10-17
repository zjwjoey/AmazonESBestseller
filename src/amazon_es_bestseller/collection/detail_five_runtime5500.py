"""Explicitly approved three-plus-two lanes in one original browser context.

One persistent profile owner, queue, FIFO writer, cumulative budget and global
3.6-second dispatch clock. This is never two competing collector processes.
"""
import argparse
import asyncio
import json
from pathlib import Path

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
