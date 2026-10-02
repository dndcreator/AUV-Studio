from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.execution.engine import WorkflowEngine, WorkflowValidationError
from app.execution.simulation_loop import action_instruction, build_loop_policy, normalize_action_output
from app.observability import perf_tracker
from app.queue_manager import RunQueueManager
from app.schemas import EnvironmentSpec, InteractionSpec, NodePosition, RunReportRequest, RunRequest, TemplateCreateRequest, WorkflowDefinition, WorkflowEdge, WorkflowNode
from app.service import (
    compile_workflow_from_plan,
    compare_runs,
    create_or_update_template,
    generate_run_report,
    get_trace_event_context,
    get_observability_snapshot,
    get_run_metrics,
    get_template,
    list_templates,
    verify_run_audit,
)


class FakeProvider:
    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        return {"content": f"{model}:{user_prompt}", "raw": {"ok": True}}


class CapturingProvider:
    def __init__(self) -> None:
        self.user_prompt = ""
        self.model = ""

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        self.model = model
        self.user_prompt = user_prompt
        return {"content": "The actor reaches a decision after a conflict.", "raw": {"ok": True}}


class SequenceProvider:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.user_prompts: list[str] = []

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        self.user_prompts.append(user_prompt)
        idx = min(len(self.user_prompts) - 1, len(self.outputs) - 1)
        return {"content": self.outputs[idx], "raw": {"ok": True}}


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    local = sessionmaker(bind=engine)
    return local()


def make_node(node_id: str, node_type: str, config: dict | None = None, inputs: dict | None = None) -> WorkflowNode:
    return WorkflowNode(
        id=node_id,
        type=node_type,  # type: ignore[arg-type]
        position=NodePosition(x=0, y=0),
        config=config or {},
        inputs=inputs or {},
    )


def test_cycle_validation() -> None:
    wf = WorkflowDefinition(
        id="wf1",
        name="cycle",
        nodes=[make_node("a", "prompt"), make_node("b", "prompt")],
        edges=[WorkflowEdge(id="e1", source="a", target="b"), WorkflowEdge(id="e2", source="b", target="a")],
        entry_nodes=["a"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    try:
        engine._validate(wf)
        assert False, "expected cycle validation error"
    except WorkflowValidationError:
        assert True


def test_engine_honors_stop_before_queued_run_starts() -> None:
    db = make_db()
    from app.models import RunEventRecord, RunRecord

    db.add(
        RunRecord(
            id="run_stopping_engine",
            workflow_id="wf_stop_engine",
            status="stopping",
            input_json="{}",
            output_json="{}",
            error_json="null",
        )
    )
    db.commit()
    wf = WorkflowDefinition(
        id="wf_stop_engine",
        name="stop before start",
        nodes=[make_node("p1", "prompt", config={"template": "must not execute"})],
        edges=[],
        entry_nodes=["p1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={}), run_id="run_stopping_engine"))

    run = db.query(RunRecord).filter(RunRecord.id == "run_stopping_engine").one()
    assert run.status == "stopped"
    assert db.query(RunEventRecord).filter(RunEventRecord.run_id == run.id, RunEventRecord.event == "stopped").count() == 1


def test_director_plan_prompt_keeps_roleplay_entities_inside_the_world() -> None:
    provider = CapturingProvider()
    __import__("asyncio").run(
        compile_workflow_from_plan(
            provider=provider,  # type: ignore[arg-type]
            request=__import__("app.schemas", fromlist=["PlanCompileRequest"]).PlanCompileRequest(
                plan_text="Simulate a long-running fictional conflict.",
                mode="roleplay",
                max_agents=4,
                language="en",
            ),
        )
    )
    assert "roles must be entities inside the simulated world" in provider.user_prompt
    assert "only when the user explicitly asks for collaborative creation" in provider.user_prompt
    assert "shared task contract" in provider.user_prompt
    assert "identity contract" in provider.user_prompt
    assert "not by literary functions" in provider.user_prompt


def test_autonomous_action_contract_is_rich_but_limited_to_self() -> None:
    prompt = action_instruction(
        {
            "state": {
                "task_contract": {
                    "deliverable": {"format": "screenplay", "style": "concrete dialogue and physical action"}
                }
            }
        },
        {
            "identity": "A team leader",
            "identity_contract": {"objective": "Keep the team safe", "knowledge": ["the current route"]},
        },
    )
    assert "participation (act|observe|wait)" in prompt
    assert "as rich as the requested deliverable needs" in prompt
    assert "never write another participant's action" in prompt
    assert "never turn an unobserved assumption into a global fact" in prompt

    normalized = normalize_action_output(
        {
            "content": (
                '{"participation":"act","action":"I block the doorway and order a halt.",'
                '"intent":"protect the team","observation":"smoke ahead; source uncertain",'
                '"self_update":"alert","shared_effect_claim":"the team may have stopped",'
                '"episode_signal":"ready"}'
            )
        },
        round_index=1,
        action_index=1,
    )
    assert normalized["participation"] == "act"
    assert normalized["intent"] == "protect the team"
    assert normalized["shared_effect_claim"] == "the team may have stopped"
    assert normalized["boundary_signal"] == "ready"


def test_condition_branch_only_true_runs() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf2",
        name="branch",
        nodes=[
            make_node("cond", "condition", config={"expression": "True"}),
            make_node("t", "prompt", config={"template": "true"}),
            make_node("f", "prompt", config={"template": "false"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source="cond", target="t", condition="true"),
            WorkflowEdge(id="e2", source="cond", target="f", condition="false"),
        ],
        entry_nodes=["cond"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    rows = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id).all()
    status = {r.node_id: r.status for r in rows}
    assert status["cond"] == "succeeded"
    assert status["t"] == "succeeded"
    assert "f" not in status


def test_retry_from_failed_node() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf3",
        name="retry",
        nodes=[
            make_node("a", "prompt", config={"template": "hello"}),
            make_node("b", "condition", config={"expression": "1/0"}),
        ],
        edges=[WorkflowEdge(id="e1", source="a", target="b")],
        entry_nodes=["a"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run1 = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    # Fix workflow and retry from b
    wf.nodes[1].config["expression"] = "True"
    run2 = __import__("asyncio").run(
        engine.run(
            db=db,
            workflow=wf,
            request=RunRequest(input={}, retry_from_run_id=run1, retry_from_node="b"),
        )
    )
    from app.models import RunRecord

    r2 = db.query(RunRecord).filter(RunRecord.id == run2).first()
    assert r2 is not None
    assert r2.status == "succeeded"


def test_human_checkpoint_pause_and_resume() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_human_1",
        name="human-checkpoint",
        nodes=[
            make_node("p1", "prompt", config={"template": "initial draft"}),
            make_node(
                "hc1",
                "human_checkpoint",
                config={"owner": "director", "question_template": "Need human decision", "required": "true"},
                inputs={"context_hint": "review draft"},
            ),
            make_node("p2", "prompt", config={"template": "finalized"}),
        ],
        edges=[WorkflowEdge(id="e1", source="p1", target="hc1"), WorkflowEdge(id="e2", source="hc1", target="p2")],
        entry_nodes=["p1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import RunEventRecord, RunRecord

    r1 = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    assert r1 is not None
    assert r1.status == "waiting_human"

    __import__("asyncio").run(
        engine.run(
            db=db,
            workflow=wf,
            request=RunRequest(
                input={"_human_response": {"node_id": "hc1", "response": "Approved. continue.", "responder": "operator"}},
                retry_from_run_id=run_id,
                retry_from_node="hc1",
            ),
            run_id=run_id,
        )
    )

    r2 = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    assert r2 is not None
    assert r2.status == "succeeded"

    events = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id).all()
    event_types = [e.event for e in events]
    assert "waiting_human" in event_types
    assert "human_resumed" in event_types


def test_director_preflight_confirm_pause_and_resume() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_preflight_1",
        name="preflight-confirm",
        nodes=[
            make_node("director_1", "director", config={"model": "mock", "objective": "align", "style_guardrails": "concise"}),
            make_node("agent_1", "agent", config={"role": "planner", "model": "mock", "system_prompt": "plan"}, inputs={"prompt": "step 1"}),
            make_node("agent_2", "agent", config={"role": "coder", "model": "mock", "system_prompt": "do"}, inputs={"prompt": "step 2"}),
        ],
        edges=[WorkflowEdge(id="e1", source="director_1", target="agent_1"), WorkflowEdge(id="e2", source="agent_1", target="agent_2")],
        entry_nodes=["director_1"],
        environment={
            "profile": "",
            "scenario": "",
            "facts": [],
            "constraints": [],
            "glossary": {},
            "simulation": {
                "preflight_confirm": {
                    "enabled": True,
                    "warmup_nodes": 1,
                    "min_remaining_expensive_nodes": 1,
                    "question": "confirm direction before costly stage",
                }
            },
        },
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import RunEventRecord, RunRecord

    r1 = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    assert r1 is not None
    assert r1.status == "waiting_human"
    err1 = __import__("json").loads(r1.error_json)
    assert err1["code"] == "DIRECTOR_PREFLIGHT_CONFIRM_REQUIRED"
    assert err1["node_id"] == "agent_1"

    __import__("asyncio").run(
        engine.run(
            db=db,
            workflow=wf,
            request=RunRequest(
                input={
                    "_human_response": {
                        "node_id": "agent_1",
                        "response": "direction confirmed",
                        "responder": "operator",
                        "metadata": {"kind": "director_preflight_confirm"},
                    }
                },
                retry_from_run_id=run_id,
                retry_from_node="agent_1",
            ),
            run_id=run_id,
        )
    )
    r2 = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    assert r2 is not None
    assert r2.status == "succeeded"
    events = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id).all()
    event_types = [e.event for e in events]
    assert "waiting_human" in event_types
    assert "human_resumed" in event_types


def test_interaction_report_injected_to_agent_prompt() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf4",
        name="interaction",
        nodes=[
            make_node("researcher", "prompt", config={"template": "finding:A"}),
            make_node("summary", "agent", config={"role": "reviewer", "model": "mock", "system_prompt": "summarize"}),
        ],
        edges=[
            WorkflowEdge(
                id="e1",
                source="researcher",
                target="summary",
                interaction=InteractionSpec(mode="report", relation="member", template="report={{source_output}}"),
            )
        ],
        entry_nodes=["researcher"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "summary").first()
    assert row is not None
    node_input = __import__("json").loads(row.input_json)
    assert "_interactions" in node_input
    assert len(node_input["_interactions"]) == 1
    assert node_input["_interactions"][0]["message"] == "report=finding:A"
    assert "raw" not in node_input["_interactions"][0]["message"]


def test_agent_output_contract_enforces_utterance_limit() -> None:
    db = make_db()
    provider = SequenceProvider(["one\ntwo\nthree\nfour"])
    wf = WorkflowDefinition(
        id="wf_output_contract",
        name="bounded-output",
        nodes=[
            make_node(
                "summary_1",
                "agent",
                config={
                    "role": "reviewer",
                    "system_prompt": "return the final dialogue",
                    "output_contract": {"format": "dialogue", "max_items": 3, "unit": "utterance", "final_only": True},
                },
                inputs={"prompt": "write dialogue"},
            )
        ],
        edges=[],
        entry_nodes=["summary_1"],
    )
    engine = WorkflowEngine(provider=provider)  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "summary_1").first()
    assert row is not None
    output = __import__("json").loads(row.output_json)
    assert output["content"] == "one\ntwo\nthree"


def test_platform_default_agent_uses_global_provider_model() -> None:
    db = make_db()
    provider = CapturingProvider()
    wf = WorkflowDefinition(
        id="wf_platform_default_model",
        name="platform-default-model",
        nodes=[
            make_node(
                "agent_1",
                "agent",
                config={
                    "role": "analyst",
                    "model_connection_mode": "platform_default",
                    "model": "gpt-4o-mini",
                    "system_prompt": "analyze",
                },
                inputs={"prompt": "run"},
            )
        ],
        edges=[],
        entry_nodes=["agent_1"],
    )
    engine = WorkflowEngine(provider=provider)  # type: ignore[arg-type]
    __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))

    assert provider.model == ""


def test_director_vote_mechanism_aggregates_upstream_interactions() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_vote_1",
        name="director-vote",
        nodes=[
            make_node("a1", "prompt", config={"template": "yes, I support and approve this plan"}),
            make_node("a2", "prompt", config={"template": "no, I oppose and reject this plan"}),
            make_node(
                "decision_1",
                "agent",
                config={"role": "decision_maker", "model": "mock", "system_prompt": "decide based on reports"},
                inputs={"prompt": "approve this go/no-go decision after reviewing the reports"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source="a1", target="decision_1", interaction=InteractionSpec(mode="report", relation="peer")),
            WorkflowEdge(id="e2", source="a2", target="decision_1", interaction=InteractionSpec(mode="report", relation="peer")),
        ],
        entry_nodes=["a1", "a2"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import RunEventRecord

    row = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.event == "director_vote_finished")
        .first()
    )
    assert row is not None
    payload = __import__("json").loads(row.payload_json)
    assert payload["counts"]["yes"] == 1
    assert payload["counts"]["no"] == 1
    assert payload["passed"] is False
    assert payload["trigger_node_id"] == "decision_1"

def test_director_gpro_guidance_injected_to_agent() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf5",
        name="director",
        nodes=[
            make_node(
                "director_1",
                "director",
                config={
                    "model": "mock",
                    "objective": "align output",
                    "style_guardrails": "concise",
                    "gpro_candidates": 2,
                },
                inputs={"situation": "team simulation"},
            ),
            make_node(
                "agent_1",
                "agent",
                config={"role": "coder", "model": "mock", "system_prompt": "follow guidance"},
                inputs={"prompt": "execute"},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source="director_1", target="agent_1")],
        entry_nodes=["director_1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "agent_1").first()
    assert row is not None
    node_input = __import__("json").loads(row.input_json)
    assert "_global_guidance" in node_input
    assert node_input["_global_guidance"] != ""


def test_director_override_memory_is_pulled_into_global_guidance() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_dir_override_1",
        name="director-override",
        nodes=[make_node("agent_1", "agent", config={"role": "analyst", "model": "mock", "system_prompt": "follow guidance"}, inputs={"prompt": "run"})],
        edges=[],
        entry_nodes=["agent_1"],
    )
    from app.models import MemoryRecord, NodeRunRecord, RunRecord

    run_id = "run_dir_override_1"
    db.add(
        RunRecord(
            id=run_id,
            workflow_id=wf.id,
            status="pending",
            input_json="{}",
            output_json="{}",
            error_json="null",
        )
    )
    db.commit()
    db.add(
        MemoryRecord(
            workflow_id=wf.id,
            run_id=run_id,
            node_id="director_console",
            role="director_override",
            content="Focus only on sample size quality and cost control.",
            tags_json='["director_override","set_focus"]',
        )
    )
    db.commit()

    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={}), run_id=run_id))

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "agent_1").first()
    assert row is not None
    node_input = __import__("json").loads(row.input_json)
    assert "_global_guidance" in node_input
    assert "cost control" in str(node_input["_global_guidance"]).lower()


def test_environment_injected_to_node_input() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf6",
        name="env",
        nodes=[
            make_node("a", "prompt", config={"template": "ok"}),
        ],
        edges=[],
        entry_nodes=["a"],
        environment={
            "profile": "academic collaboration",
            "scenario": "weekly review",
            "time_context": "Friday morning, week 3",
            "spatial_context": "remote lab meeting room",
            "facts": ["deadline Friday", "budget limited"],
            "constraints": ["be concise"],
            "glossary": {"PI": "principal investigator"},
        },
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "a").first()
    assert row is not None
    node_input = __import__("json").loads(row.input_json)
    assert "_environment" in node_input
    assert node_input["_environment"]["profile"] == "academic collaboration"
    assert node_input["_environment"]["time_context"] == "Friday morning, week 3"
    assert node_input["_environment"]["spatial_context"] == "remote lab meeting room"


def test_simulation_state_injected_and_emitted() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_sim_1",
        name="simulation-state",
        nodes=[make_node("a", "prompt", config={"template": "ok"})],
        edges=[],
        entry_nodes=["a"],
        environment={
            "profile": "simulation",
            "scenario": "test run",
            "time_context": "day 1",
            "spatial_context": "test lab",
            "facts": [],
            "constraints": [],
            "glossary": {},
            "simulation": {
                "version": 1,
                "mode": "research",
                "research_question": "what will happen",
                "phases": [{"id": "p1", "name": "Scope", "objective": "scope", "exit_criteria": "done"}],
                "variables": {"progress": 0.1, "confidence": 0.4, "risk": 0.2, "alignment": 0.5},
                "world_state": {
                    "current_time": "day 1 morning",
                    "current_location": "test lab / room A",
                    "conditions": ["quiet"],
                },
            },
        },
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord, RunEventRecord

    node_row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "a").first()
    assert node_row is not None
    node_input = __import__("json").loads(node_row.input_json)
    assert "_simulation" in node_input
    assert isinstance(node_input["_simulation"], dict)
    assert node_input["_simulation"]["mode"] == "research"

    event_row = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.node_id == "a", RunEventRecord.event == "succeeded")
        .first()
    )
    assert event_row is not None
    payload = __import__("json").loads(event_row.payload_json)
    assert "simulation_state" in payload
    assert isinstance(payload["simulation_state"], dict)
    assert "variables" in payload["simulation_state"]
    assert "state_memory" in payload["simulation_state"]
    assert payload["simulation_state"]["world_state"]["current_time"] == "day 1 morning"
    assert payload["simulation_state"]["world_state"]["current_location"] == "test lab / room A"
    assert isinstance(payload["simulation_state"]["state_memory"], dict)
    assert "current_states" in payload["simulation_state"]["state_memory"]
    assert "_trace" in payload
    assert isinstance(payload["_trace"], dict)
    assert payload["_trace"].get("event_hash")


def test_run_metrics_computed() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf7",
        name="metrics",
        nodes=[
            make_node("p1", "prompt", config={"template": "hello"}),
            make_node("a1", "agent", config={"role": "coder", "model": "mock", "system_prompt": "x"}, inputs={"prompt": "do"}),
        ],
        edges=[WorkflowEdge(id="e1", source="p1", target="a1")],
        entry_nodes=["p1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    metrics = get_run_metrics(db, run_id)
    assert metrics.total_nodes >= 2
    assert metrics.total_events > 0
    assert metrics.estimated_token_usage >= 0


def test_three_layer_memory_written_and_injected() -> None:
    db = make_db()
    provider = CapturingProvider()
    wf = WorkflowDefinition(
        id="wf_memory_layers",
        name="memory layers",
        nodes=[
            make_node(
                "alice",
                "agent",
                config={
                    "role": "custom",
                    "model": "mock",
                    "system_prompt": "Act as Alice.",
                    "entity_type": "individual",
                    "entity_name": "Alice",
                    "entity_profile": "A cautious strategist.",
                },
                    inputs={"prompt": "Alice argues and reaches a decision with conflict."},
            ),
            make_node(
                "bob",
                "agent",
                config={
                    "role": "custom",
                    "model": "mock",
                    "system_prompt": "Act as Bob.",
                    "entity_type": "individual",
                    "entity_name": "Bob",
                },
                inputs={"prompt": "Respond using memory."},
            ),
        ],
        edges=[WorkflowEdge(id="e_alice_bob", source="alice", target="bob")],
        entry_nodes=["alice"],
    )
    engine = WorkflowEngine(provider=provider)  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import MemoryRecord, NodeRunRecord

    rows = db.query(MemoryRecord).filter(MemoryRecord.run_id == run_id).all()
    metas = [__import__("json").loads(row.tags_json) for row in rows]
    kinds = {meta.get("kind") for meta in metas if isinstance(meta, dict)}
    assert {"fact", "state", "character"}.issubset(kinds)
    assert all(meta.get("source_event_seq") for meta in metas if isinstance(meta, dict))

    bob_row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "bob").first()
    assert bob_row is not None
    bob_input = __import__("json").loads(bob_row.input_json)
    injected = bob_input.get("_memory", [])
    assert isinstance(injected, list)
    assert any(item.get("kind") == "state" for item in injected if isinstance(item, dict))
    assert "Facts:" in provider.user_prompt
    assert "Current State:" in provider.user_prompt
    assert "Character State:" in provider.user_prompt


def test_memory_write_trigger_filters_trivial_facts() -> None:
    trivial_node = make_node("p", "prompt", config={"template": "ok"})
    assert WorkflowEngine._should_write_memory(
        kind="fact",
        node=trivial_node,
        content="ok",
        importance=0.55,
        interactions=[],
    ) is False
    assert WorkflowEngine._should_write_memory(
        kind="fact",
        node=trivial_node,
        content="The group reaches a decision after a conflict.",
        importance=0.55,
        interactions=[],
    ) is True
    assert WorkflowEngine._should_write_memory(
        kind="state",
        node=trivial_node,
        content='{"task_state":"planning"}',
        importance=0.8,
        state_before={"current_states": {"task_state": "planning"}},
        state_after={"current_states": {"task_state": "planning"}},
        interactions=[],
    ) is False


def test_director_quality_correction_guides_following_node() -> None:
    db = make_db()
    provider = SequenceProvider(["ok", "The second node responds."])
    wf = WorkflowDefinition(
        id="wf_quality_correction",
        name="quality correction",
        nodes=[
            make_node("a1", "agent", config={"role": "actor", "model": "mock", "system_prompt": "act"}, inputs={"prompt": "Create a vivid scene."}),
            make_node("a2", "agent", config={"role": "actor", "model": "mock", "system_prompt": "act"}, inputs={"prompt": "Continue."}),
        ],
        edges=[WorkflowEdge(id="e1", source="a1", target="a2")],
        entry_nodes=["a1"],
        environment={
            "profile": "",
            "scenario": "",
            "facts": [],
            "constraints": [],
            "glossary": {},
            "simulation": {
                "mode": "roleplay",
                "detail_granularity": "detailed",
                "director_quality_control": {"enabled": True, "interval_nodes": 1, "threshold": 0.6},
            },
        },
    )
    engine = WorkflowEngine(provider=provider)  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import RunEventRecord

    correction = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.event == "director_corrected")
        .first()
    )
    assert correction is not None
    assert "Director guidance:" in provider.user_prompts[-1]
    assert "concrete scene actions" in provider.user_prompts[-1]


def test_compare_runs_works() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf8",
        name="compare",
        nodes=[make_node("p1", "prompt", config={"template": "hello"})],
        edges=[],
        entry_nodes=["p1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_a = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={"task": "A"})))
    run_b = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={"task": "B"})))
    result = compare_runs(db, run_a, run_b)
    assert result.run_a == run_a
    assert result.run_b == run_b


def test_generate_story_report() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf9",
        name="report",
        nodes=[make_node("p1", "prompt", config={"template": "battle starts"})],
        edges=[],
        entry_nodes=["p1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={"task": "war sim"})))
    report = __import__("asyncio").run(
        generate_run_report(
            db=db,
            provider=FakeProvider(),  # type: ignore[arg-type]
            run_id=run_id,
            request=RunReportRequest(mode="story", length="short", style_prompt="cinematic"),
        )
    )
    assert report.run_id == run_id
    assert report.mode == "story"
    assert len(report.report_markdown) > 20


def test_report_writer_receives_readable_material_without_provider_raw_data() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_report_readable",
        name="readable report",
        nodes=[
            make_node("agent_1", "agent", config={"entity_name": "小林", "role": "student"}),
            make_node("summary_1", "prompt", config={"template": "summarize"}),
        ],
        edges=[WorkflowEdge(id="e1", source="agent_1", target="summary_1")],
        entry_nodes=["agent_1"],
        environment=EnvironmentSpec(simulation={"ui_mode": "roleplay"}),
    )
    engine_provider = SequenceProvider(["小林说：今天一起回家吧。", "两人并肩走出了校门。"])
    engine = WorkflowEngine(provider=engine_provider)  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    report_provider = SequenceProvider(["## 校园黄昏\n\n两人走出校门。"])

    report = __import__("asyncio").run(
        generate_run_report(
            db=db,
            provider=report_provider,  # type: ignore[arg-type]
            run_id=run_id,
            request=RunReportRequest(mode="story", length="short"),
        )
    )

    prompt = report_provider.user_prompts[-1]
    assert "Readable Simulation Material" in prompt
    assert "小林说：今天一起回家吧。" in prompt
    assert '"raw"' not in prompt
    assert '"choices"' not in prompt
    assert "payload=" not in prompt
    assert report.title.startswith("Simulation Narrative")


def test_continuous_loop_uses_semantic_activation_and_autonomous_node_proposals() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_continuous_roleplay",
        name="continuous roleplay",
        nodes=[
            make_node("director_1", "director", config={"objective": "keep the scene focused", "gpro_candidates": 1}),
            make_node(
                "agent_1",
                "agent",
                config={"entity_name": "Lin", "entity_type": "individual", "entity_profile": "quiet student", "role": "individual"},
                inputs={"prompt": "act"},
            ),
            make_node(
                "agent_2",
                "agent",
                config={"entity_name": "Chen", "entity_type": "individual", "entity_profile": "direct student", "role": "individual"},
                inputs={"prompt": "act"},
            ),
            make_node("summary_1", "agent", config={"role": "reviewer"}, inputs={"prompt": "summarize"}),
        ],
        edges=[],
        entry_nodes=["director_1"],
        environment=EnvironmentSpec(
            simulation={
                "mode": "roleplay",
                "execution_model": "continuous",
                "horizon_rounds": 3,
                "director_interval_rounds": 1,
                "output_contract": {"max_items": 6, "min_items": 2, "unit": "action"},
            }
        ),
    )
    provider = SequenceProvider(
        [
            '{"active_node_ids":["agent_1","agent_2"],"reason":"both are involved"}',
            '{"action":"Lin says hello.","proposal_summary":"start a conversation","public_state_update":"Lin greets Chen","private_state_update":"less hesitant","boundary_signal":"none"}',
            '{"action":"Chen answers.","proposal_summary":"engage Lin","public_state_update":"Chen responds","private_state_update":"curious","boundary_signal":"ready"}',
            '{"decision":"continue","guidance":"Only preserve continuity.","reason":"exchange remains open","shared_state_summary":"Lin and Chen have started talking."}',
            '{"active_node_ids":["agent_1","agent_2"],"reason":"conversation continues"}',
            '{"action":"Lin asks about class.","proposal_summary":"learn Chen opinion","public_state_update":"A class topic is opened","private_state_update":"more confident","boundary_signal":"none"}',
            '{"action":"Chen smiles.","proposal_summary":"signal interest","public_state_update":"Chen reacts positively","private_state_update":"receptive","boundary_signal":"ready"}',
            '{"decision":"transition","guidance":"Carry forward established facts only.","reason":"topic is resolved","shared_state_summary":"They have established mutual interest."}',
            '{"active_node_ids":["agent_1","agent_2"],"reason":"both decide what follows"}',
            '{"action":"Lin suggests walking home.","proposal_summary":"continue together","public_state_update":"Lin proposes leaving together","private_state_update":"committed","boundary_signal":"ready"}',
            '{"action":"Chen agrees.","proposal_summary":"accept proposal","public_state_update":"They leave together","private_state_update":"trust increased","boundary_signal":"ready"}',
            '{"decision":"stop","guidance":"","reason":"objective reached","shared_state_summary":"They leave campus together."}',
            "## Final scene\n\nThey leave campus together.",
        ]
    )
    engine = WorkflowEngine(provider=provider)  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))

    from app.models import NodeRunRecord, RunEventRecord, RunRecord

    run = db.query(RunRecord).filter(RunRecord.id == run_id).one()
    action_events = (
        db.query(RunEventRecord)
        .filter(
            RunEventRecord.run_id == run_id,
            RunEventRecord.event == "succeeded",
            RunEventRecord.node_id.in_(["agent_1", "agent_2"]),
        )
        .order_by(RunEventRecord.seq.asc())
        .all()
    )
    agent_runs = (
        db.query(NodeRunRecord)
        .filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id.in_(["agent_1", "agent_2"]))
        .all()
    )
    summary_runs = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "summary_1").all()

    assert run.status == "succeeded"
    assert len(action_events) == 6
    assert len(agent_runs) == 6
    assert len(summary_runs) == 1
    activation_events = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id, RunEventRecord.event == "nodes_activated").all()
    audit_events = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id, RunEventRecord.event == "episode_audited").all()
    assert len(activation_events) == 3
    assert len(audit_events) == 3
    first_input = __import__("json").loads(agent_runs[0].input_json)
    first_output = __import__("json").loads(agent_runs[0].output_json)
    assert first_input["context"]["state"]["mode"] == "roleplay"
    assert first_input["role"]["identity"] in {"Lin", "Chen"}
    assert first_output["action"]
    assert first_output["proposal_summary"]
    assert first_output["action_index"] == 1


def test_distributed_activation_is_mode_neutral_and_skips_irrelevant_research_nodes() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_distributed_research",
        name="distributed research",
        nodes=[
            make_node("director_1", "director", config={"objective": "audit the research stage"}),
            make_node("analyst_1", "agent", config={"role": "analyst", "profile": "analyze interview evidence"}),
            make_node("reviewer_1", "agent", config={"role": "reviewer", "profile": "challenge mature conclusions"}),
        ],
        edges=[],
        entry_nodes=["director_1"],
        environment=EnvironmentSpec(
            scenario="Early interview evidence has arrived.",
            simulation={
                "mode": "research",
                "execution_model": "continuous",
                "horizon_rounds": 1,
                "director_interval_rounds": 1,
            },
        ),
    )
    provider = SequenceProvider(
        [
            '{"active_node_ids":["analyst_1"],"reason":"only the analyst owns the current evidence task"}',
            '{"action":"The analyst identifies two competing explanations.","proposal_summary":"test both explanations","public_state_update":"Two hypotheses are now open","private_state_update":"needs more evidence","boundary_signal":"ready"}',
            '{"decision":"transition","guidance":"Preserve both hypotheses.","reason":"analysis can move to review","shared_state_summary":"Two hypotheses await challenge."}',
        ]
    )
    engine = WorkflowEngine(provider=provider)  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))

    from app.models import NodeRunRecord, RunEventRecord

    analyst_runs = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "analyst_1").count()
    reviewer_runs = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "reviewer_1").count()
    activation = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id, RunEventRecord.event == "nodes_activated").one()
    activation_payload = __import__("json").loads(activation.payload_json)

    assert analyst_runs == 1
    assert reviewer_runs == 0
    assert activation_payload["active_node_ids"] == ["analyst_1"]


def test_final_section_limit_does_not_truncate_simulation_turns() -> None:
    wf = WorkflowDefinition(
        id="wf_section_contract",
        name="section contract",
        nodes=[
            make_node("prince", "agent", config={"entity_name": "Prince"}),
            make_node("usurper", "agent", config={"entity_name": "Usurper"}),
        ],
        edges=[],
        entry_nodes=["prince"],
        environment=EnvironmentSpec(
            simulation={
                "execution_model": "continuous",
                "horizon_rounds": 8,
                "output_contract": {"format": "story", "max_items": 1, "unit": "section"},
            }
        ),
    )
    policy = build_loop_policy(wf)
    assert policy.max_rounds == 8
    assert policy.max_actions == 16


def test_external_agent_mock_mode() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf10",
        name="external-agent",
        nodes=[
            make_node(
                "ext1",
                "external_agent",
                config={"integration_mode": "mock", "agent_id": "custom_agent"},
                inputs={"prompt": "summarize team status"},
            )
        ],
        edges=[],
        entry_nodes=["ext1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "ext1").first()
    assert row is not None
    output = __import__("json").loads(row.output_json)
    assert output["status"] == "succeeded"
    assert output["agent_id"] == "custom_agent"


def test_entity_agent_can_use_external_execution_mode() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf10b",
        name="entity-agent-external-mode",
        nodes=[
            make_node(
                "group1",
                "agent",
                config={
                    "entity_type": "group",
                    "entity_name": "office coworkers",
                    "entity_profile": "A small office group discussing a new employee.",
                    "behavior_prompt": "You are simulating a group, not a single person.",
                    "execution_mode": "external_agent",
                    "external_integration_mode": "mock",
                },
                inputs={"prompt": "React to the rumor."},
            )
        ],
        edges=[],
        entry_nodes=["group1"],
        environment={
            "profile": "",
            "scenario": "",
            "facts": [],
            "constraints": [],
            "glossary": {},
            "simulation": {"detail_granularity": "detailed"},
        },
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "group1").first()
    assert row is not None
    output = __import__("json").loads(row.output_json)
    assert output["status"] == "succeeded"
    assert output["agent_id"] == "office coworkers"
    assert "Entity profile" in output["text"]
    assert "not a single person" in output["text"]
    assert "typical short quotes" in output["text"]


def test_entity_agent_injects_entity_specific_detail_granularity_to_builtin_llm() -> None:
    db = make_db()
    provider = CapturingProvider()
    wf = WorkflowDefinition(
        id="wf_entity_action_examples",
        name="entity-action-examples",
        nodes=[
            make_node(
                "actor1",
                "agent",
                config={
                    "entity_type": "individual",
                    "entity_name": "partner",
                    "entity_profile": "A person in an argument.",
                    "behavior_prompt": "You are simulating one concrete individual.",
                    "role": "individual",
                    "model": "mock",
                    "system_prompt": "stay embodied",
                },
                inputs={"prompt": "Respond to the argument."},
            )
        ],
        edges=[],
        entry_nodes=["actor1"],
        environment={
            "profile": "",
            "scenario": "",
            "facts": [],
            "constraints": [],
            "glossary": {},
            "simulation": {"detail_granularity": "detailed"},
        },
    )
    engine = WorkflowEngine(provider=provider)  # type: ignore[arg-type]
    __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    assert "Detail granularity" in provider.user_prompt
    assert "key spoken lines" in provider.user_prompt
    assert "private thoughts" in provider.user_prompt


def test_external_agent_injects_background_prompt() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf11",
        name="external-agent-bg",
        nodes=[
            make_node(
                "ext1",
                "external_agent",
                config={
                    "integration_mode": "mock",
                    "include_background_in_prompt": "true",
                    "prompt_template": "{{prompt}}\nENV={{environment}}\nGUIDE={{global_guidance}}",
                },
                inputs={"prompt": "do mission"},
            )
        ],
        edges=[],
        entry_nodes=["ext1"],
        environment={"profile": "modern war sim", "scenario": "urban raid", "facts": [], "constraints": [], "glossary": {}},
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "ext1").first()
    assert row is not None
    output = __import__("json").loads(row.output_json)
    assert "modern war sim" in output["text"]


def test_external_agent_bridge_local_default_endpoint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = make_db()
    called: dict[str, object] = {}

    class FakeResp:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id

        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {
                "task_id": self.task_id,
                "status": "succeeded",
                "output": {"text": "bridge ok"},
                "metrics": {"latency_ms": 5},
                "errors": [],
            }

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, headers: dict, json: dict):  # type: ignore[override]
            called["url"] = url
            called["headers"] = headers
            called["json"] = json
            return FakeResp(str(json["task_id"]))

    monkeypatch.setattr("app.execution.nodes.httpx.AsyncClient", FakeClient)

    wf = WorkflowDefinition(
        id="wf12",
        name="external-agent-bridge-local",
        nodes=[
            make_node(
                "ext1",
                "external_agent",
                config={"integration_mode": "bridge_local", "agent_id": "local_agent", "endpoint_path": "/agent/tasks"},
                inputs={"prompt": "hello bridge"},
            )
        ],
        edges=[],
        entry_nodes=["ext1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "ext1").first()
    assert row is not None
    output = __import__("json").loads(row.output_json)
    assert output["status"] == "succeeded"
    assert output["text"] == "bridge ok"
    assert called["url"] == "http://127.0.0.1:8787/agent/tasks"


def test_external_agent_rejects_non_http_endpoint() -> None:
    from app.execution.nodes import _execute_external_agent

    node = make_node(
        "ext_invalid",
        "external_agent",
        config={"integration_mode": "http", "endpoint_url": "file:///tmp/agent"},
        inputs={"prompt": "hello"},
    )
    try:
        __import__("asyncio").run(_execute_external_agent(node, {"prompt": "hello"}, {}))
        assert False, "expected endpoint validation error"
    except ValueError as exc:
        assert "http or https" in str(exc)


def test_external_agent_rejects_mismatched_task_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.execution.nodes import _execute_external_agent

    class FakeResp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {
                "task_id": "wrong-task",
                "status": "succeeded",
                "output": {"text": "should not be accepted"},
                "metrics": {},
                "errors": [],
            }

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, headers: dict, json: dict):  # type: ignore[override]
            return FakeResp()

    monkeypatch.setattr("app.execution.nodes.httpx.AsyncClient", FakeClient)
    node = make_node(
        "ext_mismatch",
        "external_agent",
        config={"integration_mode": "http", "endpoint_url": "http://127.0.0.1:8787"},
        inputs={"prompt": "hello"},
    )
    try:
        __import__("asyncio").run(_execute_external_agent(node, {"prompt": "hello"}, {}))
        assert False, "expected task correlation error"
    except ValueError as exc:
        assert "task_id" in str(exc)


def test_template_create_and_load() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_tpl",
        name="tpl-source",
        nodes=[make_node("p1", "prompt", config={"template": "hello"})],
        edges=[],
        entry_nodes=["p1"],
    )
    create_or_update_template(
        db,
        TemplateCreateRequest(
            id="tpl_1",
            name="Template One",
            description="desc",
            category="research",
            tags=["alpha", "beta"],
            workflow=wf,
        ),
    )
    rows = list_templates(db)
    assert len(rows) == 1
    detail = get_template(db, "tpl_1")
    assert detail.name == "Template One"
    assert detail.workflow.id == "wf_tpl"


def test_observability_snapshot_with_alerts() -> None:
    db = make_db()
    # Simulate request traces.
    for _ in range(30):
        perf_tracker.record_http(path="/api/test", method="GET", status_code=200, latency_ms=1300)
    for _ in range(3):
        perf_tracker.record_http(path="/api/test", method="GET", status_code=500, latency_ms=900)

    # Simulate one slow node in runtime metrics.
    wf = WorkflowDefinition(
        id="wf_obs",
        name="obs",
        nodes=[make_node("p1", "prompt", config={"template": "hello"})],
        edges=[],
        entry_nodes=["p1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id).first()
    assert row is not None
    row.duration_ms = 20000
    row.started_at = datetime.now(timezone.utc)
    db.add(row)
    db.commit()

    snap = get_observability_snapshot(db)
    assert snap.http.requests_5m >= 33
    assert snap.runtime.slow_nodes_24h >= 1
    assert len(snap.alerts) >= 1


def test_queue_manager_snapshot_defaults() -> None:
    q = RunQueueManager(worker_count=3, max_size=10)
    snap = q.snapshot()
    assert snap["queued"] == 0
    assert snap["active"] == 0
    assert snap["worker_count"] == 3


def test_memory_persisted_and_injected_to_following_agent() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_mem_1",
        name="memory-chain",
        nodes=[
            make_node("a1", "agent", config={"role": "planner", "model": "mock", "system_prompt": "plan"}, inputs={"prompt": "step one"}),
            make_node("a2", "agent", config={"role": "reviewer", "model": "mock", "system_prompt": "review"}, inputs={"prompt": "step two"}),
        ],
        edges=[WorkflowEdge(id="e1", source="a1", target="a2")],
        entry_nodes=["a1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import MemoryRecord, NodeRunRecord

    mems = db.query(MemoryRecord).filter(MemoryRecord.run_id == run_id).all()
    assert len(mems) >= 2

    row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "a2").first()
    assert row is not None
    node_input = __import__("json").loads(row.input_json)
    assert "_memory" in node_input
    assert isinstance(node_input["_memory"], list)
    assert len(node_input["_memory"]) >= 1


def test_trace_context_and_audit_verification() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_trace_1",
        name="trace-check",
        nodes=[
            make_node("a1", "prompt", config={"template": "alpha"}),
            make_node("a2", "agent", config={"role": "analyst", "model": "mock", "system_prompt": "analyze"}, inputs={"prompt": "next"}),
        ],
        edges=[WorkflowEdge(id="e1", source="a1", target="a2")],
        entry_nodes=["a1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))

    verification = verify_run_audit(db, run_id)
    assert verification.verified is True
    assert verification.total_events >= 4

    from app.models import RunEventRecord

    target = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.node_id == "a2", RunEventRecord.event == "succeeded")
        .first()
    )
    assert target is not None
    trace = get_trace_event_context(db, run_id=run_id, seq=int(target.seq))
    assert trace.chain_ok is True
    assert trace.caused_by in {"node", "scheduler", "engine"}
    assert isinstance(trace.context_snapshot, dict)
    assert trace.context_snapshot.get("node_id") == "a2"


def test_secret_fields_are_redacted_in_events_and_node_runs() -> None:
    db = make_db()
    wf = WorkflowDefinition(
        id="wf_redact_1",
        name="redact",
        nodes=[
            make_node(
                "p1",
                "prompt",
                config={"template": "hello"},
                inputs={
                    "api_key": "sk-secret",
                    "token": "token-secret-value",
                    "session_token": "session-secret-value",
                    "estimated_tokens": 321,
                    "token_budget": 500,
                    "task": "x",
                },
            )
        ],
        edges=[],
        entry_nodes=["p1"],
    )
    engine = WorkflowEngine(provider=FakeProvider())  # type: ignore[arg-type]
    run_id = __import__("asyncio").run(engine.run(db=db, workflow=wf, request=RunRequest(input={})))
    from app.models import NodeRunRecord, RunEventRecord

    event_row = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.node_id == "p1", RunEventRecord.event == "queued")
        .first()
    )
    assert event_row is not None
    payload = __import__("json").loads(event_row.payload_json)
    assert payload["input"]["api_key"] == "***REDACTED***"
    assert payload["input"]["token"] == "***REDACTED***"
    assert payload["input"]["session_token"] == "***REDACTED***"
    assert payload["input"]["estimated_tokens"] == 321
    assert payload["input"]["token_budget"] == 500
    assert "sk-secret" not in event_row.payload_json
    assert "token-secret-value" not in event_row.payload_json

    node_row = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id, NodeRunRecord.node_id == "p1").first()
    assert node_row is not None
    node_input = __import__("json").loads(node_row.input_json)
    assert node_input["api_key"] == "***REDACTED***"
    assert node_input["token"] == "***REDACTED***"
    assert node_input["session_token"] == "***REDACTED***"
    assert node_input["estimated_tokens"] == 321
    assert node_input["token_budget"] == 500
    assert "sk-secret" not in node_row.input_json
    assert "abc" not in node_row.input_json
