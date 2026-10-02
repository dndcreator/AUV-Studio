from __future__ import annotations

import asyncio
from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app, get_db, workflow_engine


class CapturingQueueManager:
    def __init__(self) -> None:
        self.tasks: list[object] = []

    def start(self, _engine) -> None:  # type: ignore[no-untyped-def]
        return

    async def stop(self) -> None:
        return

    def enqueue_nowait(self, task) -> None:  # type: ignore[no-untyped-def]
        self.tasks.append(task)

    def snapshot(self) -> dict:
        return {
            "queued": len(self.tasks),
            "active": 0,
            "completed": 0,
            "failed": 0,
            "avg_wait_ms": 0,
            "worker_count": 0,
            "active_run_ids": [],
        }


def _make_workflow(workflow_id: str, condition_expr: str = "True", with_human_checkpoint: bool = False) -> dict:
    if with_human_checkpoint:
        return {
            "id": workflow_id,
            "name": "Human Flow",
            "version": 1,
            "nodes": [
                {
                    "id": "p1",
                    "type": "prompt",
                    "position": {"x": 0, "y": 0},
                    "config": {"template": "draft: {{input.task}}"},
                    "inputs": {},
                },
                {
                    "id": "hc1",
                    "type": "human_checkpoint",
                    "position": {"x": 200, "y": 0},
                    "config": {
                        "owner": "director",
                        "question_template": "Please decide next step for {{input.task}}",
                        "required": "true",
                    },
                    "inputs": {"context_hint": "{{p1.text}}"},
                },
                {
                    "id": "p2",
                    "type": "prompt",
                    "position": {"x": 400, "y": 0},
                    "config": {"template": "done after human"},
                    "inputs": {},
                },
            ],
            "edges": [
                {"id": "e1", "source": "p1", "target": "hc1", "condition": None, "interaction": None},
                {"id": "e2", "source": "hc1", "target": "p2", "condition": None, "interaction": None},
            ],
            "entry_nodes": ["p1"],
            "environment": {"profile": "", "scenario": "", "facts": [], "constraints": [], "glossary": {}},
        }
    return {
        "id": workflow_id,
        "name": "Base Flow",
        "version": 1,
        "nodes": [
            {
                "id": "a",
                "type": "prompt",
                "position": {"x": 0, "y": 0},
                "config": {"template": "hello {{input.task}}"},
                "inputs": {},
            },
            {
                "id": "b",
                "type": "condition",
                "position": {"x": 200, "y": 0},
                "config": {"expression": condition_expr},
                "inputs": {},
            },
            {
                "id": "c",
                "type": "prompt",
                "position": {"x": 400, "y": 0},
                "config": {"template": "tail"},
                "inputs": {},
            },
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "condition": None, "interaction": None},
            {"id": "e2", "source": "b", "target": "c", "condition": "true", "interaction": None},
        ],
        "entry_nodes": ["a"],
        "environment": {"profile": "", "scenario": "", "facts": [], "constraints": [], "glossary": {}},
    }


def _drain_one_task(queue: CapturingQueueManager, db_local: sessionmaker[Session]) -> str:
    task = queue.tasks.pop(0)
    db = db_local()
    try:
        run_id = asyncio.run(
            workflow_engine.run(
                db=db,
                workflow=getattr(task, "workflow"),
                request=getattr(task, "request"),
                run_id=getattr(task, "run_id"),
            )
        )
        return run_id
    finally:
        db.close()


def _client_and_db(monkeypatch) -> Generator[tuple[TestClient, sessionmaker[Session], CapturingQueueManager], None, None]:  # type: ignore[no-untyped-def]
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    queue = CapturingQueueManager()
    monkeypatch.setattr("app.main.run_queue_manager", queue)
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client, TestingSessionLocal, queue
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_e2e_run_events_metrics_report_compare(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    gen = _client_and_db(monkeypatch)
    client, db_local, queue = next(gen)
    try:
        wf = _make_workflow("wf_e2e_1")
        r = client.post("/api/workflows", json=wf)
        assert r.status_code == 200

        run_start = client.post("/api/workflows/wf_e2e_1/run", json={"input": {"task": "alpha"}})
        assert run_start.status_code == 200
        run_id = run_start.json()["run_id"]
        assert run_start.json()["status"] == "queued"
        assert len(queue.tasks) == 1

        done_run_id = _drain_one_task(queue, db_local)
        assert done_run_id == run_id

        detail = client.get(f"/api/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "succeeded"

        events = client.get(f"/api/runs/{run_id}/events?after_seq=0&limit=500")
        assert events.status_code == 200
        assert len(events.json()["events"]) > 0
        first_seq = events.json()["events"][0]["seq"]

        full_log = client.get(f"/api/runs/{run_id}/full-log?format=markdown")
        assert full_log.status_code == 200
        assert full_log.json()["event_count"] == len(events.json()["events"])
        assert "Full Simulation Log" in full_log.json()["content"]
        assert f"Event #{first_seq}" in full_log.json()["content"]

        full_log_json = client.get(f"/api/runs/{run_id}/full-log?format=json")
        assert full_log_json.status_code == 200
        assert full_log_json.json()["format"] == "json"
        assert '"events"' in full_log_json.json()["content"]

        audit = client.get(f"/api/runs/{run_id}/audit/verify")
        assert audit.status_code == 200
        assert audit.json()["verified"] is True

        trace = client.get(f"/api/runs/{run_id}/trace/{first_seq}")
        assert trace.status_code == 200
        assert trace.json()["seq"] == first_seq
        assert "event" in trace.json()
        assert "chain_ok" in trace.json()

        checkpoints = client.get(f"/api/runs/{run_id}/checkpoints")
        assert checkpoints.status_code == 200
        checkpoint_rows = checkpoints.json()["checkpoints"]
        assert checkpoint_rows
        rollback_target = next(row for row in checkpoint_rows if row["node_id"] == "a" and row["event"] == "succeeded")

        rollback = client.post(
            f"/api/runs/{run_id}/rollback",
            json={"event_seq": rollback_target["seq"], "reason": "e2e restart from known-good prompt"},
        )
        assert rollback.status_code == 200
        rollback_run_id = rollback.json()["run_id"]
        assert rollback.json()["source_run_id"] == run_id
        assert rollback.json()["retry_from_node"] == "a"
        _drain_one_task(queue, db_local)

        rollback_detail = client.get(f"/api/runs/{rollback_run_id}")
        assert rollback_detail.status_code == 200
        assert rollback_detail.json()["status"] == "succeeded"
        assert rollback_detail.json()["retry_from_run_id"] == run_id
        assert rollback_detail.json()["retry_from_node"] == "a"
        assert rollback_detail.json()["input"]["_rollback"]["event_seq"] == rollback_target["seq"]

        metrics = client.get(f"/api/runs/{run_id}/metrics")
        assert metrics.status_code == 200
        assert metrics.json()["total_nodes"] >= 2
        assert metrics.json()["total_events"] > 0

        memories = client.get("/api/workflows/wf_e2e_1/memories?limit=50")
        assert memories.status_code == 200
        assert len(memories.json()) >= 1

        report = client.post(
            f"/api/runs/{run_id}/report",
            json={"mode": "briefing", "length": "short", "style_prompt": "formal"},
        )
        assert report.status_code == 200
        assert len(report.json()["report_markdown"]) > 20

        narrative = client.post(
            f"/api/runs/{run_id}/report",
            json={"mode": "narrative", "length": "short", "style_prompt": "screenplay style"},
        )
        assert narrative.status_code == 200
        assert narrative.json()["mode"] == "narrative"
        assert len(narrative.json()["report_markdown"]) > 20

        run_start_b = client.post("/api/workflows/wf_e2e_1/run", json={"input": {"task": "beta"}})
        assert run_start_b.status_code == 200
        run_id_b = run_start_b.json()["run_id"]
        _drain_one_task(queue, db_local)

        cmp_res = client.get(f"/api/compare-runs?run_a={run_id}&run_b={run_id_b}")
        assert cmp_res.status_code == 200
        assert cmp_res.json()["run_a"] == run_id
        assert cmp_res.json()["run_b"] == run_id_b
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_e2e_human_checkpoint_pause_and_resume(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    gen = _client_and_db(monkeypatch)
    client, db_local, queue = next(gen)
    try:
        wf = _make_workflow("wf_e2e_human", with_human_checkpoint=True)
        assert client.post("/api/workflows", json=wf).status_code == 200

        run_start = client.post("/api/workflows/wf_e2e_human/run", json={"input": {"task": "market research"}})
        assert run_start.status_code == 200
        run_id = run_start.json()["run_id"]
        _drain_one_task(queue, db_local)

        detail_wait = client.get(f"/api/runs/{run_id}")
        assert detail_wait.status_code == 200
        assert detail_wait.json()["status"] == "waiting_human"

        events_wait = client.get(f"/api/runs/{run_id}/events?after_seq=0&limit=500").json()["events"]
        assert any(e["event"] == "waiting_human" and e["node_id"] == "hc1" for e in events_wait)

        resume = client.post(
            f"/api/runs/{run_id}/human-response",
            json={
                "node_id": "hc1",
                "response": "Approve and continue",
                "responder": "operator",
                "metadata": {"source": "e2e"},
            },
        )
        assert resume.status_code == 200
        assert resume.json()["status"] == "queued"
        assert len(queue.tasks) == 1

        _drain_one_task(queue, db_local)

        detail_ok = client.get(f"/api/runs/{run_id}")
        assert detail_ok.status_code == 200
        assert detail_ok.json()["status"] == "succeeded"

        events_done = client.get(f"/api/runs/{run_id}/events?after_seq=0&limit=500").json()["events"]
        assert any(e["event"] == "human_resumed" and e["node_id"] == "hc1" for e in events_done)
        assert any(e["event"] == "succeeded" and e["node_id"] == "hc1" for e in events_done)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_e2e_failed_then_retry_from_node(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    gen = _client_and_db(monkeypatch)
    client, db_local, queue = next(gen)
    try:
        wf_bad = _make_workflow("wf_e2e_retry", condition_expr="1/0")
        assert client.post("/api/workflows", json=wf_bad).status_code == 200

        first = client.post("/api/workflows/wf_e2e_retry/run", json={"input": {"task": "retry-case"}})
        assert first.status_code == 200
        run_1 = first.json()["run_id"]
        _drain_one_task(queue, db_local)

        detail_1 = client.get(f"/api/runs/{run_1}")
        assert detail_1.status_code == 200
        assert detail_1.json()["status"] == "failed"

        wf_good = _make_workflow("wf_e2e_retry", condition_expr="True")
        assert client.put("/api/workflows/wf_e2e_retry", json=wf_good).status_code == 200

        retry = client.post(f"/api/runs/{run_1}/retry", json={"node_id": "b"})
        assert retry.status_code == 200
        retry_run_id = retry.json()["run_id"]
        _drain_one_task(queue, db_local)

        detail_2 = client.get(f"/api/runs/{retry_run_id}")
        assert detail_2.status_code == 200
        assert detail_2.json()["status"] == "succeeded"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_e2e_platform_catalog_and_health_endpoints(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    gen = _client_and_db(monkeypatch)
    client, _, _ = next(gen)
    try:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        specs = client.get("/api/node-specs")
        assert specs.status_code == 200
        node_types = {row["type"] for row in specs.json()}
        assert "human_checkpoint" in node_types
        assert "external_agent" in node_types
        assert "director" in node_types

        protocol = client.get("/api/protocols/text-agent-v1")
        assert protocol.status_code == 200
        body = protocol.json()
        assert body["version"] == "1.0"
        assert "task_submit" in body

        queue = client.get("/api/queue/status")
        assert queue.status_code == 200
        assert "queued" in queue.json()

        obs = client.get("/api/observability")
        assert obs.status_code == 200
        assert "http" in obs.json()
        assert "runtime" in obs.json()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
