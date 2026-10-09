"""Event history (bounded ring buffer) and fan-out to bounded per-client queues.

``publish`` is synchronous and never blocks: a subscriber whose queue is full is marked
``dropped`` (the WebSocket endpoint then closes it with ``CLOSE_SLOW``); the engine is never
delayed by a slow reader.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

CLOSE_SLOW = 4429  # private-use close code: client too slow, reconnect for a fresh snapshot
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_FULL = 4503


class Subscriber:
    def __init__(self, size: int) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=size)
        self.dropped = False

    def offer(self, event: dict[str, Any]) -> bool:
        if self.dropped:
            return False
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self.dropped = True
            while not self.queue.empty():  # free memory now; the writer notices ``dropped``
                self.queue.get_nowait()
            return False


class EventLog:
    def __init__(self, history_size: int, client_queue: int, max_clients: int) -> None:
        self.history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._client_queue = client_queue
        self._max_clients = max_clients
        self.subscribers: set[Subscriber] = set()
        self.dropped_total = 0

    def reset(self) -> None:
        self.history.clear()

    def append(self, event: dict[str, Any], broadcast: bool = True) -> None:
        self.history.append(event)
        if not broadcast:
            return
        for s in tuple(self.subscribers):
            if not s.offer(event):
                if s.dropped:
                    self.dropped_total += 1
                    self.subscribers.discard(s)

    def broadcast(self, event: dict[str, Any]) -> None:
        """Deliver without recording (snapshots)."""
        for s in tuple(self.subscribers):
            if not s.offer(event):
                self.dropped_total += 1
                self.subscribers.discard(s)

    def subscribe(self, snapshot: dict[str, Any]) -> Subscriber | None:
        """Atomically (no await) register a subscriber with ``snapshot`` as its first message."""
        if len(self.subscribers) >= self._max_clients:
            return None
        s = Subscriber(self._client_queue)
        s.offer(snapshot)
        self.subscribers.add(s)
        return s

    def unsubscribe(self, s: Subscriber) -> None:
        self.subscribers.discard(s)

    def last(self, n: int) -> list[dict[str, Any]]:
        if n <= 0:
            return []
        items = list(self.history)
        return items[-n:]
