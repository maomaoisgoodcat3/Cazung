"""Sequential background job runner (memory extraction, compaction, reflection...).

Jobs wait while the agent is answering so they never slow down a live reply."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("cazung.worker")


class Worker:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.is_busy = lambda: False
        self.current: str | None = None

    def submit(self, name: str, fn, *args):
        self.queue.put_nowait((name, fn, args))

    @property
    def pending(self) -> int:
        return self.queue.qsize()

    async def run(self):
        while True:
            name, fn, args = await self.queue.get()
            while self.is_busy():
                await asyncio.sleep(0.5)
            self.current = name
            try:
                await fn(*args)
            except Exception:  # noqa: BLE001
                log.exception("background job %s failed", name)
            finally:
                self.current = None
                self.queue.task_done()
