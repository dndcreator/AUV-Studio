from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution.events import emit_event
from app.models import NodeRunRecord, RunEventRecord, RunRecord
from app.run_lifecycle import fail_interrupted_run, recover_interrupted_runs
from app.runtime_lock import runtime_lease
from app.schemas import WorkflowDefinition
from app.service import upsert_workflow, verify_run_audit


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        upsert_workflow(db, WorkflowDefinition(id="recovery", name="recovery", nodes=[], edges=[], entry_nodes=[]))
    yield engine, sessions
    engine.dispose()


def test_runtime_lease_excludes_second_owner_and_releases_after_exception(database):
    engine, _ = database
    with pytest.raises(ValueError):
        with runtime_lease(engine):
            with pytest.raises(RuntimeError, match="already owns"):
                with runtime_lease(engine):
                    pytest.fail("second owner must not enter")
            raise ValueError("startup failed")
    with runtime_lease(engine):
        pass


def test_os_releases_runtime_lease_after_unclean_exit(database):
    engine, _ = database
    code = (
        "import os,sys; from sqlalchemy import create_engine; from app.runtime_lock import runtime_lease; "
        "e=create_engine(sys.argv[1]); lease=runtime_lease(e); lease.__enter__(); os._exit(0)"
    )
    result = subprocess.run([sys.executable, "-c", code, str(engine.url)], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with runtime_lease(engine):
        pass


def test_recovery_keeps_outputs_checkpoints_and_terminal_runs(database):
    engine, sessions = database
    statuses = ["pending", "queued", "running", "stopping", "waiting_human", "succeeded", "failed", "stopped"]
    with sessions() as db:
        for status in statuses:
            db.add(RunRecord(id=status, workflow_id="recovery", status=status, input_json='{"task":"keep"}',
                             output_json='{"dynamic_state":{"kept":true}}'))
        db.add_all([
            NodeRunRecord(run_id="running", node_id="done", status="succeeded", output_json='{"text":"keep me"}'),
            NodeRunRecord(run_id="running", node_id="active", status="running", started_at=datetime.now(timezone.utc)),
        ])
        db.commit()
        emit_event(db, "running", "done", "succeeded", {"output": {"text": "keep me"}, "_rollback_snapshot": {"kept": True}})
        with runtime_lease(engine):
            assert recover_interrupted_runs(db) == 4
            assert recover_interrupted_runs(db) == 0
        for status in statuses:
            row = db.get(RunRecord, status)
            assert row.input_json == '{"task":"keep"}'
            assert json.loads(row.output_json)["dynamic_state"]["kept"]
            if status in statuses[:4]:
                assert row.status == "failed" and row.ended_at
                assert json.loads(row.error_json)["code"] == "RUNTIME_INTERRUPTED"
                assert verify_run_audit(db, status).verified
            else:
                assert row.status == status
                assert db.query(RunEventRecord).filter_by(run_id=status).count() == 0
        nodes = {row.node_id: row for row in db.query(NodeRunRecord).all()}
        assert nodes["done"].status == "succeeded" and "keep me" in nodes["done"].output_json
        assert nodes["active"].status == "failed" and nodes["active"].duration_ms >= 0
        first_event = db.query(RunEventRecord).filter_by(run_id="running").order_by(RunEventRecord.seq).first()
        assert json.loads(first_event.payload_json)["_rollback_snapshot"] == {"kept": True}


def test_failure_status_and_audit_event_commit_together(database, monkeypatch):
    _, sessions = database
    with sessions() as db:
        db.add(RunRecord(id="atomic", workflow_id="recovery", status="running"))
        db.commit()

        def broken_event(*_args, **_kwargs):
            raise RuntimeError("cannot write audit")

        monkeypatch.setattr("app.run_lifecycle.emit_event", broken_event)
        with pytest.raises(RuntimeError, match="cannot write audit"):
            fail_interrupted_run(db, "atomic", "WORKER_FAILED", "test")
        assert db.get(RunRecord, "atomic").status == "running"
        assert db.query(RunEventRecord).count() == 0


def test_lifespan_recovers_before_starting_workers(database, monkeypatch):
    from app import main

    engine, sessions = database
    with sessions() as db:
        db.add(RunRecord(id="startup", workflow_id="recovery", status="running"))
        db.commit()

    class Queue:
        starts = 0
        stops = 0

        def start(self, _engine):
            with sessions() as db:
                assert db.get(RunRecord, "startup").status == "failed"
                assert verify_run_audit(db, "startup").verified
            self.starts += 1

        async def stop(self):
            self.stops += 1

    queue = Queue()
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main, "run_queue_manager", queue)
    monkeypatch.setattr(main, "apply_stored_model_config", lambda *_args: None)
    for _ in range(2):
        with TestClient(main.app) as client:
            assert client.get("/api/health").status_code == 200
    assert queue.starts == queue.stops == 2
    with sessions() as db:
        assert db.query(RunEventRecord).filter_by(run_id="startup").count() == 1
