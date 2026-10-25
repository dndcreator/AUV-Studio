from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy.orm import Session

from ..models import MemoryRecord
from ..schemas import RunRequest, WorkflowNode

MemoryKind = Literal["fact", "character"]
_MEMORY_KINDS = {"fact", "character"}


def load_recent_memories(db: Session, workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(MemoryRecord)
        .filter(MemoryRecord.workflow_id == workflow_id)
        .order_by(MemoryRecord.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    memories: list[dict[str, Any]] = []
    for row in reversed(rows):
        try:
            meta = json.loads(row.tags_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        kind = str(meta.get("kind", ""))
        if kind not in _MEMORY_KINDS:
            continue
        memories.append(
            {
                "id": row.id,
                "run_id": row.run_id,
                "node_id": row.node_id,
                "role": row.role,
                "content": row.content,
                "tags": meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
                "kind": kind,
                "scope": str(meta.get("scope", "node")),
                "subject": str(meta.get("subject", row.node_id)),
                "importance": float(meta.get("importance", 0.5)),
                "confidence": float(meta.get("confidence", 0.7)),
                "source_event_seq": meta.get("source_event_seq"),
                "trigger": str(meta.get("trigger", "")),
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
        )
    return memories


def memories_at_rollback(memories: list[dict[str, Any]], request: RunRequest) -> list[dict[str, Any]]:
    rollback = request.input.get("_rollback") if isinstance(request.input, dict) else None
    if not isinstance(rollback, dict):
        return memories
    source_run_id = str(rollback.get("from_run_id", "")).strip()
    try:
        event_seq = int(rollback.get("event_seq"))
    except (TypeError, ValueError):
        return memories
    return [
        memory
        for memory in memories
        if str(memory.get("run_id", "")) != source_run_id
        or isinstance(memory.get("source_event_seq"), int)
        and int(memory["source_event_seq"]) <= event_seq
    ]


def extract_memory_content(node_type: str, output: dict[str, Any]) -> str:
    output_keys = {
        "agent": "content",
        "director": "global_guidance",
        "external_agent": "text",
        "prompt": "text",
        "human_checkpoint": "response",
    }
    key = output_keys.get(node_type)
    return str(output.get(key, "")).strip() if key else ""


def node_subject(node: WorkflowNode) -> str:
    return str(node.config.get("entity_name", node.config.get("role", node.id))).strip() or node.id


def memory_importance(node_type: str, status: str, output: dict[str, Any]) -> float:
    if status in {"failed", "waiting_human"}:
        return 0.9
    if node_type in {"director", "human_checkpoint"}:
        return 0.85
    text = json.dumps(output, ensure_ascii=False, default=str).lower()
    if any(word in text for word in ["final", "conclusion", "decision", "turning point", "结论", "决定", "转折"]):
        return 0.8
    return 0.55


def should_write_memory(
    *,
    kind: MemoryKind,
    node: WorkflowNode,
    content: str,
    importance: float,
    interactions: list[dict[str, Any]] | None = None,
) -> bool:
    if not content.strip() or node.type in {"condition", "tool"} and importance < 0.75:
        return False
    if kind == "character":
        return node.type in {"agent", "external_agent"} and (bool(interactions) or importance >= 0.6)
    if importance >= 0.75:
        return True
    text = content.lower()
    signal_words = [
        "decide",
        "decision",
        "conclude",
        "conflict",
        "agree",
        "reject",
        "turning point",
        "决定",
        "结论",
        "冲突",
        "同意",
        "拒绝",
        "转折",
    ]
    return bool(interactions) or any(word in text for word in signal_words) or len(content.strip()) >= 80


def memory_recall_score(item: dict[str, Any], node: WorkflowNode) -> float:
    subject = node_subject(node)
    kind = str(item.get("kind", "fact"))
    scope = str(item.get("scope", "node"))
    node_id = str(item.get("node_id", ""))
    item_subject = str(item.get("subject", ""))
    importance = float(item.get("importance", 0.5) or 0.5)
    score = importance
    if scope in {"global", "environment"}:
        score += 0.45
    if node_id == node.id or item_subject == subject:
        score += 0.35
    if kind == "character":
        score += 0.2 if node_id == node.id or item_subject == subject else 0.05
    elif kind == "fact":
        score += 0.1
    if item.get("source_event_seq") is not None:
        score += 0.05
    return score


def build_memory_meta(
    *,
    kind: MemoryKind,
    node: WorkflowNode,
    role: str,
    importance: float,
    confidence: float,
    source_event_seq: int | None,
    trigger: str = "",
) -> dict[str, Any]:
    scope = "global" if node.type == "director" else (
        "environment" if str(node.config.get("entity_type", "")) == "environment" else "node"
    )
    return {
        "version": 1,
        "kind": kind,
        "scope": scope,
        "subject": node_subject(node),
        "role": role,
        "importance": max(0.0, min(1.0, importance)),
        "confidence": max(0.0, min(1.0, confidence)),
        "source_event_seq": source_event_seq,
        "trigger": trigger,
    }


def select_memories_for_node(
    memories: Any,
    node: WorkflowNode,
    limit: int = 12,
    *,
    include_peer_facts: bool = True,
) -> list[dict[str, Any]]:
    if not isinstance(memories, list):
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in memories:
        if not isinstance(item, dict) or str(item.get("kind", "")) not in _MEMORY_KINDS:
            continue
        kind = str(item["kind"])
        scope = str(item.get("scope", "node"))
        node_id = str(item.get("node_id", ""))
        importance = float(item.get("importance", 0.5) or 0.5)
        score = memory_recall_score(item, node)
        relevant = (
            scope in {"global", "environment"}
            or node_id == node.id
            or include_peer_facts and kind == "fact" and (importance >= 0.5 or score >= 0.8)
            or kind == "character" and score >= 0.8
        )
        if relevant:
            scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], int(pair[1].get("id", 0) or 0)))
    return [item for _, item in scored[-max(1, min(limit, 40)) :]]


def append_memory_record(
    *,
    db: Session,
    context: dict[str, Any],
    workflow_id: str,
    run_id: str,
    node: WorkflowNode,
    role: str,
    content: str,
    meta: dict[str, Any],
) -> None:
    if not content.strip():
        return
    memory = MemoryRecord(
        workflow_id=workflow_id,
        run_id=run_id,
        node_id=node.id,
        role=role,
        content=content.strip()[:4000],
        tags_json=json.dumps(meta, ensure_ascii=False, default=str),
    )
    db.add(memory)
    db.commit()
    memory_item = {
        "id": memory.id,
        "run_id": run_id,
        "node_id": node.id,
        "role": memory.role,
        "content": memory.content,
        "tags": [],
        "created_at": memory.created_at.isoformat() if memory.created_at else "",
        **meta,
    }
    current_memory = context.get("_memory", [])
    if not isinstance(current_memory, list):
        current_memory = []
    current_memory.append(memory_item)
    context["_memory"] = current_memory[-100:]


def extract_role(node_type: str, node: WorkflowNode, output: dict[str, Any]) -> str:
    if node_type == "agent":
        return str(node.config.get("role", "agent"))
    if node_type == "director":
        return "director"
    if node_type == "external_agent":
        return str(node.config.get("agent_id", "external_agent"))
    if node_type == "human_checkpoint":
        return "human"
    return str(output.get("role", "")).strip()


def pull_latest_director_override(
    db: Session,
    *,
    workflow_id: str,
    run_id: str,
    after_id: int,
) -> tuple[int, str | None]:
    rows = (
        db.query(MemoryRecord)
        .filter(
            MemoryRecord.workflow_id == workflow_id,
            MemoryRecord.run_id == run_id,
            MemoryRecord.role == "director_override",
            MemoryRecord.id > after_id,
        )
        .order_by(MemoryRecord.id.asc())
        .all()
    )
    if not rows:
        return after_id, None
    latest = rows[-1]
    return int(latest.id), str(latest.content).strip() or None
