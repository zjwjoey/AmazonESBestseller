"""Bounded asynchronous dispatch. Reservation/start are serialized, responses are not."""
from __future__ import annotations
import asyncio
import time


class ThreeRequestScheduler:
    def __init__(self, reserve, capture, *, interval=3, clock=time.monotonic, sleep=asyncio.sleep):
        if interval < 3:
            raise ValueError('ORIGINAL_GLOBAL_THREE_SECOND_PACING_REQUIRED')
        self.reserve, self.capture = reserve, capture
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.lock = asyncio.Lock()
        self.stop = asyncio.Event()
        self.failure = None
        self.access_failure = False
        self.last_start = None
        self.active = self.peak_active = 0
        self.claimed = []

    def fail(self, exc, *, access=False):
        # A later access restriction takes precedence over an identity pause.
        if self.failure is None or access and not self.access_failure:
            self.failure = exc
            self.access_failure = access
        self.stop.set()

    async def _capture(self, token, sent):
        try:
            await self.capture(token, sent)
        except Exception as exc:
            self.fail(exc)
        finally:
            self.active -= 1

    async def _lane(self, lane, queue):
        while not self.stop.is_set():
            try:
                task = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                async with self.lock:
                    if self.stop.is_set():return
                    if self.last_start is not None:
                        await self.sleep(max(0, self.last_start+self.interval-self.clock()))
                    if self.stop.is_set():return
                    token = await self.reserve(task, lane)
                    # reserve may await validation while another lane observes a
                    # restriction. Capture must save even a reserved unsent task.
                    event = asyncio.Event()
                    def sent():
                        if event.is_set():raise ValueError('DUPLICATE_INITIAL_DOCUMENT_START')
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
                    # Only the actual dispatch boundary is serialized. Never
                    # hold this lock until HTTP response/render/parse completes.
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
        for task in tasks:queue.put_nowait(task)
        await asyncio.gather(*(self._lane(lane, queue) for lane in range(3)))
        if self.active:
            raise RuntimeError('CAPTURE_NOT_DRAINED')
        if self.failure:
            raise self.failure
