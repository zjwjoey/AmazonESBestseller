"""Two in-flight captures with a non-bursting 1,000 requests/hour target.

The target is a maximum dispatch cadence, not a promise of successful products.
Reservation, actual-start acknowledgement, global stop and evidence draining use
the existing reviewed scheduler. Responses never hold the dispatch lock.
"""
from __future__ import annotations

import asyncio
import math

from .detail_parallel5500 import ThreeRequestScheduler

TARGET_REQUESTS_PER_HOUR = 1000
TARGET_START_INTERVAL_SECONDS = 3600 / TARGET_REQUESTS_PER_HOUR


class TwoRequestScheduler(ThreeRequestScheduler):
    def __init__(self, reserve, capture, *, interval=TARGET_START_INTERVAL_SECONDS, **kwargs):
        if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or interval < TARGET_START_INTERVAL_SECONDS):
            raise ValueError('DUAL_1000_NON_BURSTING_PACING_REQUIRED')
        super().__init__(reserve, capture, interval=interval, **kwargs)

    async def run(self, tasks):
        tasks = list(tasks)
        identities = [t['task_id'] if isinstance(t, dict) else t for t in tasks]
        if len(set(identities)) != len(identities):
            raise ValueError('DUPLICATE_FROZEN_TASK_CLAIM')
        queue = asyncio.Queue()
        for task in tasks:
            queue.put_nowait(task)
        await asyncio.gather(*(self._lane(lane, queue) for lane in range(2)))
        if self.active:
            raise RuntimeError('CAPTURE_NOT_DRAINED')
        if self.failure:
            raise self.failure
