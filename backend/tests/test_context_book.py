from __future__ import annotations

from app.execution.context_book import (
    activation_trace,
    build_scene_context_pack,
    context_for_node,
    environment_for_runtime,
)
from app.schemas import EnvironmentSpec, NodePosition, WorkflowNode
from app.db import Base
from app.execution.engine import WorkflowEngine
from app.models import NodeRunRecord, RunEventRecord, RunRecord
from app.schemas import PlanCompileRequest, RunRequest, WorkflowDefinition
from app.service import compile_workflow_from_plan
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _node(node_id: str) -> WorkflowNode:
    return WorkflowNode(id=node_id, type="agent", position=NodePosition(x=0, y=0), config={}, inputs={})


def test_context_book_is_backward_compatible() -> None:
    environment = EnvironmentSpec(scenario="legacy workflow")

    assert environment.context_book.entries == []
    assert environment_for_runtime(environment).get("context_book") is None


def test_scene_pack_uses_chinese_phrase_matching_and_one_hop_links() -> None:
    environment = EnvironmentSpec.model_validate(
        {
            "scenario": "学生在夜间准备离开宿舍",
            "context_book": {
                "entries": [
                    {
                        "id": "school_rule",
                        "title": "校规",
                        "content": "学生夜间不得离开宿舍。",
                        "kind": "rule",
                        "activation": {
                            "always": False,
                            "keywords": ["夜间", "宿舍"],
                            "semantic_hint": "学生违反校规",
                            "related_entry_ids": ["guard"],
                        },
                    },
                    {
                        "id": "guard",
                        "title": "门卫",
                        "content": "门卫每小时巡查一次。",
                        "activation": {"always": False},
                    },
                ]
            },
        }
    )

    pack = build_scene_context_pack(environment=environment, state={}, recent_actions=[])

    assert [entry["id"] for entry in pack["entries"]] == ["school_rule", "guard"]
    assert "keyword:夜间" in pack["entries"][0]["activation_reasons"]


def test_private_entries_are_only_visible_to_selected_node() -> None:
    environment = EnvironmentSpec.model_validate(
        {
            "context_book": {
                "entries": [
                    {"id": "public", "content": "Everyone knows this."},
                    {
                        "id": "secret",
                        "content": "Only Alice knows this.",
                        "visibility": {"scope": "private", "node_ids": ["alice"]},
                    },
                ]
            }
        }
    )
    pack = build_scene_context_pack(environment=environment, state={}, recent_actions=[])

    assert [entry["id"] for entry in context_for_node(pack, _node("alice"))["entries"]] == ["public", "secret"]
    assert [entry["id"] for entry in context_for_node(pack, _node("bob"))["entries"]] == ["public"]


def test_context_budget_prefers_higher_priority_entries() -> None:
    environment = EnvironmentSpec.model_validate(
        {
            "context_book": {
                "token_budget": 128,
                "entries": [
                    {"id": "high", "content": "A" * 400, "priority": 100},
                    {"id": "low", "content": "B" * 400, "priority": 1},
                ],
            }
        }
    )

    pack = build_scene_context_pack(environment=environment, state={}, recent_actions=[])

    assert [entry["id"] for entry in pack["entries"]] == ["high"]
    assert activation_trace(pack)["entry_count"] == 1


def test_engine_injects_only_node_visible_background_and_traces_activation() -> None:
    class RecordingProvider:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
            self.prompts.append(user_prompt)
            return {"content": "ok", "raw": {}}

    provider = RecordingProvider()
    database = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=database)
    db = sessionmaker(bind=database)()
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "wf_context_integration",
            "name": "context integration",
            "nodes": [
                {"id": "alice", "type": "agent", "position": {"x": 0, "y": 0}, "config": {}, "inputs": {"prompt": "act"}},
                {"id": "bob", "type": "agent", "position": {"x": 100, "y": 0}, "config": {}, "inputs": {"prompt": "act"}},
            ],
            "edges": [],
            "entry_nodes": ["alice", "bob"],
            "environment": {
                "context_book": {
                    "entries": [
                        {"id": "public", "content": "PUBLIC-CONTEXT"},
                        {
                            "id": "secret",
                            "content": "ALICE-SECRET",
                            "visibility": {"scope": "private", "node_ids": ["alice"]},
                        },
                    ]
                }
            },
        }
    )

    run_id = __import__("asyncio").run(
        WorkflowEngine(provider=provider).run(db=db, workflow=workflow, request=RunRequest(input={}))  # type: ignore[arg-type]
    )

    run = db.query(RunRecord).filter(RunRecord.id == run_id).one()
    assert run.status == "succeeded", run.error_json
    assert "PUBLIC-CONTEXT" in provider.prompts[0]
    assert "ALICE-SECRET" in provider.prompts[0]
    assert "PUBLIC-CONTEXT" in provider.prompts[1]
    assert "ALICE-SECRET" not in provider.prompts[1]
    activation = db.query(RunEventRecord).filter(RunEventRecord.event == "context_pack_activated").one()
    assert activation.node_id == "context_engine"


def test_external_agent_does_not_receive_context_book_by_default() -> None:
    database = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=database)
    db = sessionmaker(bind=database)()
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "wf_external_context_boundary",
            "name": "external context boundary",
            "nodes": [
                {
                    "id": "external",
                    "type": "external_agent",
                    "position": {"x": 0, "y": 0},
                    "config": {"integration_mode": "mock"},
                    "inputs": {"prompt": "act"},
                }
            ],
            "edges": [],
            "entry_nodes": ["external"],
            "environment": {"context_book": {"entries": [{"id": "secret", "content": "DO-NOT-SEND"}]}},
        }
    )

    run_id = __import__("asyncio").run(
        WorkflowEngine(provider=object()).run(db=db, workflow=workflow, request=RunRequest(input={}))  # type: ignore[arg-type]
    )

    node_run = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id).one()
    assert "DO-NOT-SEND" not in node_run.input_json


def test_director_plan_maps_private_background_to_generated_role_nodes() -> None:
    class PlanProvider:
        async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
            return {
                "content": """
                {
                  "intent_mode": "roleplay",
                  "goal": "A private negotiation",
                  "roles": [
                    {"name": "Alice", "count": 1, "focus": "negotiate", "entity_type": "individual"},
                    {"name": "Bob", "count": 1, "focus": "respond", "entity_type": "individual"}
                  ],
                  "environment": {
                    "scenario": "A private negotiation",
                    "context_book": {
                      "entries": [
                        {
                          "title": "Alice secret",
                          "content": "Alice has a hidden deadline.",
                          "kind": "knowledge",
                          "visible_to": ["Alice"],
                          "always": true
                        }
                      ]
                    }
                  }
                }
                """,
                "raw": {},
            }

    result = __import__("asyncio").run(
        compile_workflow_from_plan(
            provider=PlanProvider(),  # type: ignore[arg-type]
            request=PlanCompileRequest(plan_text="Alice negotiates with Bob.", mode="roleplay", max_agents=4),
        )
    )

    alice_ids = [node.id for node in result.workflow.nodes if node.config.get("entity_name") == "Alice"]
    entry = result.workflow.environment.context_book.entries[0]
    assert entry.visibility.scope == "private"
    assert entry.visibility.node_ids == alice_ids
