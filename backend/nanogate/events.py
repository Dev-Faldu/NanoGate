"""In-process event bus. Events are emitted only by real backend state changes and fanned out
to SSE subscribers (/api/events). A bounded ring buffer lets new subscribers replay recent events.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history: int = 500):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[asyncio.Queue] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)
        self._seq = itertools.count(1)
        self.counts: dict[str, int] = {}

    def emit(self, type_: str, request_id: str | None = None, **data: Any) -> dict[str, Any]:
        ev = {"seq": next(self._seq), "type": type_, "ts": time.time(), "request_id": request_id, "data": data}
        self._history.append(ev)
        self.counts[type_] = self.counts.get(type_, 0) + 1
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        for q in list(self._subs):
            if on_loop:
                self._put(q, ev)
            elif self._loop is not None:
                self._loop.call_soon_threadsafe(self._put, q, ev)   # emitted from a worker thread
        return ev

    @staticmethod
    def _put(q: asyncio.Queue, ev: dict) -> None:
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            pass  # slow consumer: drop (it can re-sync via REST)

    def subscribe(self) -> asyncio.Queue:
        self._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def recent(self, since_seq: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return [e for e in self._history if e["seq"] > since_seq][-limit:]

    @property
    def subscribers(self) -> int:
        return len(self._subs)


bus = EventBus()
