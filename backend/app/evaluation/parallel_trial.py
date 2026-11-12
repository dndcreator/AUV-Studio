"""Isolated Actor/admission comparison. No production scheduler or state writes."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..db import Base
from ..execution.action_consistency import assess_candidate
from ..execution.batch_admission import JOINT, parse_joint, check_joint
from ..execution.action_evidence import build_consistency_evidence
from ..execution.action_output import normalize_action_output
from ..execution.dynamic_state import simulation_state_for_node
from ..execution.events import emit_event
from ..execution.nodes import execute_node
from ..execution.perception import actions_for_node, public_action
from ..execution.provider import safe_provider_diagnostics
from ..execution.simulation_loop import action_instruction, build_role_packet
from ..execution.simulation_state import init_simulation_state
from ..schemas import WorkflowDefinition
from ..models import RunRecord, WorkflowRecord
from .budget import BudgetedProvider, EvalBudget, EvalBudgetExceeded
from .judging import strict_object
from .profiling import cost_profile

MODES = ("serial", "parallel_propose", "parallel_batch")
STORY_MODES = ("parallel_propose", "parallel_batch")
CASTLE = (
    "Western dark fantasy, one short escape scene, written in Chinese with vivid but concise dialogue and physical detail. "
    "Mara (a), a fugitive knight, and Iven (b), an archivist, stand together in a castle gatehouse. "
    "Guards are searching the castle; neither guard actions nor a precise countdown have yet occurred. "
    "Mara holds the only brass key, which opens the hatch over a dry escape culvert. "
    "The hatch is closed and locked. Its winch has a safety catch; its operation is not common knowledge. "
    "The hatch requires two coordinated people to operate; neither can do it alone. Both see the hatch and winch. "
    "Iven carries the only sealed letter proving the king's crime. Mara does not know its contents. "
    "The goal is to negotiate the dependencies and attempt escape together. "
    "Only accepted public actions in earlier rounds are observable. A proposal this round is not a delivered message. "
    "Do not invent extra keys, exits, helpers, abilities or a solved mechanism. "
    "Four rounds are an observation window, not proof that the characters must succeed."
)
def workflow(case):
    scenario = {
        "independent": "Two technicians independently report the status of separate consoles. Both consoles are green.",
        "contested": "Two people want the only key on a table. The key is indivisible and has no owner. Neither has priority.",
        "reaction": "A asks B whether a door is locked. B can answer only after receiving A's question in a previous accepted event.",
        "castle": CASTLE,
    }[case]
    wf = WorkflowDefinition.model_validate({"id": "parallel_trial", "name": case, "nodes": [
        {"id": name, "type": "agent", "position": {"x": 0, "y": 0}, "config": {"role": "custom",
         "entity_name": name, "entity_type": "individual", "entity_profile": "Independent participant; control only yourself.",
         "system_prompt": "You are an autonomous participant. Write one concise self-owned contribution in Chinese. "
                          "Follow the supplied context and JSON action contract."}} for name in ("a", "b")],
        "edges": [], "environment": {"scenario": scenario, "facts": [scenario],
            "constraints": ["An attempt does not establish another actor's response or a contested shared outcome."],
            "simulation": {"execution_model": "continuous", "dynamic_state": {"enabled": True}}}})
    if case == "castle":
        profiles = {
            "a": ("Mara", "You are Mara, a tired knight. You hold the brass key and want to flee before discovery. "
                  "You do not know how to operate the safety catch or the letter's contents. "
                  "You distrust political causes, but will not knowingly abandon Iven. Decide your own words and attempts."),
            "b": ("Iven", "You are Iven, an archivist. You know the safety catch must be held while someone else turns "
                  "the winch, after the key unlocks the hatch. You carry a sealed letter proving the king ordered "
                  "Mara's brother's execution; Mara has not been told this. You want both to escape, but refuse "
                  "to surrender or destroy the only letter. Decide whether and when to disclose it; do not assume Mara knows."),
        }
        for node in wf.nodes:
            node.config.update(entity_name=profiles[node.id][0], entity_profile=profiles[node.id][1])
    return wf


def packet(wf, node, state, actions, round_index):
    visible = actions_for_node(actions, node.id, wf.edges)
    context = {"round": round_index, "action_index": len(actions) + 1, "state": simulation_state_for_node(state, node.id),
        "recent_actions": visible, "memory": [], "background": {"entries": []},
        "environment": {"scenario": wf.environment.scenario},
        "scene_contract": {"goal": wf.environment.scenario, "detail": "concise", "progress_scale": "micro"}}
    role = build_role_packet(node, [])
    return {"context": context, "role": role, "_participant_turn": True,
            "prompt": action_instruction(context, role)}


async def propose(provider, node, runtime):
    response = await execute_node(node, runtime, {}, provider)
    candidate = normalize_action_output(response, round_index=runtime["context"]["round"], action_index=1)
    fields = ("participation", "action", "intent", "observation", "self_update", "turn_delta", "shared_effect_claim", "episode_signal")
    return {"actor_id": node.id, **{key: candidate[key] for key in fields}}


async def run_mode(provider, mode, case="independent"):
    wf = workflow(case); state = init_simulation_state(wf)
    ledger, outputs, proposals = [], [], []
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    start = perf_counter(); first = None
    row = {"mode": mode, "case": case, "status": "running"}
    before = provider.usage.calls
    phase = "setup"
    round_index = 0
    try:
        with sessionmaker(bind=engine)() as db:
            db.add(WorkflowRecord(id=wf.id, name=wf.name, version=1, definition_json=wf.model_dump_json()))
            db.add(RunRecord(id="trial", workflow_id=wf.id, status="running")); db.commit()
            for round_index in range(1, 5 if case == "castle" else 3 if case == "reaction" else 2):
                inputs = [packet(wf, node, state, ledger, round_index) for node in wf.nodes]
                if mode != "serial":
                    phase = "actor_proposals"
                    results = await asyncio.gather(*(propose(provider, node, runtime) for node, runtime in zip(wf.nodes, inputs)), return_exceptions=True)
                    error = next((item for item in results if isinstance(item, BaseException)), None)
                    if error: raise error
                    candidates = results
                    phase = "joint_check"
                    verdicts = await check_joint(provider, wf, state, ledger, inputs, candidates) if mode == "parallel_batch" else None
                for index, node in enumerate(wf.nodes):
                    runtime = packet(wf, node, state, ledger, round_index) if mode == "serial" else inputs[index]
                    phase = "actor_proposal" if mode == "serial" else "admission"
                    candidate = await propose(provider, node, runtime) if mode == "serial" else candidates[index]
                    proposals.append({"actor": node.id, "round": round_index,
                                      "received": [item["event_seq"] for item in runtime["context"]["recent_actions"]]})
                    assessment = verdicts[node.id] if mode == "parallel_batch" else await assess_candidate(
                        provider=provider, candidate=candidate,
                        evidence=build_consistency_evidence(wf, state, ledger, runtime), record=lambda item: row.setdefault("check_errors", []).append({
                            "actor": node.id, "round": round_index, "assessment": item["assessment"],
                            "provider_diagnostics": item.get("provider_diagnostics", {}),
                        }) if item["assessment"]["decision"] == "error" else None)
                    outputs.append({"actor": node.id, "round": round_index, "decision": assessment["decision"],
                                    "action": candidate["action"], "reason": assessment["reason"]})
                    if assessment["decision"] != "accept": continue
                    seq, _ = emit_event(db, "trial", node.id, "succeeded", {"output": candidate}, caused_by="isolated_trial_admission")
                    ledger.append({"node_id": node.id, "actor": node.id, "event_seq": seq, "action_index": len(ledger) + 1,
                        "round": round_index, **{key: candidate[key] for key in candidate if key != "actor_id"}})
                    if first is None: first = round((perf_counter() - start) * 1000)
        row["status"] = "completed"
    except (Exception, asyncio.CancelledError) as exc:
        error = {"type": type(exc).__name__, "code": getattr(exc, "code", "trial_error"),
                 "phase": phase, "round": round_index}
        if hasattr(exc, "issues"):
            error["issues"] = exc.issues
        cause = exc.__cause__
        if cause is not None:
            error["cause"] = {"type": type(cause).__name__, "code": getattr(cause, "code", None),
                              "provider_diagnostics": safe_provider_diagnostics(getattr(cause, "diagnostics", None))}
        row.update(status="error", error=error)
        if isinstance(exc, (EvalBudgetExceeded, asyncio.CancelledError)): row["stop_campaign"] = True
    finally: engine.dispose()
    row.update(wall_ms=round((perf_counter() - start) * 1000), first_accepted_ms=first,
        calls=provider.usage.calls - before, accepted=[public_action(item) for item in ledger],
        request_range={"start": before + 1, "end": provider.usage.calls},
        assessments=outputs, inputs=proposals, quality="not_independently_judged")
    return row


class DelayedProvider:
    """Explicit test double: delays demonstrate overlap, never actual service speed."""
    def __init__(self, case): self.case = case
    async def chat(self, model, system_prompt, user_prompt):
        checking = "action consistency checker" in system_prompt
        await asyncio.sleep(.04 if checking else .06)
        accept = {"decision": "accept", "reason": "Offline oracle.", "scope": {"self_owned": True, "public_only": True}, "findings": []}
        if checking:
            data = json.loads(user_prompt.split("\nRepair", 1)[0])
            if "CANDIDATES" in data:
                verdicts = {item["actor_id"]: copy.deepcopy(accept) for item in data["CANDIDATES"]}
                findings = []
                if self.case == "contested":
                    for verdict in verdicts.values():
                        verdict.update(decision="uncertain", findings=[{"category": "causality", "reason": "One key; no priority.", "evidence_ids": [data["EVIDENCE"][0]["id"]]}])
                    findings = [{"candidate_ids": ["a", "b"], "reason": "One key.", "evidence_ids": [data["EVIDENCE"][0]["id"]]}]
                result = {"assessments": verdicts, "joint_findings": findings}
            else:
                result = copy.deepcopy(accept)
                if self.case == "contested" and any(item["id"].startswith("event.") for item in data["EVIDENCE"]):
                    result.update(decision="uncertain", findings=[{"category": "causality", "reason": "One key.", "evidence_ids": [data["EVIDENCE"][0]["id"]]}])
        else:
            actor = json.loads(user_prompt.split("ROLE:\n", 1)[1])["node_id"]
            context, _ = json.JSONDecoder().raw_decode(user_prompt.split("CONTEXT:\n", 1)[1])
            action = actor + " reports its console."
            if self.case == "contested": action = "I take the only key."
            if self.case == "reaction":
                action = "Is the door locked?" if actor == "a" else (
                    "The door is locked." if any(item["node_id"] == "a" for item in context["recent_actions"]) else "")
            result = {"participation": "act" if action else "wait", "action": action}
        return {"content": json.dumps(result)}


async def campaign(provider, cases, timeout, modes=MODES):
    rows = [{"case": case, "mode": mode, "status": "not_run", "wall_ms": None,
             "first_accepted_ms": None, "calls": 0} for case in cases for mode in modes]
    deadline = asyncio.get_running_loop().time() + timeout
    for index, pending in enumerate(rows):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0: break
        try: row = await asyncio.wait_for(run_mode(provider, pending["mode"], pending["case"]), remaining)
        except TimeoutError: break
        rows[index] = row
        if row.get("stop_campaign"): break
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--thinking-mode", choices=["disabled"], default=None,
                        help="Explicit opt-in for this experiment only; never changes saved settings.")
    parser.add_argument("--case", choices=["independent", "contested", "reaction", "castle"], default="independent")
    args = parser.parse_args(argv)
    if args.execute and args.offline: parser.error("choose offline or execute, not both")
    if args.thinking_mode and args.case != "castle": parser.error("thinking comparison requires the castle case")
    story = args.case == "castle"
    modes = STORY_MODES if story else MODES
    timeout = 480 if story else 300
    budget = (EvalBudget(max_calls=40, max_total_tokens=160000, max_output_tokens_per_call=2400, max_cost_usd=.30)
              if story else EvalBudget(max_calls=24, max_total_tokens=80000, max_output_tokens_per_call=900, max_cost_usd=.10))
    if not args.execute and not args.offline:
        print("Zero-call preflight: 2 parallel modes x 4 castle rounds; 28 ordinary requests; caps40 calls/160k tokens/USD0.30/480s; output2400; "
              + ("Thinking disabled." if args.thinking_mode else "Thinking unchanged.")
              if story else "Zero-call preflight: 3 modes, 11 ordinary requests plus bounded checker protocol repairs; caps24 calls/80k tokens/USD0.10/300s.")
        return 0
    if args.offline: raw = DelayedProvider(args.case)
    else:
        from ..db import SessionLocal
        from ..execution.provider import build_provider, provider_accepts_keyless
        from ..model_config_service import apply_stored_model_config
        raw = build_provider()
        with SessionLocal() as db: apply_stored_model_config(db, raw)
        if not raw.api_key and not provider_accepts_keyless(raw.provider_kind, raw.base_url): raise ValueError("model not configured")
        if args.thinking_mode:
            if raw.provider_kind == "ollama":
                raise ValueError("This experiment does not implement Ollama's native thinking toggle")
            raw.thinking_mode = args.thinking_mode
    provider = BudgetedProvider(raw, budget, capture_trace=True)
    rows = asyncio.run(campaign(provider, [args.case], timeout, modes))
    record = {"created_at": datetime.now(timezone.utc).isoformat(), "source": "fake_delays" if args.offline else "real_model",
        "model": {"name": getattr(raw, "default_model", "offline"), "thinking_mode": getattr(raw, "thinking_mode", None)},
        "scope": "Actor/admission microbenchmark; excludes Planning, Router, Director, State Coder, tools and external Agents.",
        "rows": rows, "usage": provider.usage.model_dump(budget), "requests": provider.request_trace,
        "budget": {"max_calls": budget.max_calls, "max_total_tokens": budget.max_total_tokens,
                   "max_output_tokens_per_call": budget.max_output_tokens_per_call,
                   "max_estimated_usd": budget.max_cost_usd, "deadline_seconds": timeout},
        "limitations": ["One sample per mode; serial sees accepted peers while parallel proposals do not.",
                        "Batch checker reuses checker rules but has a new experimental joint-output contract.",
                        "No full-world state transition or independent semantic Judge; completion is not a quality pass."]}
    record["profile"] = cost_profile(record)
    filename = ("parallel_story_offline_latest.json" if args.offline else "parallel_story_latest.json") if story else (
        "parallel_offline_latest.json" if args.offline else "parallel_real_latest.json")
    if args.thinking_mode:
        filename = filename.replace("_latest.json", "_nonthinking_latest.json")
    target = Path(__file__).resolve().parents[2] / "data" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"source": record["source"], "rows": [{key: row[key] for key in ("mode", "status", "wall_ms", "first_accepted_ms", "calls")} for row in rows]}))
    return 0 if len(rows) == len(modes) and all(row["status"] == "completed" for row in rows) else 1


if __name__ == "__main__": raise SystemExit(main())
