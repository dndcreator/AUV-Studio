import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.evaluation.budget import BudgetedProvider, EvalBudget
from app.evaluation.full_batch_trial import main, trial
from app.evaluation.parallel_trial import workflow
from app.execution.engine import WorkflowEngine
from app.models import MemoryRecord, RunRecord
from app.schemas import RunRequest, WorkflowNode
from app.service import create_workflow, get_events, verify_run_audit


class Provider:
    def __init__(self, reject=False):
        self.inputs = []
        self.audits = 0
        self.reject = reject

    async def chat(self, model, system_prompt, user_prompt):
        if "process architect" in system_prompt:
            data = {"goal": "Agree a route", "initial_state": "Two couriers in a hall.", "intent_mode": "roleplay",
                    "completion_condition": "Both agree on north.",
                    "roles": [{"name": "Mara", "objective": "Choose route"}, {"name": "Iven", "objective": "Share north route information"}]}
        elif "route attention" in system_prompt:
            nodes = json.loads(user_prompt.split("AVAILABLE NODES:\n")[1])
            data = {"active_node_ids": [node["node_id"] for node in nodes]}
        elif "BATCH ADMISSION" in system_prompt:
            request = json.loads(user_prompt.split("\nRepair", 1)[0])
            data = {"assessments": {item["actor_id"]: {"decision": "accept", "reason": "Fixture.",
                "scope": {"self_owned": True, "public_only": True}, "findings": []}
                for item in request["CANDIDATES"]}, "joint_findings": []}
            if self.reject:
                for item in data["assessments"].values():
                    item.update(decision="uncertain", findings=[{"category": "causality", "reason": "Fixture uncertainty.",
                        "evidence_ids": [request["EVIDENCE"][0]["id"]]}])
        elif "continuity auditor" in system_prompt:
            self.audits += 1
            data = {"decision": "continue", "task_complete": False,
                    "state_patch": {"schema_ops": [], "state_ops": []}}
        elif "autonomous participant" in user_prompt:
            packet, _ = json.JSONDecoder().raw_decode(user_prompt.split("CONTEXT:\n")[1])
            self.inputs.append(packet)
            await asyncio.sleep(.01)
            data = {"participation": "act", "action": "I propose taking the north route.", "intent": "PRIVATE-INTENT"}
        else:
            data = {"content": "Short actual summary."}
        return {"content": json.dumps(data)}


def run(provider, *, unsupported=False):
    wf = workflow("castle")
    wf.nodes.append(WorkflowNode(id="director_1", type="director", position={"x": 0, "y": 0}))
    wf.entry_nodes = ["director_1"]
    wf.environment.simulation.update(horizon_rounds=2, director_interval_rounds=1)
    if unsupported:
        wf.nodes[0].config["execution_mode"] = "external_agent"
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        create_workflow(db, wf)
        rid = asyncio.run(WorkflowEngine(provider, experimental_batch=True).run(db, wf, RunRequest(input={})))
        record = db.get(RunRecord, rid)
        result = (record.status, json.loads(record.error_json), get_events(db, rid, after_seq=0, limit=10000).events,
                  db.query(MemoryRecord).filter_by(run_id=rid).count(), verify_run_audit(db, rid))
    engine.dispose()
    return result


def test_real_batch_engine_uses_frozen_inputs_memory_director_coder_and_audit_chain():
    provider = Provider()
    status, error, events, memories, audit = run(provider)
    assert status != "failed", error
    assert provider.audits == 2 and memories >= 4 and audit.verified
    assert sum(event.event == "action_batch_checked" for event in events) == 2
    assert sum(event.event == "dynamic_state_updated" for event in events) == 2
    for index in (0, 2):
        assert len(provider.inputs[index]["recent_actions"]) == len(provider.inputs[index + 1]["recent_actions"]) == index
    assert provider.inputs[2]["memory"] and provider.inputs[3]["memory"]
    assert "PRIVATE-INTENT" not in json.dumps(provider.inputs[3]["recent_actions"][0])


def test_uncertain_batch_candidates_never_enter_memory_or_success_events():
    status, error, events, memories, _ = run(Provider(reject=True))
    assert status != "failed", error
    assert memories == 0
    assert not any(event.event == "succeeded" and "action" in event.payload.get("output", {}) for event in events)


def test_batch_explicitly_refuses_external_agent_execution():
    provider = Provider()
    status, error, _, memories, _ = run(provider, unsupported=True)
    assert status == "failed" and "builtin" in error["message"] and not provider.inputs and memories == 0


def test_full_trial_preflight_and_planning_deadline_never_start_extra_calls(capsys):
    assert main([]) == 0 and "Zero-call" in capsys.readouterr().out
    class Waiting:
        async def chat(self, **kwargs):
            await asyncio.sleep(100)
    provider = BudgetedProvider(Waiting(), EvalBudget(), capture_trace=True)
    result = asyncio.run(trial(provider, timeout=.02))
    assert result["status"] == "timeout" and provider.usage.calls == 1
    assert result["actions"] == [] and provider.inflight == {"input_tokens": 0, "output_tokens": 0}


def test_full_trial_reuses_compiler_engine_and_persistence_without_fake_success():
    provider = BudgetedProvider(Provider(), EvalBudget(max_calls=24, max_total_tokens=80000), capture_trace=True)
    result = asyncio.run(trial(provider))
    assert result["compiled"] and result["status"] not in {"error", "failed", "timeout"}, result.get("error")
    assert len(result["actions"]) == 6 and result["memory_records"] >= 6
    assert result["mechanisms"]["episode_audited"] == result["mechanisms"]["dynamic_state_updated"] == 3
    assert result["output"]["completion"]["status"] != "completed"


def test_full_trial_cancels_inflight_actors_before_marking_stopped():
    class WaitingActor(Provider):
        async def chat(self, **kwargs):
            if "autonomous participant" in kwargs["user_prompt"]:
                await asyncio.sleep(100)
            return await super().chat(**kwargs)
    provider = BudgetedProvider(WaitingActor(), EvalBudget(max_calls=24, max_total_tokens=80000), capture_trace=True)
    result = asyncio.run(trial(provider, timeout=.15))
    assert result["status"] == "timeout" and result["run_status"] == "stopped"
    assert not result["actions"] and result["memory_records"] == 0
    assert provider.inflight == {"input_tokens": 0, "output_tokens": 0}
