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

    async def _lane(self, lane, queue):
        while not self.stop.is_set():
            try:
                task = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                async with self.lock:
                    if self.stop.is_set():
                        return
                    # Preserve every original validation and durable reservation,
                    # but perform it during the global pacing window. Sleeping
                    # before validation would add its entire duration to each gap.
                    token = await self.reserve(task, lane)
                    if self.last_start is not None and not self.stop.is_set():
                        await self.sleep(max(0, self.last_start+self.interval-self.clock()))
                    # Even if a stop arrives during validation/pacing, capture
                    # owns the already-reserved token and must persist it unsent.
                    # Runtime capture/route guards refuse navigation after stop.
                    event = asyncio.Event()
                    def sent():
                        if event.is_set():
                            raise ValueError('DUPLICATE_INITIAL_DOCUMENT_START')
                        self.last_start = self.clock()
                        event.set()
                    self.claimed.append(task)
                    self.active += 1
                    self.peak_active = max(self.peak_active, self.active)
                    capture = asyncio.create_task(self._capture(token, sent))
                    started = asyncio.create_task(event.wait())
                    try:
                        done, _ = await asyncio.wait([started, capture], timeout=60,
                                                     return_when=asyncio.FIRST_COMPLETED)
                        if not done or capture in done and not event.is_set():
                            self.fail(RuntimeError('INITIAL_DOCUMENT_START_NOT_OBSERVED'))
                    finally:
                        started.cancel()
                await capture
            except Exception as exc:
                self.fail(exc)
                return

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
