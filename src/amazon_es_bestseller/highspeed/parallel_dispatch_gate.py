"""Shared asynchronous dispatch pacing for the reviewed three-window adapter."""
import asyncio
import time


class DispatchStopped(RuntimeError):
    pass


class AsyncDispatchGate:
    """Serialize the actual dispatch callback, not merely earlier queue claims.

    One process/event loop owns this object. The callback must dispatch a
    nonblocking request and return only after the dispatch has been initiated.
    The parallel CLI/transport supplies durable journals, fresh admission and
    exclusive run ownership. This gate alone never grants collection permission.
    """
    def __init__(self, *, interval=3.0, deadline, clock=time.time, wait=None,
                 prior_next_start=0.0, persist_next_start=None):
        if interval<3:raise ValueError('GLOBAL_INTERVAL_TOO_SHORT')
        self.interval=float(interval);self.deadline=float(deadline);self.clock=clock
        self._wait=wait or self._wait_or_stop
        self._lock=asyncio.Lock();self._stop=asyncio.Event();self.next_start=float(prior_next_start)
        self._persist_next_start=persist_next_start or (lambda value:None)

    def stop(self):
        self._stop.set()

    def note_completion(self):
        """Conservatively extend pacing when any in-flight request completes.

        Must be called on this coordinator's event loop before yielding control
        after observing a response. This does not prove production parity while
        requests overlap; it prevents a completed response from shortening the
        next dispatch's delay. A persistence error closes the gate.
        """
        if self._stop.is_set():return
        try:
            self.next_start=max(self.next_start,self.clock()+self.interval)
            self._persist_next_start(self.next_start)
        except BaseException:
            self.stop()
            raise

    async def _wait_or_stop(self, delay, stop):
        try:
            await asyncio.wait_for(stop.wait(),timeout=delay)
        except asyncio.TimeoutError:
            return

    async def dispatch(self, *, verify_reservation, send):
        async with self._lock:
            if self._stop.is_set():raise DispatchStopped('GLOBAL_STOP')
            now=self.clock()
            if now>=self.deadline:raise DispatchStopped('ORIGINAL_DEADLINE')
            while True:
                delay=max(0.0,self.next_start-self.clock())
                if not delay:break
                await self._wait(delay,self._stop)
                if self._stop.is_set():raise DispatchStopped('GLOBAL_STOP')
                if self.clock()>=self.deadline:raise DispatchStopped('ORIGINAL_DEADLINE')
            if self.clock()>=self.deadline:raise DispatchStopped('ORIGINAL_DEADLINE')
            try:
                valid=verify_reservation()
            except BaseException:
                self.stop();raise
            if not valid:
                self.stop();raise DispatchStopped('INVALID_RESERVATION_OR_GATE')
            # The actual network-adapter contract must define when this returns.
            # On uncertainty, callers must hold the reservation, never retry.
            try:
                # Durable before send: a crash must not silently clear pacing.
                self.next_start=max(self.next_start,self.clock()+self.interval)
                self._persist_next_start(self.next_start)
                value=await send()
                # Spacing begins no earlier than completion of dispatch setup.
                self.next_start=max(self.next_start,self.clock()+self.interval)
                self._persist_next_start(self.next_start)
            except BaseException:
                self.stop()
                raise
            return value
