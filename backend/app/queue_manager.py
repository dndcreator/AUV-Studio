from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .db import SessionLocal
from .execution.engine import WorkflowEngine
from .models import RunRecord
from .run_lifecycle import fail_interrupted_run
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
        self._started = 0
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
        while not self.queue.empty():
            task = self.queue.get_nowait()
            try:
                with SessionLocal() as db:
                    self._record_failure(db, task.run_id, "RUNTIME_SHUTDOWN", "Service stopped before this run started.")
                self._failed += 1
            finally:
                self.queue.task_done()

    async def enqueue(self, task: QueueTask) -> None:
        await self.queue.put(task)

    def enqueue_nowait(self, task: QueueTask) -> None:
        self.queue.put_nowait(task)

    def snapshot(self) -> dict:
        avg_wait_ms = int(self._total_wait_ms / self._started) if self._started > 0 else 0
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
            self._started += 1
            db = None
            try:
                if self._engine is None:
                    raise RuntimeError("queue manager engine not initialized")
                db = SessionLocal()
                await self._engine.run(db=db, workflow=task.workflow, request=task.request, run_id=task.run_id)
                db.expire_all()
                run = db.get(RunRecord, task.run_id)
                if run is None:
                    raise RuntimeError("execution returned without a persisted run")
                if run.status == "failed":
                    self._failed += 1
                else:
                    self._completed += 1
            except asyncio.CancelledError:
                if db is not None:
                    self._record_failure(db, task.run_id, "RUNTIME_SHUTDOWN", "Service stopped while this run was active.")
                self._failed += 1
                raise
            except Exception as exc:  # noqa: BLE001
                self._failed += 1
                logging.getLogger(__name__).exception("Run worker failed for %s", task.run_id)
                if db is not None:
                    self._record_failure(db, task.run_id, "WORKER_FAILED", str(exc))
            finally:
                if db is not None:
                    db.close()
                self._active_run_ids.discard(task.run_id)
                self.queue.task_done()

    def _record_failure(self, db: Session, run_id: str, code: str, message: str) -> None:
        try:
            self._fail_task(db, run_id, code, message)
        except Exception:  # A failed error write must not permanently remove a worker.
            logging.getLogger(__name__).exception("Could not persist queue failure for %s", run_id)

    _fail_task = staticmethod(fail_interrupted_run)
