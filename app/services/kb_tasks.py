"""Run blocking KB work off the async event loop."""

from __future__ import annotations

import atexit
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-worker")


def submit_kb_job(fn: Callable[..., T], /, *args, **kwargs):
    """Queue CPU/IO-heavy KB work on a dedicated background thread."""
    return _executor.submit(fn, *args, **kwargs)


def _shutdown_executor() -> None:
    logger.debug("Shutting down KB worker thread pool")
    _executor.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_executor)
