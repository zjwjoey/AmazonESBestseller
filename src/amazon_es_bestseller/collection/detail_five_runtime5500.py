"""Explicitly approved three-plus-two lanes in one original browser context.

One persistent profile owner, queue, FIFO writer, cumulative budget and global
3.6-second dispatch clock. This is never two competing collector processes.
"""
import argparse
import asyncio

from . import detail_dual_runtime5500 as dual
from .detail_five5500 import FiveRequestScheduler


def validate_parallel_mode(root, plan, auth):
    return dual._validate_mode(root, plan, auth, lanes=5, backlog=5,
        scope='FIVE_LANES_GROUPED_3_PLUS_2_SAME_FROZEN_RUN', groups=[3, 2])


class FiveCollector(dual.ParallelCollector):
    lane_count = 5
    max_uncommitted = 5
    execution_mode = 'FIVE_LANES_GROUPED_3_PLUS_2'
    scheduler_type = FiveRequestScheduler

    def validate_mode(self):
        return validate_parallel_mode(self.root, self.plan, self.auth)


async def run(root, authorization, profile):
    return await dual.run(root, authorization, profile,
                          collector_type=FiveCollector, mode_validator=validate_parallel_mode)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--authorization', required=True)
    parser.add_argument('--profile', required=True)
    args=parser.parse_args()
    asyncio.run(run(args.root, args.authorization, args.profile))


if __name__ == '__main__': main()
