"""One-owner detail queue, derived from the reviewed 5500 dispatch scheduler.

Only reservation and observed initial Document starts are serialized. The
capture adapter owns durable evidence and must handle an already reserved,
unsent token even if another lane stops admission. No retries are issued here.
"""
from __future__ import annotations

import asyncio
import math
import time


class DetailDispatchScheduler:
    def __init__(self, reserve, capture, *, lanes=5, interval=3.6,
                 clock=time.perf_counter, sleep=asyncio.sleep, start_timeout=60):
        if lanes not in (1,3,5) or isinstance(lanes,bool):
            raise ValueError('SUPPORTED_DETAIL_LANES_REQUIRED')
        if (isinstance(interval,bool) or not isinstance(interval,(int,float))
                or not math.isfinite(interval) or interval<3.6):
            raise ValueError('GLOBAL_3_6_SECOND_PACING_REQUIRED')
        self.reserve,self.capture=reserve,capture
        self.lanes,self.interval,self.clock,self.sleep=lanes,interval,clock,sleep
        self.start_timeout=start_timeout
        self.lock=asyncio.Lock()
        self.stop=asyncio.Event()
        self.failure=None
        self.access_failure=False
        self.last_start=None
        self.active=self.peak_active=0
        self.claimed=[]
        self._captures=[]

    def fail(self,exc,*,access=False):
        if self.failure is None or (access and not self.access_failure):
            self.failure,self.access_failure=exc,access
        self.stop.set()

    async def _capture(self,token,sent):
        try:
            await self.capture(token,sent)
        except Exception as exc:
            self.fail(exc)
        finally:
            self.active-=1

    async def _lane(self,lane,queue):
        while not self.stop.is_set():
            try:
                task=queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                async with self.lock:
                    if self.stop.is_set():return
                    token=await self.reserve(task,lane)
                    if self.last_start is not None:
                        deadline=self.last_start+self.interval
                        while not self.stop.is_set() and self.clock()<deadline:
                            await self.sleep(max(0,deadline-self.clock()))
                    event=asyncio.Event()
                    def sent():
                        if event.is_set():
                            raise ValueError('DUPLICATE_INITIAL_DOCUMENT_START')
                        self.last_start=self.clock()
                        event.set()
                    self.claimed.append(task['task_id'])
                    self.active+=1
                    self.peak_active=max(self.peak_active,self.active)
                    capture=asyncio.create_task(self._capture(token,sent))
                    self._captures.append(capture)
                    started=asyncio.create_task(event.wait())
                    try:
                        done,_=await asyncio.wait((capture,started),timeout=self.start_timeout,
                                                 return_when=asyncio.FIRST_COMPLETED)
                        if not done or (capture in done and not event.is_set()):
                            self.fail(RuntimeError('INITIAL_DOCUMENT_START_NOT_OBSERVED'))
                    finally:
                        started.cancel()
                        await asyncio.gather(started,return_exceptions=True)
                await capture
            except Exception as exc:
                self.fail(exc)
                return

    async def run(self,tasks):
        if self.active:raise RuntimeError('SCHEDULER_ALREADY_RUNNING')
        self.claimed=[];self._captures=[];self.peak_active=0
        tasks=list(tasks)
        ids=[task['task_id'] for task in tasks]
        if len(ids)!=len(set(ids)):
            raise ValueError('DUPLICATE_FROZEN_TASK_CLAIM')
        queue=asyncio.Queue()
        for task in tasks:queue.put_nowait(task)
        try:
            await asyncio.gather(*(self._lane(lane,queue) for lane in range(self.lanes)))
        finally:
            await asyncio.gather(*self._captures,return_exceptions=True)
        if self.active:
            raise RuntimeError('CAPTURE_NOT_DRAINED')
        if self.failure:
            raise self.failure
