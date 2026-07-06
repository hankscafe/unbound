"""In-process pub/sub for live UI updates (job progress, status changes).

Subscribers (SSE connections) receive dicts. This is intentionally simple and
single-instance; a Redis pub/sub backend can replace it for multi-process setups.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

_subscribers: set["queue.Queue[str]"] = set()
_lock = threading.Lock()


def publish(event: dict[str, Any]) -> None:
    payload = json.dumps(event, default=str)
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def subscribe() -> "queue.Queue[str]":
    q: "queue.Queue[str]" = queue.Queue(maxsize=100)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: "queue.Queue[str]") -> None:
    with _lock:
        _subscribers.discard(q)
