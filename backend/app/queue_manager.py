from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import SessionLocal
from .execution.engine import WorkflowEngine
from .schemas import RunRequest, WorkflowDefinition


@dataclass
class QueueTask:
    run_id: str
    workflow: WorkflowDefinition
    request: RunRequest
    enqueued_at: datetime


class RunQueueManager:
    def __init__(self, worker_count: int, max_size: int) -> None:
        self.worker_count = max(1, worker_count)
        self.queue: asyncio.Queue[QueueTask] = asyncio.Queue(maxsize=max(1, max_size))
        self._workers: list[asyncio.Task[None]] = []
        self._engine: WorkflowEngine | None = None
        self._active_run_ids: set[str] = set()
        self._completed = 0
        self._failed = 0
        self._total_wait_ms = 0
        self._stop = False

    def start(self, engine: WorkflowEngine) -> None:
        if self._workers:
            return
        self._engine = engine
        self._stop = False
        for i in range(self.worker_count):
            self._workers.append(asyncio.create_task(self._worker_loop(i)))

    async def stop(self) -> None:
        self._stop = True
        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def enqueue(self, task: QueueTask) -> None:
        await self.queue.put(task)

    def enqueue_nowait(self, task: QueueTask) -> None:
        self.queue.put_nowait(task)

    def snapshot(self) -> dict:
        avg_wait_ms = int(self._total_wait_ms / self._completed) if self._completed > 0 else 0
        return {
            "queued": self.queue.qsize(),
            "active": len(self._active_run_ids),
            "completed": self._completed,
            "failed": self._failed,
            "avg_wait_ms": avg_wait_ms,
            "worker_count": self.worker_count,
            "active_run_ids": sorted(self._active_run_ids),
        }

    async def _worker_loop(self, _worker_index: int) -> None:
        while not self._stop:
            task = await self.queue.get()
            self._active_run_ids.add(task.run_id)
            wait_ms = int((datetime.now(timezone.utc) - task.enqueued_at).total_seconds() * 1000)
            self._total_wait_ms += max(0, wait_ms)
            try:
                if self._engine is None:
                    raise RuntimeError("queue manager engine not initialized")
                db = SessionLocal()
                try:
                    await self._engine.run(
                        db=db,
                        workflow=task.workflow,
                        request=task.request,
                        run_id=task.run_id,
                    )
                finally:
                    db.close()
                self._completed += 1
            except Exception:  # noqa: BLE001
                self._failed += 1
            finally:
                self._active_run_ids.discard(task.run_id)
                self.queue.task_done()
