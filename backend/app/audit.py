from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .models import RunEventRecord

TRACE_VERSION = "1.0"
HASH_ALGO = "sha256"


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _strip_trace(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out.pop("_trace", None)
    return out


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _trim_value(value: Any, *, max_text: int = 400, max_items: int = 20, depth: int = 0) -> Any:
    if depth > 4:
        return "[trimmed-depth]"
    if isinstance(value, str):
        return value if len(value) <= max_text else (value[:max_text] + "...[truncated]")
    if isinstance(value, list):
        return [_trim_value(v, max_text=max_text, max_items=max_items, depth=depth + 1) for v in value[:max_items]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= max_items:
                out["__truncated__"] = True
                break
            out[str(k)] = _trim_value(v, max_text=max_text, max_items=max_items, depth=depth + 1)
        return out
    return value


def compute_event_hash(
    *,
    run_id: str,
    node_id: str,
    event: str,
    timestamp_iso: str,
    duration_ms: int | None,
    payload_without_trace: dict[str, Any],
    prev_hash: str,
) -> str:
    canonical = _canonical_json(
        {
            "run_id": run_id,
            "node_id": node_id,
            "event": event,
            "timestamp": timestamp_iso,
            "duration_ms": duration_ms,
            "payload": payload_without_trace,
            "prev_hash": prev_hash,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_last_event_hash(db: Session, run_id: str) -> tuple[int, str]:
    row = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id)
        .order_by(RunEventRecord.seq.desc())
        .first()
    )
    if row is None:
        return 0, ""
    payload = _safe_json_dict(row.payload_json)
    trace = payload.get("_trace", {})
    if isinstance(trace, dict):
        return int(row.seq), str(trace.get("event_hash", "") or "")
    return int(row.seq), ""


def append_trace_to_payload(
    *,
    payload: dict[str, Any],
    run_id: str,
    node_id: str,
    event: str,
    timestamp: datetime,
    duration_ms: int | None,
    prev_hash: str,
    trace_id: str | None = None,
    parent_event_ids: list[int] | None = None,
    caused_by: str = "engine",
    context_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    clean_payload = _strip_trace(payload)
    timestamp_iso = _to_utc(timestamp).isoformat()
    event_hash = compute_event_hash(
        run_id=run_id,
        node_id=node_id,
        event=event,
        timestamp_iso=timestamp_iso,
        duration_ms=duration_ms,
        payload_without_trace=clean_payload,
        prev_hash=prev_hash,
    )
    trace = {
        "version": TRACE_VERSION,
        "trace_id": trace_id or f"trc_{uuid.uuid4().hex[:12]}",
        "parent_event_ids": parent_event_ids or [],
        "caused_by": caused_by,
        "context_snapshot": _trim_value(context_snapshot or {}),
        "prev_hash": prev_hash,
        "event_hash": event_hash,
        "hash_algo": HASH_ALGO,
    }
    merged = dict(clean_payload)
    merged["_trace"] = trace
    return merged, event_hash


def verify_run_event_chain(rows: list[RunEventRecord]) -> tuple[bool, int | None, str]:
    prev_hash = ""
    checked = 0
    for row in sorted(rows, key=lambda x: int(x.seq)):
        payload = _safe_json_dict(row.payload_json)
        trace = payload.get("_trace", {})
        if not isinstance(trace, dict):
            return False, int(row.seq), "missing _trace payload"
        payload_without_trace = _strip_trace(payload)
        expected_prev = str(trace.get("prev_hash", "") or "")
        if expected_prev != prev_hash:
            return False, int(row.seq), "prev_hash mismatch"
        expected_hash = str(trace.get("event_hash", "") or "")
        actual_hash = compute_event_hash(
            run_id=row.run_id,
            node_id=row.node_id,
            event=row.event,
            timestamp_iso=_to_utc(row.timestamp).isoformat(),
            duration_ms=row.duration_ms,
            payload_without_trace=payload_without_trace,
            prev_hash=expected_prev,
        )
        if expected_hash != actual_hash:
            return False, int(row.seq), "event_hash mismatch"
        prev_hash = expected_hash
        checked += 1
    return True, None, f"verified {checked} events"


def _safe_json_dict(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
