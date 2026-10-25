from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.engine import WorkflowEngine
from app.models import NodeRunRecord, RunRecord
from app.queue_manager import QueueTask, RunQueueManager
from app.schemas import RunRequest, WorkflowDefinition
from app.service import upsert_workflow, verify_run_audit


def test_queue_counts_real_failures_and_survives_worker_errors(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("app.queue_manager.SessionLocal", sessions)
    workflow = WorkflowDefinition(id="queue", name="queue", nodes=[], edges=[], entry_nodes=[])
    with sessions() as db:
        upsert_workflow(db, workflow)
        db.add_all([RunRecord(id=name, workflow_id=workflow.id, status="pending") for name in ("raises", "fails", "works")])
        db.commit()

    class Engine:
        async def run(self, db, workflow, request, run_id):
            if run_id == "raises":
                raise ValueError("bad workflow")
            run = db.get(RunRecord, run_id)
            run.status = "failed" if run_id == "fails" else "succeeded"
            db.commit()

    async def check():
        queue = RunQueueManager(worker_count=1, max_size=4)
        queue.start(Engine())
        for name in ("raises", "fails", "works"):
            queue.enqueue_nowait(QueueTask(name, workflow, RunRequest(), datetime.now(timezone.utc)))
        await asyncio.wait_for(queue.queue.join(), timeout=2)
        assert queue.snapshot()["failed"] == 2
        assert queue.snapshot()["completed"] == 1
        assert queue.snapshot()["active"] == 0
        await queue.stop()

    asyncio.run(check())
    with sessions() as db:
        assert db.get(RunRecord, "raises").status == "failed"
        assert json.loads(db.get(RunRecord, "raises").error_json)["code"] == "WORKER_FAILED"
        assert verify_run_audit(db, "raises").verified
    engine.dispose()


def test_queue_shutdown_finishes_active_and_pending_records(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("app.queue_manager.SessionLocal", sessions)
    workflow = WorkflowDefinition(id="shutdown", name="shutdown", nodes=[], edges=[], entry_nodes=[])
    with sessions() as db:
        upsert_workflow(db, workflow)
        db.add_all([RunRecord(id=name, workflow_id=workflow.id, status="pending") for name in ("active", "pending")])
        db.commit()

    async def check():
        started = asyncio.Event()

        class Engine:
            async def run(self, db, workflow, request, run_id):
                db.get(RunRecord, run_id).status = "running"
                db.add(NodeRunRecord(run_id=run_id, node_id="a", status="running", started_at=datetime.now(timezone.utc)))
                db.commit()
                started.set()
                await asyncio.Event().wait()

        queue = RunQueueManager(worker_count=1, max_size=2)
        queue.start(Engine())
        for name in ("active", "pending"):
            queue.enqueue_nowait(QueueTask(name, workflow, RunRequest(), datetime.now(timezone.utc)))
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.wait_for(queue.stop(), timeout=2)
        await asyncio.wait_for(queue.queue.join(), timeout=2)
        assert queue.snapshot()["queued"] == queue.snapshot()["active"] == 0
        assert queue.snapshot()["failed"] == 2

    asyncio.run(check())
    with sessions() as db:
        for name in ("active", "pending"):
            row = db.get(RunRecord, name)
            assert row.status == "failed" and row.ended_at
            assert json.loads(row.error_json)["code"] == "RUNTIME_SHUTDOWN"
            assert verify_run_audit(db, name).verified
        assert db.query(NodeRunRecord).one().status == "failed"
    engine.dispose()


def test_error_persistence_failure_does_not_remove_worker(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("app.queue_manager.SessionLocal", sessions)
    workflow = WorkflowDefinition(id="resilience", name="resilience", nodes=[], edges=[], entry_nodes=[])
    with sessions() as db:
        upsert_workflow(db, workflow)
        db.add_all([RunRecord(id=name, workflow_id=workflow.id, status="pending") for name in ("bad", "good")])
        db.commit()

    class Engine:
        async def run(self, db, workflow, request, run_id):
            if run_id == "bad":
                raise ValueError("execution failed")
            db.get(RunRecord, run_id).status = "succeeded"
            db.commit()

    def cannot_persist(*_args):
        raise RuntimeError("database unavailable")

    async def check():
        queue = RunQueueManager(worker_count=1, max_size=2)
        monkeypatch.setattr(queue, "_fail_task", cannot_persist)
        queue.start(Engine())
        for name in ("bad", "good"):
            queue.enqueue_nowait(QueueTask(name, workflow, RunRequest(), datetime.now(timezone.utc)))
        await asyncio.wait_for(queue.queue.join(), timeout=2)
        assert queue.snapshot()["failed"] == queue.snapshot()["completed"] == 1
        assert not queue._workers[0].done()
        await queue.stop()

    asyncio.run(check())
    engine.dispose()


@pytest.mark.parametrize("tool, expected", [("echo", "succeeded"), ("not_allowed", "failed")])
def test_queue_runs_real_engine_and_persists_auditable_results(monkeypatch, tool, expected) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("app.queue_manager.SessionLocal", sessions)
    workflow = WorkflowDefinition.model_validate({
        "id": "real_queue", "name": "real queue",
        "nodes": [{"id": "tool", "type": "tool", "position": {"x": 0, "y": 0},
                   "config": {"tool_name": tool}, "inputs": {"message": "hello"}}],
        "edges": [], "entry_nodes": ["tool"],
    })
    with sessions() as db:
        upsert_workflow(db, workflow)
        db.add(RunRecord(id="real_run", workflow_id=workflow.id, status="pending"))
        db.commit()

    class NoModelCalls:
        async def chat(self, **_kwargs):
            pytest.fail("tool execution must not call a model")

    async def check():
        queue = RunQueueManager(worker_count=1, max_size=2)
        queue.start(WorkflowEngine(provider=NoModelCalls()))
        queue.enqueue_nowait(QueueTask("real_run", workflow, RunRequest(), datetime.now(timezone.utc)))
        await asyncio.wait_for(queue.queue.join(), timeout=3)
        assert queue.snapshot()["failed"] == int(expected == "failed")
        assert queue.snapshot()["completed"] == int(expected == "succeeded")
        await queue.stop()

    asyncio.run(check())
    with sessions() as db:
        assert db.get(RunRecord, "real_run").status == expected
        assert db.query(NodeRunRecord).one().status == expected
        assert verify_run_audit(db, "real_run").verified
    engine.dispose()
