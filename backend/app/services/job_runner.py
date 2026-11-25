from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AsyncJobQueue(Generic[T]):
    """Lightweight asyncio background worker queue with clean lifecycle shutdown."""

    def __init__(self, handler: Callable[[T], Awaitable[None]], *, name: str) -> None:
        self._handler = handler
        self._name = name
        self._queue: asyncio.Queue[T] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._in_flight = 0

    @property
    def pending_count(self) -> int:
        return self._queue.qsize() + self._in_flight

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._loop(), name=self._name)

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    def enqueue(self, item: T) -> None:
        self._queue.put_nowait(item)

    async def drain(self) -> None:
        await self._queue.join()

    async def _loop(self) -> None:
        while True:
            item = await self._queue.get()
            self._in_flight += 1
            try:
                await self._handler(item)
            except Exception as exc:
                logger.exception("Background job %s failed for %r: %s", self._name, item, exc)
            finally:
                self._in_flight -= 1
                self._queue.task_done()


class BackgroundJobRunner(AsyncJobQueue[str]):
    """Lightweight asyncio queue that recomputes holding cards when saved questions change."""

    def __init__(self, handler: Callable[[str], Awaitable[None]]) -> None:
        super().__init__(handler, name="cockpit-job-runner")

    def enqueue(self, ticker: str) -> None:
        super().enqueue(ticker.upper())
