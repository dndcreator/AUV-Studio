"""One explicitly authorized, 60-second full-engine batch trial; no implicit model calls."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..db import Base
from ..execution.engine import WorkflowEngine
from ..execution.events import emit_event
from ..execution.perception import public_action
from ..models import MemoryRecord, RunRecord, RunEventRecord
from ..plan_compiler import compile_workflow_from_plan
from ..schemas import PlanCompileRequest, RunRequest
from ..service import create_workflow, get_events, get_run
from .budget import BudgetedProvider, EvalBudget

PLAN = (
    "中文西方奇幻短场景，只要两位自主角色，禁止新增演员：信使玛拉急于带信离城，最初主张走南门；"
    "同伴伊文刚亲眼看见南门被守卫封锁、北门仍可通行，玛拉尚未得知。两人已在同一无人门廊，"
    "没有物理障碍，不需要战斗或远行。先各自表达意见，传递信息，修正计划；"
    "终点是两人分别明确同意改走北门，未表达的同意不算。不要预写台词、替另一角色决定或添加新危机。"
    "角色需要推进而不是反复要求保证；导演只审计进展和是否达到终点，不代角色同意。"
    "保留具体对白与动作，但不是长篇小说，不要求最低字数。"
)


async def trial(provider, *, timeout=60):
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    record = {"status": "not_started", "scope": "compiled workflow -> real experimental batch engine",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "budget": {"max_calls": 24, "max_total_tokens": 80000, "max_estimated_usd": .15, "deadline_seconds": timeout},
              "mechanisms": {}, "actions": [], "events": []}
    started = perf_counter()
    with sessionmaker(bind=database)() as db:
        async def execute():
            compiled = await compile_workflow_from_plan(provider, PlanCompileRequest(plan_text=PLAN, mode="roleplay", max_agents=2))
            workflow = compiled.workflow
            workflow.environment.simulation.update(execution_model="continuous", horizon_rounds=3,
                output_contract={"unit": "action", "max_items": 6}, director_interval_rounds=1,
                dynamic_state={"enabled": True})
            record["compiled"] = True
            record["roles"] = [{"id": node.id, "name": node.config.get("entity_name"),
                                "profile": node.config.get("entity_profile")} for node in workflow.nodes
                               if node.type == "agent" and not node.id.startswith(("report_", "summary_"))]
            if not any(node.type == "director" for node in workflow.nodes):
                raise ValueError("Compiled workflow has no Director; full-chain trial cannot proceed")
            create_workflow(db, workflow)
            await WorkflowEngine(provider, experimental_batch=True).run(db, workflow, RunRequest(input={}), run_id="full_batch_trial")
            detail = get_run(db, "full_batch_trial")
            record.update(status=detail.status, output=detail.output, error=detail.error)

        try:
            await asyncio.wait_for(execute(), timeout)
        except TimeoutError:
            record["status"] = "timeout"
        except Exception as exc:
            record.update(status="error", error={"type": type(exc).__name__, "code": getattr(exc, "code", None)})
        finally:
            record["wall_ms"] = round((perf_counter() - started) * 1000)
            run = db.get(RunRecord, "full_batch_trial")
            if run:
                events = get_events(db, run.id, after_seq=0, limit=10000).events
                actions = []
                for event in events:
                    payload = event.payload
                    if event.event == "succeeded" and isinstance(payload.get("output"), dict) and "action" in payload["output"]:
                        actions.append(public_action({**payload["output"], "node_id": event.node_id, "event_seq": event.seq}))
                    if event.event in {"episode_audited", "dynamic_state_updated", "action_batch_checked", "action_checked", "nodes_activated"}:
                        record["events"].append({"seq": event.seq, "node_id": event.node_id, "event": event.event,
                            "details": {key: payload[key] for key in ("round", "assessment", "assessments", "decision", "task_complete",
                                        "completion_reason", "progress_assessment", "coding", "rejected", "version") if key in payload}})
                    record["mechanisms"][event.event] = record["mechanisms"].get(event.event, 0) + 1
                record["actions"] = actions
                for saved in db.query(RunEventRecord).filter_by(run_id=run.id).order_by(RunEventRecord.seq.desc()):
                    snapshot = json.loads(saved.payload_json).get("_rollback_snapshot")
                    if isinstance(snapshot, dict):
                        record["checkpoint_state"] = snapshot.get("simulation_state", {})
                        break
                record["memory_records"] = db.query(MemoryRecord).filter_by(run_id=run.id).count()
                if record["status"] == "timeout":
                    # Cancellation has settled before this synchronous finalization; never keep a running orphan.
                    run.status = "stopped"
                    run.ended_at = datetime.now(timezone.utc)
                    run.output_json = json.dumps({"actions": actions, "simulation": record.get("checkpoint_state", {}),
                        "completion": {"status": "incomplete", "reason": "trial_deadline"}}, ensure_ascii=False)
                    db.add(run)
                    emit_event(db, run.id, "run_control", "stopped", {"reason": "trial_deadline"}, caused_by="evaluation_deadline")
                record["run_status"] = run.status
            record["usage"] = provider.usage.model_dump(provider.budget)
            record["requests"] = provider.request_trace
    database.dispose()
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print("Zero-call preflight: full engine batch; non-thinking; 24 requests/80k tokens/USD0.15 estimate/60 seconds from planning.")
        return 0
    from ..db import SessionLocal
    from ..execution.provider import build_provider, provider_accepts_keyless
    from ..model_config_service import apply_stored_model_config
    raw = build_provider()
    with SessionLocal() as db:
        apply_stored_model_config(db, raw)
    if not raw.api_key and not provider_accepts_keyless(raw.provider_kind, raw.base_url):
        raise ValueError("Real provider required")
    if raw.provider_kind == "ollama":
        raise ValueError("Native Ollama thinking toggle is not implemented in this experiment")
    raw.thinking_mode = "disabled"
    provider = BudgetedProvider(raw, EvalBudget(max_calls=24, max_total_tokens=80000,
        max_output_tokens_per_call=2400, planning_output_tokens=2400, max_cost_usd=.15), capture_trace=True)
    record = asyncio.run(trial(provider))
    target = Path(__file__).resolve().parents[2] / "data" / "full_batch_latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: record.get(key) for key in ("status", "wall_ms", "mechanisms", "memory_records", "usage")}))
    return 0 if record["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
