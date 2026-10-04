from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any


def evaluate_run(
    run_detail: dict[str, Any],
    expected: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    node_runs = run_detail.get("node_runs", []) if isinstance(run_detail, dict) else []
    actor_ids = set(str(value) for value in expected.get("actor_node_ids", []))
    actions: list[dict[str, str]] = []
    contract_failures = 0
    for item in node_runs:
        if not isinstance(item, dict) or str(item.get("node_id")) not in actor_ids or item.get("status") != "succeeded":
            continue
        output = item.get("output", {})
        if not isinstance(output, dict):
            contract_failures += 1
            continue
        action = str(output.get("action") or output.get("content") or "").strip()
        public_update = str(output.get("public_state_update") or "").strip()
        proposal = str(output.get("proposal_summary") or "").strip()
        if not action or not public_update or not proposal:
            contract_failures += 1
        if action:
            actions.append({"node_id": str(item.get("node_id")), "action": action, "public_state_update": public_update})

    normalized = [_normalize(row["action"]) for row in actions]
    unique_ratio = len(set(normalized)) / len(normalized) if normalized else 0.0
    actor_counts = Counter(row["node_id"] for row in actions)
    min_actions = max(1, int(expected.get("min_actions", 1)))
    min_actors = max(1, int(expected.get("min_active_actors", 1)))
    checks = {
        "run_succeeded": run_detail.get("status") == "succeeded",
        "minimum_actions": len(actions) >= min_actions,
        "multiple_actors_participated": len(actor_counts) >= min_actors,
        "action_contract_complete": contract_failures == 0 and bool(actions),
        "actions_not_repetitive": unique_ratio >= float(expected.get("min_unique_action_ratio", 0.7)),
    }
    if expected.get("require_director_completion"):
        audits = [event for event in events or [] if event.get("event") == "episode_audited"]
        last_decision = str((audits[-1].get("payload") or {}).get("decision", "")) if audits else ""
        checks["director_confirmed_completion"] = last_decision == "stop"
    dynamic_expected = expected.get("dynamic_state", {})
    dynamic_stats: dict[str, Any] = {}
    if isinstance(dynamic_expected, dict) and dynamic_expected:
        simulation = run_detail.get("output", {}).get("simulation", {}) if isinstance(run_detail.get("output"), dict) else {}
        dynamic = simulation.get("dynamic_state", {}) if isinstance(simulation, dict) else {}
        concepts = dynamic.get("concepts", {}) if isinstance(dynamic, dict) else {}
        values = dynamic.get("values", {}) if isinstance(dynamic, dict) else {}
        active = {
            key: value
            for key, value in concepts.items()
            if isinstance(value, dict) and value.get("status", "active") == "active"
        } if isinstance(concepts, dict) else {}
        max_active = max(0, int(dynamic_expected.get("max_active_concepts", 6)))
        min_active = max(0, int(dynamic_expected.get("min_active_concepts", 1)))
        checks["dynamic_state_captured_durable_facts"] = len(active) >= min_active
        checks["dynamic_state_within_budget"] = len(active) <= max_active
        checks["dynamic_state_values_complete"] = all(concept_id in values for concept_id in active)
        checks["dynamic_state_has_exact_sources"] = all(
            isinstance(values.get(concept_id), dict)
            and bool(values[concept_id].get("source_refs"))
            and all(
                isinstance(ref, dict)
                and str(ref.get("node_id", "")).strip()
                and int(ref.get("action_index", 0) or 0) > 0
                and int(ref.get("event_seq", 0) or 0) > 0
                for ref in values[concept_id].get("source_refs", [])
            )
            for concept_id in active
        )
        checks["dynamic_private_ownership_valid"] = all(
            concept.get("visibility") != "private"
            or (
                str(concept.get("owner_id", "")).strip()
                and isinstance(values.get(concept_id), dict)
                and str(concept.get("owner_id")) in values[concept_id].get("source_node_ids", [])
            )
            for concept_id, concept in active.items()
        )
        forbidden = [str(term).casefold() for term in dynamic_expected.get("forbidden_transient_terms", [])]
        state_text = json.dumps({"concepts": active, "values": values}, ensure_ascii=False).casefold()
        checks["dynamic_state_avoids_transient_details"] = not any(term in state_text for term in forbidden)
        dynamic_stats = {
            "active_concept_count": len(active),
            "concept_ids": sorted(active),
            "rejected_transient_terms": [term for term in forbidden if term in state_text],
        }
    score = round(sum(1 for passed in checks.values() if passed) / len(checks) * 100, 1)
    findings: list[str] = []
    if not checks["run_succeeded"]:
        findings.append("Run did not finish successfully.")
    if not checks["minimum_actions"]:
        findings.append(f"Only {len(actions)} actor actions were produced; expected at least {min_actions}.")
    if not checks["multiple_actors_participated"]:
        findings.append("Too few distinct actors participated.")
    if not checks["action_contract_complete"]:
        findings.append(f"{contract_failures} actor outputs missed required action-contract fields.")
    if not checks["actions_not_repetitive"]:
        findings.append(f"Action uniqueness ratio is {unique_ratio:.2f}.")
    if checks.get("director_confirmed_completion") is False:
        findings.append("The run ended before the Director confirmed that the scenario completion condition was met.")
    if checks.get("dynamic_state_within_budget") is False:
        findings.append("Dynamic State retained more active concepts than the scenario budget allows.")
    if checks.get("dynamic_state_captured_durable_facts") is False:
        findings.append("Dynamic State did not retain the minimum durable facts required by the scenario.")
    if checks.get("dynamic_state_has_exact_sources") is False:
        findings.append("At least one Dynamic State value lacks an exact action/event source reference.")
    if checks.get("dynamic_state_values_complete") is False:
        findings.append("At least one active Dynamic State concept has no authoritative current value.")
    if checks.get("dynamic_private_ownership_valid") is False:
        findings.append("At least one private Dynamic State value is not sourced from its owner.")
    if checks.get("dynamic_state_avoids_transient_details") is False:
        findings.append("Dynamic State retained transient psychological, stylistic, or gesture detail.")
    return {
        "score": score,
        "checks": checks,
        "findings": findings,
        "stats": {
            "action_count": len(actions),
            "active_actor_count": len(actor_counts),
            "actions_per_actor": dict(actor_counts),
            "unique_action_ratio": round(unique_ratio, 3),
            "contract_failures": contract_failures,
            "dynamic_state": dynamic_stats,
        },
        "actions": actions,
    }


async def judge_run(provider: Any, rubric: dict[str, Any], full_log: str) -> dict[str, Any]:
    prompt = (
        "Evaluate this fixed multi-agent simulation. Return JSON only with keys: "
        "verdict (pass|mixed|fail), overall_score (0-100), role_consistency (0-10), "
        "progression (0-10), node_autonomy (0-10), continuity (0-10), readability (0-10), "
        "state_precision (0-10), provenance (0-10), "
        "strengths (array), problems (array), recommendation (string). "
        "Do not reward fluent prose if actors do not make autonomous, state-changing decisions.\n\n"
        f"RUBRIC:\n{json.dumps(rubric, ensure_ascii=False)}\n\nFULL LOG:\n{full_log[:24000]}"
    )
    response = await provider.chat(
        model="",
        system_prompt="You are a strict regression evaluator for a distributed multi-agent simulation engine.",
        user_prompt=prompt,
    )
    return _extract_json(str(response.get("content", "")))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.lower())).strip()


def _extract_json(raw: str) -> dict[str, Any]:
    candidate = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else {"error": "judge returned non-object JSON", "raw": raw[:2000]}
    except Exception:  # noqa: BLE001
        match = re.search(r"\{[\s\S]*\}", candidate)
        if match:
            try:
                value = json.loads(match.group(0))
                if isinstance(value, dict):
                    return value
            except Exception:  # noqa: BLE001
                pass
    return {"error": "judge returned invalid JSON", "raw": raw[:2000]}
