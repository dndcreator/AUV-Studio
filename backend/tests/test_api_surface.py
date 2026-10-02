from __future__ import annotations

from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app, get_db
from app.models import MemoryRecord, RunRecord


def _client_with_db() -> Generator[TestClient, None, None]:
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

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()


def _workflow_payload(workflow_id: str, name: str) -> dict:
    return {
        "id": workflow_id,
        "name": name,
        "version": 1,
        "nodes": [
            {
                "id": "n1",
                "type": "prompt",
                "position": {"x": 0, "y": 0},
                "config": {"template": "hello"},
                "inputs": {},
            }
        ],
        "edges": [],
        "entry_nodes": ["n1"],
        "environment": {"profile": "", "scenario": "", "facts": [], "constraints": [], "glossary": {}},
    }


def test_workflow_crud_list_update_get() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        create = client.post("/api/workflows", json=_workflow_payload("wf_surface", "surface"))
        assert create.status_code == 200

        list_resp = client.get("/api/workflows")
        assert list_resp.status_code == 200
        assert any(w["id"] == "wf_surface" for w in list_resp.json())

        get_resp = client.get("/api/workflows/wf_surface")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "surface"

        payload = _workflow_payload("wf_surface", "surface-updated")
        payload["version"] = 2
        update_resp = client.put("/api/workflows/wf_surface", json=payload)
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "surface-updated"
        assert update_resp.json()["version"] == 2
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_evidence_parse_api_accepts_raw_file_upload() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        response = client.post(
            "/api/evidence/parse",
            content="segment,count\nstudent,20".encode(),
            headers={"Content-Type": "application/octet-stream", "X-Filename": "sample.csv"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["parsed_files"] == 1
        assert "student | 20" in body["items"][0]["content"]
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_research_compile_persists_confirmed_evidence() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        response = client.post(
            "/api/templates/compile",
            json={
                "plan_text": "研究学生对新饮料的接受度",
                "mode": "research",
                "submode": "research",
                "max_agents": 3,
                "language": "zh",
                "evidence_pack": {
                    "version": 1,
                    "package_name": "survey.csv",
                    "items": [{"id": "src_1", "name": "survey.csv", "kind": "table", "content": "segment | count\nstudent | 20"}],
                    "summary": {"parsed_files": 1, "skipped_files": 0, "failed_files": 0, "characters": 31},
                },
            },
        )
        assert response.status_code == 200
        simulation = response.json()["workflow"]["environment"]["simulation"]
        assert simulation["evidence_status"] == "user_confirmed"
        assert simulation["evidence"]["items"][0]["name"] == "survey.csv"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_template_crud_list_get() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        wf = _workflow_payload("wf_tpl_surface", "template-source")
        create_tpl = client.post(
            "/api/templates",
            json={
                "id": "tpl_surface_1",
                "name": "Template Surface",
                "description": "desc",
                "category": "research",
                "tags": ["a", "b"],
                "workflow": wf,
            },
        )
        assert create_tpl.status_code == 200
        assert create_tpl.json()["id"] == "tpl_surface_1"

        list_tpl = client.get("/api/templates")
        assert list_tpl.status_code == 200
        assert any(t["id"] == "tpl_surface_1" for t in list_tpl.json())

        get_tpl = client.get("/api/templates/tpl_surface_1")
        assert get_tpl.status_code == 200
        assert get_tpl.json()["name"] == "Template Surface"
        assert get_tpl.json()["workflow"]["id"] == "wf_tpl_surface"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_interaction_modes_endpoint() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        resp = client.get("/api/interaction-modes")
        assert resp.status_code == 200
        modes = resp.json()
        assert "report" in modes
        assert "instruction" in modes
        assert "dialogue" in modes
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_director_capabilities_endpoint() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        resp = client.get("/api/director/capabilities")
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list)
        assert any(r["capability_id"] == "set_focus" for r in rows)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_mode_contracts_endpoint() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        resp = client.get("/api/mode-contracts")
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list)
        assert any(r["mode"] == "research" for r in rows)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_template_compile_endpoint() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        resp = client.post(
            "/api/templates/compile",
            json={
                "plan_text": "请帮我做一个关于巧克力味啤酒的市场调研，主要关注18-22岁的大学生群体。",
                "mode": "research",
                "submode": "consulting",
                "max_agents": 5,
                "language": "zh",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "workflow" in body
        assert body["workflow"]["id"].startswith("wf_auto_")
        assert len(body["workflow"]["nodes"]) >= 3
        assert body["workflow"]["entry_nodes"] == ["director_1"]
        assert "simulation_blueprint" in body
        assert isinstance(body["simulation_blueprint"], dict)
        assert isinstance(body["workflow"]["environment"].get("simulation"), dict)
        assert body["simulation_blueprint"]["task_contract"]["objective"]
        assert body["simulation_blueprint"]["mode"] == "research"
        assert body["simulation_blueprint"]["submode"] == "consulting"
        assert body["simulation_blueprint"]["assumptions"]
        assert body["simulation_blueprint"]["hypotheses"]
        assert body["simulation_blueprint"]["decision_criteria"]
        assert body["simulation_blueprint"]["research_design"]["methodology"]
        assert body["simulation_blueprint"]["simulation_design"]["interaction_rules"]
        assert body["simulation_blueprint"]["output_plan"]["full_log"] is True
        assert body["simulation_blueprint"]["quality_controls"]
        assert "world_state" in body["simulation_blueprint"]
        assert isinstance(body["simulation_blueprint"]["world_state"], dict)
        agent_nodes = [node for node in body["workflow"]["nodes"] if node["type"] == "agent" and node["id"].startswith("agent_")]
        assert agent_nodes
        assert any(node["config"].get("entity_type") in {"group", "organization", "individual"} for node in agent_nodes)
        for node in agent_nodes:
            assert node["config"].get("entity_name")
            assert node["config"].get("entity_profile")
            assert node["config"].get("behavior_prompt")
            assert node["config"].get("identity_contract", {}).get("identity")
            assert node["config"]["identity_contract"].get("boundaries")
            assert node["config"].get("execution_mode") == "builtin_llm"
            assert node["config"].get("model_connection_mode") == "platform_default"
            assert node["config"].get("model") == ""
        assert any("Decision criteria:" in node["inputs"].get("prompt", "") for node in agent_nodes)
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_template_compile_auto_roleplay_mode() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        resp = client.post(
            "/api/templates/compile",
            json={
                "plan_text": "模拟一个现代战争小队在城市战中的故事，重点是队员冲突、任务转折和最后写成剧本。",
                "mode": "auto",
                "max_agents": 4,
                "language": "zh",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        blueprint = body["simulation_blueprint"]
        assert blueprint["mode"] == "roleplay"
        assert blueprint["domain"] == "story_simulation"
        assert "turning point traceability" in blueprint["success_metrics"]
        assert "screenplay" in blueprint["output_plan"]["narrative_options"]
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_template_compile_dialogue_contract_is_confirmable_and_bounded() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        resp = client.post(
            "/api/templates/compile",
            json={
                "plan_text": "生成一个十句话以内的家庭晚餐对话，表现日常气氛和一点冲突。",
                "mode": "auto",
                "max_agents": 6,
                "language": "zh",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        blueprint = body["simulation_blueprint"]
        assert blueprint["mode"] == "roleplay"
        assert blueprint["ui_mode"] == "roleplay"
        assert blueprint["output_contract"] == {
            "format": "dialogue",
            "max_items": 10,
            "unit": "utterance",
            "final_only": True,
        }
        assert blueprint["detail_granularity"] == "detailed"
        participants = [n for n in body["workflow"]["nodes"] if n["id"].startswith("agent_")]
        assert participants
        assert all("Do not write the whole scene" in n["inputs"]["prompt"] for n in participants)
        summary = next(n for n in body["workflow"]["nodes"] if n["id"] == "summary_1")
        assert summary["config"]["output_contract"]["max_items"] == 10
        assert "no headings" in summary["inputs"]["prompt"]
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_memories_list_and_clear() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        wf = _workflow_payload("wf_mem_surface", "memory surface")
        assert client.post("/api/workflows", json=wf).status_code == 200

        # Insert one run + memory row through API-independent setup path.
        db_dep = app.dependency_overrides[get_db]
        db_gen = db_dep()  # type: ignore[misc]
        db = next(db_gen)
        try:
            run = RunRecord(
                id="run_mem_surface_1",
                workflow_id="wf_mem_surface",
                status="succeeded",
                input_json="{}",
                output_json="{}",
                error_json="null",
            )
            db.add(run)
            db.commit()
            db.add(
                MemoryRecord(
                    workflow_id="wf_mem_surface",
                    run_id="run_mem_surface_1",
                    node_id="a1",
                    role="planner",
                    content="remember this",
                    tags_json='["agent"]',
                )
            )
            db.commit()
        finally:
            try:
                next(db_gen)
            except StopIteration:
                pass

        list_resp = client.get("/api/workflows/wf_mem_surface/memories?limit=20")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 1

        clear_resp = client.delete("/api/workflows/wf_mem_surface/memories")
        assert clear_resp.status_code == 200
        assert clear_resp.json()["deleted"] >= 1
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_director_command_endpoint() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        wf = _workflow_payload("wf_dir_surface", "director surface")
        assert client.post("/api/workflows", json=wf).status_code == 200
        start = client.post("/api/workflows/wf_dir_surface/run", json={"input": {"task": "x"}})
        assert start.status_code == 200
        run_id = start.json()["run_id"]

        cmd = client.post(
            f"/api/runs/{run_id}/director-command",
            json={"text": "收紧范围并降低成本", "scope": "global"},
        )
        assert cmd.status_code == 200
        body = cmd.json()
        assert body["accepted"] is True
        assert body["applied_capability"] in {"set_cost_mode", "set_focus", "set_style", "set_phase", "inject_event"}
        assert body["event_seq"] is not None

        trace_resp = client.get(f"/api/runs/{run_id}/trace/{body['event_seq']}")
        assert trace_resp.status_code == 200
        assert trace_resp.json()["caused_by"] == "director"
        audit_resp = client.get(f"/api/runs/{run_id}/audit/verify")
        assert audit_resp.status_code == 200
        assert audit_resp.json()["verified"] is True
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_stop_pending_run_is_immediate_and_traceable() -> None:
    gen = _client_with_db()
    client = next(gen)
    try:
        db_dep = app.dependency_overrides[get_db]
        db_gen = db_dep()  # type: ignore[misc]
        db = next(db_gen)
        try:
            db.add(
                RunRecord(
                    id="run_stop_surface",
                    workflow_id="wf_stop_surface",
                    status="pending",
                    input_json="{}",
                    output_json="{}",
                    error_json="null",
                )
            )
            db.commit()
        finally:
            try:
                next(db_gen)
            except StopIteration:
                pass

        response = client.post("/api/runs/run_stop_surface/stop")
        assert response.status_code == 200
        assert response.json() == {"run_id": "run_stop_surface", "status": "stopped"}
        detail = client.get("/api/runs/run_stop_surface")
        assert detail.status_code == 200
        assert detail.json()["status"] == "stopped"
        events = client.get("/api/runs/run_stop_surface/events").json()["events"]
        assert events[-1]["event"] == "stopped"
        assert events[-1]["payload"]["requested_by"] == "user"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

