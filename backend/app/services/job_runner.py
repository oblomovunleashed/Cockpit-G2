from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


class BackgroundJobRunner:
    """Lightweight asyncio queue that recomputes holding cards when saved questions change."""

    def __init__(self, handler: Callable[[str], Awaitable[None]]) -> None:
        self._handler = handler
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._loop(), name="cockpit-job-runner")

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    def enqueue(self, ticker: str) -> None:
        self._queue.put_nowait(ticker.upper())

    async def _loop(self) -> None:
        while True:
            ticker = await self._queue.get()
            try:
                await self._handler(ticker)
            except Exception as exc:
                logger.exception("Background job failed for %s: %s", ticker, exc)
            finally:
                self._queue.task_done()
