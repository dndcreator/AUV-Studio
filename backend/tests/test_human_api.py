from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.main import human_response_api, run_workflow_api
from app.models import RunRecord
from app.node_specs import get_node_specs
from app.schemas import HumanResponseRequest, NodePosition, RunRequest, WorkflowDefinition, WorkflowNode
from app.service import upsert_workflow


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    local = sessionmaker(bind=engine)
    return local()


def make_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        id="wf_human_api",
        name="human api test",
        nodes=[
            WorkflowNode(id="hc1", type="human_checkpoint", position=NodePosition(x=0, y=0), config={}, inputs={}),
        ],
        edges=[],
        entry_nodes=["hc1"],
    )


def test_human_response_run_not_found() -> None:
    db = make_db()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(human_response_api("run_missing", HumanResponseRequest(response="ok"), db))
    assert exc.value.status_code == 404


def test_human_response_reject_when_not_waiting() -> None:
    db = make_db()
    wf = make_workflow()
    upsert_workflow(db, wf)
    db.add(
        RunRecord(
            id="run_1",
            workflow_id=wf.id,
            status="running",
            input_json="{}",
            output_json="{}",
            error_json="null",
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(human_response_api("run_1", HumanResponseRequest(response="ok"), db))
    assert exc.value.status_code == 409


def test_human_response_reject_when_node_id_unknown() -> None:
    db = make_db()
    wf = make_workflow()
    upsert_workflow(db, wf)
    db.add(
        RunRecord(
            id="run_2",
            workflow_id=wf.id,
            status="waiting_human",
            input_json="{}",
            output_json="{}",
            error_json="null",
            retry_from_run_id=None,
            retry_from_node=None,
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(human_response_api("run_2", HumanResponseRequest(response="ok"), db))
    assert exc.value.status_code == 400


def test_human_response_enqueue_success(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db()
    wf = make_workflow()
    upsert_workflow(db, wf)
    db.add(
        RunRecord(
            id="run_3",
            workflow_id=wf.id,
            status="waiting_human",
            input_json="{}",
            output_json="{}",
            error_json=json.dumps({"node_id": "hc1"}),
            retry_from_run_id=None,
            retry_from_node=None,
        )
    )
    db.commit()

    captured: list[object] = []

    class StubQueue:
        def enqueue_nowait(self, task):  # type: ignore[no-untyped-def]
            captured.append(task)

    monkeypatch.setattr("app.main.run_queue_manager", StubQueue())

    result = asyncio.run(
        human_response_api(
            "run_3",
            HumanResponseRequest(response="approve and continue", responder="operator", metadata={"source": "test"}),
            db,
        )
    )
    assert result["run_id"] == "run_3"
    assert result["status"] == "queued"
    assert len(captured) == 1

    rec = db.query(RunRecord).filter(RunRecord.id == "run_3").first()
    assert rec is not None
    assert rec.status == "pending"
    assert rec.error_json == "null"
    assert rec.retry_from_run_id == "run_3"
    assert rec.retry_from_node == "hc1"

    task = captured[0]
    assert getattr(task, "run_id") == "run_3"
    request = getattr(task, "request")
    assert request.retry_from_run_id == "run_3"
    assert request.retry_from_node == "hc1"
    assert request.input["_human_response"]["response"] == "approve and continue"
    assert request.input["_human_response"]["responder"] == "operator"


def test_human_response_queue_full_keeps_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db()
    wf = make_workflow()
    upsert_workflow(db, wf)
    db.add(
        RunRecord(
            id="run_4",
            workflow_id=wf.id,
            status="waiting_human",
            input_json="{}",
            output_json="{}",
            error_json=json.dumps({"node_id": "hc1"}),
            retry_from_run_id=None,
            retry_from_node=None,
        )
    )
    db.commit()

    class FullQueue:
        def enqueue_nowait(self, task):  # type: ignore[no-untyped-def]
            raise asyncio.QueueFull()

    monkeypatch.setattr("app.main.run_queue_manager", FullQueue())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(human_response_api("run_4", HumanResponseRequest(response="continue"), db))
    assert exc.value.status_code == 429

    rec = db.query(RunRecord).filter(RunRecord.id == "run_4").first()
    assert rec is not None
    assert rec.status == "waiting_human"


def test_node_specs_include_human_checkpoint() -> None:
    specs = get_node_specs()
    target = next((s for s in specs if s.type == "human_checkpoint"), None)
    assert target is not None
    config_keys = {f.key for f in target.config_fields}
    input_keys = {f.key for f in target.input_fields}
    assert {"owner", "question_template", "required"}.issubset(config_keys)
    assert {"question", "context_hint"}.issubset(input_keys)
    external = next((s for s in specs if s.type == "external_agent"), None)
    assert external is not None
    mode_field = next((f for f in external.config_fields if f.key == "integration_mode"), None)
    assert mode_field is not None
    assert "bridge_local" in mode_field.options


def test_run_workflow_queue_full_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_db()
    wf = make_workflow()
    upsert_workflow(db, wf)

    class FullQueue:
        def enqueue_nowait(self, task):  # type: ignore[no-untyped-def]
            raise asyncio.QueueFull()

    monkeypatch.setattr("app.main.run_queue_manager", FullQueue())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run_workflow_api(wf.id, RunRequest(input={}), db))
    assert exc.value.status_code == 429

    rec = db.query(RunRecord).order_by(RunRecord.started_at.desc()).first()
    assert rec is not None
    assert rec.status == "failed"
