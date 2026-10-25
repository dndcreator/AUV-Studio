from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import NodeRunRecord, RunRecord
from app.schemas import RunReportRequest, WorkflowDefinition
from app.service import generate_run_report, upsert_workflow


@pytest.fixture
def report_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        workflow = WorkflowDefinition.model_validate({
            "id": "report", "name": "report", "nodes": [{"id": "actor", "type": "agent",
            "position": {"x": 0, "y": 0}, "config": {"entity_name": "Mina"}}], "edges": [], "entry_nodes": ["actor"],
            "environment": {"simulation": {"evidence": {"items": [{"name": "facts", "content": "e" * 2100 + "EVIDENCE_END"}]}}},
        })
        upsert_workflow(db, workflow)
        db.add(RunRecord(id="long", workflow_id="report", status="succeeded"))
        db.add(NodeRunRecord(run_id="long", node_id="actor", status="succeeded",
                             output_json=json.dumps({"content": "BEGIN " + "m" * 17000 + " MIDDLE " + "n" * 2000 + " ENDING"})))
        db.commit()
        yield db
    engine.dispose()


class Writer:
    prompts: list[str]

    def __init__(self):
        self.prompts = []

    async def chat(self, model, system_prompt, user_prompt):
        self.prompts.append(user_prompt)
        return {"content": "# Result\n\nThe ending is preserved."}


@pytest.mark.parametrize("mode", ["briefing", "narrative", "story"])
def test_report_preserves_beginning_middle_ending_and_supplied_evidence(report_db, mode):
    writer = Writer()
    result = asyncio.run(generate_run_report(report_db, writer, "long", RunReportRequest(mode=mode, style_prompt="restrained")))
    assert len(writer.prompts) == 1
    prompt = writer.prompts[0]
    for marker in ("BEGIN", "MIDDLE", "ENDING", "EVIDENCE_END", "restrained"):
        assert marker in prompt
    assert prompt.count("BEGIN") == 1
    assert result.mode == mode


def test_oversized_source_fails_before_any_model_call(report_db, monkeypatch):
    monkeypatch.setattr("app.service.settings", replace(settings, report_source_max_chars=100))
    writer = Writer()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(generate_run_report(report_db, writer, "long", RunReportRequest(mode="narrative")))
    assert exc.value.status_code == 413
    assert "AUV_REPORT_SOURCE_MAX_CHARS=100" in exc.value.detail
    assert writer.prompts == []
