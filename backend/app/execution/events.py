from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..audit import append_trace_to_payload
from ..models import NodeRunRecord, RunEventRecord


def serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def redact_secrets(value: Any) -> Any:
    secret_keys = {"api_key", "authorization", "auth", "token", "secret", "password"}
    numeric_telemetry_keys = {
        "estimated_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "token_count",
        "token_budget",
        "max_tokens",
        "max_output_tokens",
    }
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower()
            numeric_telemetry = normalized in numeric_telemetry_keys and isinstance(item, (int, float)) and not isinstance(item, bool)
            if not numeric_telemetry and (
                normalized in secret_keys or any(marker in normalized for marker in ["api_key", "token", "secret", "password"])
            ):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def emit_event(
    db: Session,
    run_id: str,
    node_id: str,
    event: str,
    payload: dict[str, Any],
    *,
    duration_ms: int | None = None,
    trace_id: str | None = None,
    parent_event_ids: list[int] | None = None,
    caused_by: str = "engine",
    context_snapshot: dict[str, Any] | None = None,
    prev_hash: str = "",
) -> tuple[int, str]:
    now = datetime.now(timezone.utc)
    event_payload = redact_secrets(payload)
    # Internal checkpoints contain domain state, not transport configuration.
    # Public event serializers remove this field; keep exact values for rollback.
    if isinstance(payload.get("_rollback_snapshot"), dict):
        event_payload["_rollback_snapshot"] = payload["_rollback_snapshot"]
    signed_payload, event_hash = append_trace_to_payload(
        payload=event_payload,
        run_id=run_id,
        node_id=node_id,
        event=event,
        timestamp=now,
        duration_ms=duration_ms,
        prev_hash=prev_hash,
        trace_id=trace_id,
        parent_event_ids=parent_event_ids,
        caused_by=caused_by,
        context_snapshot=context_snapshot,
    )
    record = RunEventRecord(
        run_id=run_id,
        node_id=node_id,
        event=event,
        duration_ms=duration_ms,
        timestamp=now,
        payload_json=serialize(signed_payload),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return int(record.seq), event_hash


def load_previous_outputs(db: Session, run_id: str) -> dict[str, dict[str, Any]]:
    rows = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id).all()
    return {row.node_id: json.loads(row.output_json) for row in rows}


def load_previous_outputs_until_event(db: Session, run_id: str, event_seq: int) -> dict[str, dict[str, Any]]:
    rows = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.seq <= event_seq, RunEventRecord.event == "succeeded")
        .order_by(RunEventRecord.seq.asc())
        .all()
    )
    outputs: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        output = payload.get("output", {}) if isinstance(payload, dict) else {}
        if isinstance(output, dict):
            outputs[row.node_id] = output
    return outputs


def is_director_preflight_response(response: dict[str, Any]) -> bool:
    metadata = response.get("metadata", {})
    return isinstance(metadata, dict) and str(metadata.get("kind", "")) == "director_preflight_confirm"


def emit_human_resumed(
    *,
    db: Session,
    run_id: str,
    node_id: str,
    response: dict[str, Any],
    last_event_hash: str,
    parent_event_ids: list[int] | None = None,
) -> str:
    metadata = response.get("metadata", {})
    _, next_hash = emit_event(
        db,
        run_id,
        node_id,
        "human_resumed",
        {
            "responder": str(response.get("responder", "user")),
            "metadata": metadata if isinstance(metadata, dict) else {},
        },
        caused_by="human",
        parent_event_ids=parent_event_ids or [],
        context_snapshot={"responder": str(response.get("responder", "user")), "node_id": node_id},
        prev_hash=last_event_hash,
    )
    return next_hash
