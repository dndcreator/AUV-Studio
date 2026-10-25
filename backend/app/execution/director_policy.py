from __future__ import annotations

import re
from typing import Any

from ..schemas import WorkflowDefinition, WorkflowNode
from .output_utils import output_text


def parse_preflight_confirm_policy(workflow: WorkflowDefinition) -> dict[str, Any]:
    simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
    raw = simulation.get("preflight_confirm", {})
    if not isinstance(raw, dict):
        raw = {}
    expensive_node_types = raw.get("expensive_node_types", ["agent", "external_agent", "director"])
    if not isinstance(expensive_node_types, list):
        expensive_node_types = ["agent", "external_agent", "director"]
    return {
        "enabled": bool(raw.get("enabled", False)),
        "warmup_nodes": max(0, int(raw.get("warmup_nodes", 1) or 1)),
        "min_remaining_expensive_nodes": max(1, int(raw.get("min_remaining_expensive_nodes", 1) or 1)),
        "expensive_node_types": [str(node_type) for node_type in expensive_node_types if str(node_type).strip()],
        "question": str(raw.get("question", "请确认当前方向是否正确，再继续高成本执行。")).strip(),
    }


def parse_director_quality_policy(workflow: WorkflowDefinition) -> dict[str, Any]:
    simulation = workflow.environment.simulation if isinstance(workflow.environment.simulation, dict) else {}
    raw = simulation.get("director_quality_control", {})
    if not isinstance(raw, dict):
        raw = {}
    mode = str(simulation.get("mode", "")).strip().lower()
    threshold = float(raw.get("threshold", 0.45 if mode == "roleplay" else 0.4) or 0.4)
    return {
        "enabled": bool(raw.get("enabled", True)),
        "interval_nodes": max(1, int(raw.get("interval_nodes", 2) or 2)),
        "threshold": max(0.0, min(1.0, threshold)),
        "mode": mode,
    }


def assess_output_quality(
    *,
    node: WorkflowNode,
    runtime_input: dict[str, Any],
    output: dict[str, Any],
    simulation: dict[str, Any],
) -> dict[str, Any]:
    text = output_text(output)
    lowered = text.lower()
    mode = str(simulation.get("mode", "")).strip().lower()
    granularity = str(simulation.get("detail_granularity", "concise")).strip().lower()
    word_count = len(re.findall(r"\S+", text))
    interactions = runtime_input.get("_interactions", [])
    interaction_count = len(interactions) if isinstance(interactions, list) else 0

    score = 0.0
    reasons: list[str] = []
    if text.strip():
        score += 0.25
    else:
        reasons.append("empty_output")
    if any(token in lowered for token in ["because", "therefore", "however", "risk", "evidence", "assumption", "原因", "因此", "但是", "风险", "证据", "假设"]):
        score += 0.2
    if any(token in lowered for token in ["decide", "conflict", "change", "pressure", "tension", "turning", "决定", "冲突", "变化", "压力", "紧张", "转折"]):
        score += 0.2
    if any(character in text for character in ['"', "'", "“", "”", "：", ":"]):
        score += 0.1
    if interaction_count > 0:
        score += 0.1
    if node.type in {"agent", "external_agent"} and str(node.config.get("entity_type", "")):
        score += 0.05
    if mode == "roleplay" or granularity == "detailed":
        if any(token in lowered for token in ["said", "looked", "felt", "moved", "asked", "answered", "说", "看", "感到", "走", "问", "回答"]):
            score += 0.15
        else:
            reasons.append("lacks_concrete_scene_action")
    if mode == "research":
        if any(token in lowered for token in ["finding", "evidence", "confidence", "limitation", "recommend", "发现", "证据", "置信", "局限", "建议"]):
            score += 0.15
        else:
            reasons.append("lacks_research_structure")
    return {"score": round(max(0.0, min(1.0, score)), 4), "reasons": reasons, "word_count": word_count}


def build_quality_guidance(*, node: WorkflowNode, assessment: dict[str, Any], simulation: dict[str, Any]) -> str:
    mode = str(simulation.get("mode", "")).strip().lower()
    reasons = ", ".join(str(reason) for reason in assessment.get("reasons", [])) or "quality_below_threshold"
    if mode == "roleplay":
        return (
            f"[Director Quality Correction] Node {node.id} output is too flat ({reasons}). "
            "Continue the simulation with concrete scene actions, specific dialogue, visible emotional pressure, "
            "and a clear change in the relationship or situation. Do not summarize when the user expects a vivid scene."
        )
    if mode == "research":
        return (
            f"[Director Quality Correction] Node {node.id} output lacks enough research value ({reasons}). "
            "Continue with explicit assumptions, evidence signals, uncertainty, and decision-useful implications."
        )
    return (
        f"[Director Quality Correction] Node {node.id} output quality is below target ({reasons}). "
        "Continue with more specific actions, clearer causality, and a concrete next state."
    )


def build_preflight_preview(
    *,
    workflow: WorkflowDefinition,
    node_id: str,
    node_output: dict[str, dict[str, Any]],
    context: dict[str, Any],
    expensive_remaining: int,
) -> dict[str, Any]:
    recent: list[dict[str, Any]] = []
    for previous_node_id in list(node_output.keys())[-3:]:
        output = node_output.get(previous_node_id, {})
        snippet = str(output.get("content") or output.get("text") or output.get("summary") or "")[:260]
        recent.append({"node_id": previous_node_id, "snippet": snippet})
    simulation_state = context.get("_simulation", {}) if isinstance(context.get("_simulation", {}), dict) else {}
    return {
        "workflow_id": workflow.id,
        "next_node_id": node_id,
        "recent_outputs": recent,
        "simulation_state": simulation_state,
        "expensive_nodes_remaining": expensive_remaining,
    }


def vote_label_from_text(text: str) -> str:
    lowered = text.lower()
    no_words = ["no", "reject", "oppose", "deny", "fail", "反对", "否决", "不通过", "不支持", "不行", "不够色"]
    yes_words = ["yes", "approve", "support", "pass", "accept", "赞成", "同意", "通过", "支持", "可以", "够色"]
    abstain_words = ["abstain", "neutral", "skip", "hold", "待定", "中立", "弃权", "观望"]
    if any(word in lowered for word in no_words):
        return "no"
    if any(word in lowered for word in yes_words):
        return "yes"
    if any(word in lowered for word in abstain_words):
        return "abstain"
    return "unknown"


def collect_vote_ballots(interactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ballots: list[dict[str, Any]] = []
    for item in interactions:
        source = str(item.get("source", "agent")).strip() or "agent"
        message = str(item.get("message", "")).strip()
        payload = item.get("payload", {})
        label = "unknown"
        if isinstance(payload, dict):
            for key in ["vote", "option", "decision", "result"]:
                raw = payload.get(key)
                if isinstance(raw, bool):
                    label = "yes" if raw else "no"
                    break
                if raw is not None:
                    label = vote_label_from_text(str(raw))
                    if label != "unknown":
                        break
        if label == "unknown":
            label = vote_label_from_text(message)
        ballots.append({"source": source, "vote": label, "reason": message[:300]})
    return ballots


def is_critical_decision(runtime_input: dict[str, Any], output: dict[str, Any]) -> bool:
    text_parts = [
        value.lower()
        for value in [runtime_input.get("prompt"), runtime_input.get("question"), output.get("content"), output.get("text"), output.get("summary")]
        if isinstance(value, str)
    ]
    critical_tokens = [
        "是否",
        "决策",
        "方案",
        "go/no-go",
        "approve",
        "reject",
        "关键",
        "重大",
        "推进",
        "立项",
        "剧情",
        "分支",
        "尺度",
        "够色",
    ]
    return any(token in " ".join(text_parts) for token in critical_tokens)
