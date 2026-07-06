"""Task queue abstraction with a default in-process backend.

For a single-instance self-hosted deployment the default backend runs tasks on a
bounded thread pool inside the app process — zero external dependencies. Download
job *state* is persisted in the ``download_jobs`` table, so incomplete jobs are
re-enqueued on startup and survive restarts. A Redis/RQ backend can be slotted in
behind the same :func:`enqueue` interface for multi-process deployments.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger("worker.queue")
settings = get_settings()

# Registry of task-name -> callable(**kwargs)
_REGISTRY: dict[str, Callable[..., Any]] = {}
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


def task(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY[name] = fn
        return fn

    return deco


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            # +1 headroom for sync tasks alongside download slots.
            workers = max(2, settings.max_concurrent_downloads + 1)
            _executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="unbound-wk")
    return _executor


def _run(name: str, kwargs: dict[str, Any]) -> None:
    fn = _REGISTRY.get(name)
    if fn is None:
        log.error("unknown_task", task=name)
        return
    try:
        fn(**kwargs)
    except Exception as exc:  # pragma: no cover
        log.error("task_failed", task=name, error=str(exc))


def enqueue(name: str, **kwargs: Any) -> None:
    """Schedule a registered task to run on the worker pool."""
    log.info("task_enqueued", task=name, **{k: v for k, v in kwargs.items() if k.endswith("_id")})
    _get_executor().submit(_run, name, kwargs)


def start_worker() -> None:
    """Initialize the pool, register tasks, and requeue interrupted download jobs."""
    from app.worker import tasks  # noqa: F401  (registers @task functions)

    _get_executor()
    tasks.requeue_incomplete_jobs()
    log.info("worker_started", backend=settings.queue_backend)


def shutdown_worker() -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None
