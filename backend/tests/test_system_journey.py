from __future__ import annotations

import asyncio
import json
import threading
import time

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.execution.engine import WorkflowEngine
from app.queue_manager import RunQueueManager


class ScenarioProvider:
    default_model = "offline-system"
    api_key = ""
    base_url = "http://127.0.0.1:1/v1"
    provider_kind = "openai_compatible"
    planning_output_tokens = 2400

    def __init__(self):
        self.actors = []
        self.started = threading.Event()
        self.allow_completion = True

    async def chat(self, model, system_prompt, user_prompt):
        if "process architect" in system_prompt:
            data = {"goal": "Choose a route", "initial_state": "Two couriers compare routes.",
                    "completion_condition": "A concrete route proposal is delivered.", "intent_mode": "roleplay",
                    "roles": [{"name": "Mara", "objective": "Propose a route"}, {"name": "Ivo", "objective": "Check feasibility"}]}
        elif "route attention" in system_prompt:
            data = {"active_node_ids": self.actors}
        elif "action consistency checker" in system_prompt:
            data = {"decision": "accept", "reason": "Offline fixture.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
        elif "continuity auditor" in system_prompt:
            data = {"decision": "stop" if self.allow_completion else "continue", "task_complete": self.allow_completion,
                    "completion_reason": "The route proposal was delivered.", "state_patch": {"schema_ops": [], "state_ops": []}}
        elif "You are an autonomous participant" in user_prompt:
            self.started.set()
            await asyncio.sleep(0.05)
            data = {"participation": "act", "action": "I propose the north route and ask for a feasibility check.", "turn_delta": "A route is proposed."}
        else:
            return {"content": "## Summary\nThe couriers proposed a route; no unsupported travel or arrival is asserted."}
        return {"content": json.dumps(data), "raw": {"usage": {"prompt_tokens": 30, "completion_tokens": 20}}}


@pytest.fixture
def service(tmp_path, monkeypatch):
    database = create_engine("sqlite:///" + (tmp_path / "runtime.db").as_posix(), connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    provider = ScenarioProvider()
    queue = RunQueueManager(1, 8)
    monkeypatch.setattr(main, "engine", database)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main, "provider", provider)
    monkeypatch.setattr(main, "workflow_engine", WorkflowEngine(provider))
    monkeypatch.setattr(main, "run_queue_manager", queue)
    monkeypatch.setattr(main, "apply_stored_model_config", lambda *_: None)
    monkeypatch.setattr("app.queue_manager.SessionLocal", sessions)

    def get_db():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[main.get_db] = get_db
    try:
        with TestClient(main.app) as client:
            yield client, provider, queue
        assert not queue._workers
    finally:
        main.app.dependency_overrides.clear()
        database.dispose()


def wait_status(client, run_id, statuses):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["status"] in statuses:
            return detail
        if detail["status"] == "failed":
            pytest.fail(str(detail["error"]))
        time.sleep(0.01)
    pytest.fail(f"Run {run_id} did not reach {statuses}")


@pytest.mark.parametrize("stop", [False, True])
def test_compile_save_run_worker_events_result_and_stop_use_real_lifecycle(service, stop):
    client, provider, queue = service
    response = client.post("/api/templates/compile", json={"plan_text": "Two couriers propose a route.", "mode": "roleplay", "max_agents": 2})
    assert response.status_code == 200, response.text
    workflow = response.json()["workflow"]
    provider.actors = [node["id"] for node in workflow["nodes"] if node["type"] == "agent" and not node["id"].startswith(("summary_", "report_"))]
    assert len(provider.actors) == 2
    workflow["environment"]["simulation"].update({"horizon_rounds": 4, "dynamic_state": {"enabled": False}})
    provider.allow_completion = not stop
    assert client.post("/api/workflows", json=workflow).status_code == 200
    reopened = client.get(f"/api/workflows/{workflow['id']}")
    assert reopened.status_code == 200 and len(reopened.json()["nodes"]) == len(workflow["nodes"])
    response = client.post(f"/api/workflows/{workflow['id']}/run", json={"input": {}})
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    if stop:
        assert provider.started.wait(3)
        assert client.post(f"/api/runs/{run_id}/stop").status_code == 200
    detail = wait_status(client, run_id, {"stopped"} if stop else {"succeeded"})
    events = client.get(f"/api/runs/{run_id}/events?limit=500").json()["events"]
    assert any(row["event"] == "queued" for row in events)
    assert any(row["event"] == "action_checked" for row in events)
    assert client.get(f"/api/runs/{run_id}/audit/verify").json()["verified"]
    full_log = client.get(f"/api/runs/{run_id}/full-log?format=markdown").json()
    assert full_log["event_count"] == len(events)
    assert queue.snapshot()["failed"] == 0
    if not stop:
        assert detail["output"]["completion"]["status"] == "completed"
        response = client.post(f"/api/runs/{run_id}/report", json={"mode": "briefing"})
        assert response.status_code == 200, response.text
        assert response.json()["report_markdown"]


def test_human_pause_and_resume_execute_through_the_real_worker(service):
    client, _, queue = service
    workflow = {"id": "human_system", "name": "human_system", "nodes": [
        {"id": "start", "type": "prompt", "position": {"x": 0, "y": 0}, "config": {"template": "A proposal"}},
        {"id": "human", "type": "human_checkpoint", "position": {"x": 1, "y": 0}, "config": {"question_template": "Approve?", "required": "true"}},
        {"id": "finish", "type": "prompt", "position": {"x": 2, "y": 0}, "config": {"template": "Approved result"}},
    ], "edges": [{"id": "first", "source": "start", "target": "human"}, {"id": "last", "source": "human", "target": "finish"}], "entry_nodes": ["start"]}
    assert client.post("/api/workflows", json=workflow).status_code == 200
    run_id = client.post("/api/workflows/human_system/run", json={}).json()["run_id"]
    wait_status(client, run_id, {"waiting_human"})
    response = client.post(f"/api/runs/{run_id}/human-response", json={"node_id": "human", "response": "Approved", "responder": "test-user"})
    assert response.status_code == 200, response.text
    wait_status(client, run_id, {"succeeded"})
    events = client.get(f"/api/runs/{run_id}/events?limit=500").json()["events"]
    assert sum(row["event"] == "human_resumed" for row in events) == 1
    assert client.get(f"/api/runs/{run_id}/audit/verify").json()["verified"]
    assert queue.snapshot()["failed"] == 0
