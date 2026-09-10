from __future__ import annotations

"""In-process event bus used to fan out live pipeline events to SSE subscribers."""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set


@dataclass
class Event:
    type: str
    data: Dict[str, Any]
    ts: float = field(default_factory=time.time)


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subs: Set[asyncio.Queue] = set()
        self._history: Deque[Event] = deque(maxlen=history)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, type: str, data: Dict[str, Any]) -> Event:
        ev = Event(type=type, data=data)
        self._history.append(ev)
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                self._subs.discard(q)
        return ev

    def publish_threadsafe(self, type: str, data: Dict[str, Any]) -> None:
        if self._loop is None:
            self.publish(type, data)
        else:
            self._loop.call_soon_threadsafe(self.publish, type, data)

    def recent(self, types: Optional[List[str]] = None, limit: int = 50) -> List[Event]:
        evs = [e for e in self._history if not types or e.type in types]
        return evs[-limit:]


bus = EventBus()
