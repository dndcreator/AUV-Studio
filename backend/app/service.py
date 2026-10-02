from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from time import perf_counter
from textwrap import dedent

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .audit import append_trace_to_payload, load_last_event_hash, verify_run_event_chain
from .config import settings
from .execution.provider import OpenAICompatibleProvider
from .models import MemoryRecord, NodeRunRecord, RunEventRecord, RunRecord, WorkflowRecord, WorkflowTemplateRecord
from .observability import perf_tracker
from .mode_contracts import MODE_CONTRACTS
from .model_config_service import apply_stored_model_config, get_model_config, save_model_config, test_model_config
from .plan_compiler import compile_workflow_from_plan
from .schemas import (
    DirectorCapability,
    DirectorCommandRequest,
    DirectorCommandResponse,
    DirectorEffect,
    AuditVerifyResponse,
    EventsResponse,
    FullLogResponse,
    NodeRun,
    ObservabilityHttpMetrics,
    ObservabilityRunMetrics,
    ObservabilitySnapshot,
    PerformanceAlert,
    MemoryClearResponse,
    MemoryItem,
    ModeContract,
    RollbackRequest,
    RollbackResponse,
    RunCompareResponse,
    RunCheckpoint,
    RunCheckpointsResponse,
    RunDetail,
    RunReportRequest,
    RunReportResponse,
    TemplateCreateRequest,
    TemplateDetail,
    TemplateSummary,
    ReportSection,
    RunRequest,
    RunEvent,
    TraceEventContextResponse,
    RunMetricsResponse,
    WorkflowDefinition,
    WorkflowSummary,
)

DIRECTOR_CAPABILITIES: list[DirectorCapability] = [
    DirectorCapability(
        capability_id="set_focus",
        title="Set Focus",
        description="Change current research/simulation focus.",
        args_schema={"focus": "string"},
    ),
    DirectorCapability(
        capability_id="set_style",
        title="Set Style",
        description="Adjust output style for following nodes.",
        args_schema={"style": "concise|formal|creative|critical"},
    ),
    DirectorCapability(
        capability_id="set_cost_mode",
        title="Set Cost Mode",
        description="Control cost/quality tradeoff.",
        args_schema={"mode": "low|balanced|high"},
    ),
    DirectorCapability(
        capability_id="inject_event",
        title="Inject Event",
        description="Inject an external event into current simulation.",
        args_schema={"event": "string"},
    ),
    DirectorCapability(
        capability_id="set_phase",
        title="Set Phase",
        description="Move simulation focus to a named phase.",
        args_schema={"phase_name": "string"},
    ),
]

def upsert_workflow(db: Session, workflow: WorkflowDefinition) -> WorkflowDefinition:
    rec = db.query(WorkflowRecord).filter(WorkflowRecord.id == workflow.id).first()
    data = json.dumps(workflow.model_dump(), ensure_ascii=False)
    if rec is None:
        rec = WorkflowRecord(id=workflow.id, name=workflow.name, version=workflow.version, definition_json=data)
        db.add(rec)
    else:
        rec.name = workflow.name
        rec.version = workflow.version
        rec.definition_json = data
        db.add(rec)
    db.commit()
    return workflow


def create_workflow(db: Session, workflow: WorkflowDefinition) -> WorkflowDefinition:
    existed = db.query(WorkflowRecord).filter(WorkflowRecord.id == workflow.id).first()
    if existed:
        raise HTTPException(status_code=409, detail=f"workflow '{workflow.id}' already exists")
    return upsert_workflow(db, workflow)


def get_workflow(db: Session, workflow_id: str) -> WorkflowDefinition:
    rec = db.query(WorkflowRecord).filter(WorkflowRecord.id == workflow_id).first()
    if rec is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return WorkflowDefinition.model_validate(json.loads(rec.definition_json))


def list_workflows(db: Session) -> list[WorkflowSummary]:
    rows = db.query(WorkflowRecord).order_by(WorkflowRecord.updated_at.desc()).all()
    return [WorkflowSummary(id=r.id, name=r.name, version=r.version, updated_at=r.updated_at) for r in rows]


def create_or_update_template(db: Session, req: TemplateCreateRequest) -> TemplateDetail:
    rec = db.query(WorkflowTemplateRecord).filter(WorkflowTemplateRecord.id == req.id).first()
    definition_json = json.dumps(req.workflow.model_dump(), ensure_ascii=False)
    tags_json = json.dumps(req.tags, ensure_ascii=False)
    if rec is None:
        rec = WorkflowTemplateRecord(
            id=req.id,
            name=req.name,
            description=req.description,
            category=req.category,
            tags_json=tags_json,
            definition_json=definition_json,
        )
    else:
        rec.name = req.name
        rec.description = req.description
        rec.category = req.category
        rec.tags_json = tags_json
        rec.definition_json = definition_json
    db.add(rec)
    db.commit()
    return TemplateDetail(
        id=rec.id,
        name=rec.name,
        description=rec.description,
        category=rec.category,
        tags=req.tags,
        workflow=req.workflow,
    )


def list_templates(db: Session) -> list[TemplateSummary]:
    rows = db.query(WorkflowTemplateRecord).order_by(WorkflowTemplateRecord.updated_at.desc()).all()
    out: list[TemplateSummary] = []
    for row in rows:
        tags = _safe_json_loads(row.tags_json, {})
        out.append(
            TemplateSummary(
                id=row.id,
                name=row.name,
                description=row.description,
                category=row.category,
                tags=tags if isinstance(tags, list) else [],
                updated_at=row.updated_at,
            )
        )
    return out


def get_template(db: Session, template_id: str) -> TemplateDetail:
    row = db.query(WorkflowTemplateRecord).filter(WorkflowTemplateRecord.id == template_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="template not found")
    workflow = WorkflowDefinition.model_validate(json.loads(row.definition_json))
    tags = _safe_json_loads(row.tags_json, {})
    return TemplateDetail(
        id=row.id,
        name=row.name,
        description=row.description,
        category=row.category,
        tags=tags if isinstance(tags, list) else [],
        workflow=workflow,
    )


def list_memories(db: Session, workflow_id: str, limit: int = 100, run_id: str | None = None) -> list[MemoryItem]:
    q = db.query(MemoryRecord).filter(MemoryRecord.workflow_id == workflow_id)
    if run_id:
        q = q.filter(MemoryRecord.run_id == run_id)
    rows = q.order_by(MemoryRecord.id.desc()).limit(max(1, min(limit, 1000))).all()
    out: list[MemoryItem] = []
    for row in rows:
        raw_meta = _safe_json_loads(row.tags_json, [])
        tags = raw_meta if isinstance(raw_meta, list) else []
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        kind = str(meta.get("kind", "fact")) if isinstance(meta, dict) else "fact"
        if kind not in {"fact", "state", "character"}:
            kind = "other"
        out.append(
            MemoryItem(
                id=row.id,
                workflow_id=row.workflow_id,
                run_id=row.run_id,
                node_id=row.node_id,
                role=row.role,
                content=row.content,
                tags=tags if isinstance(tags, list) else [],
                kind=kind,  # type: ignore[arg-type]
                scope=str(meta.get("scope", "node")) if isinstance(meta, dict) else "node",
                subject=str(meta.get("subject", "")) if isinstance(meta, dict) else "",
                importance=float(meta.get("importance", 0.5)) if isinstance(meta, dict) else 0.5,
                confidence=float(meta.get("confidence", 0.7)) if isinstance(meta, dict) else 0.7,
                source_event_seq=int(meta["source_event_seq"]) if isinstance(meta, dict) and meta.get("source_event_seq") is not None else None,
                created_at=row.created_at,
            )
        )
    return out


def clear_memories(db: Session, workflow_id: str, run_id: str | None = None) -> MemoryClearResponse:
    q = db.query(MemoryRecord).filter(MemoryRecord.workflow_id == workflow_id)
    if run_id:
        q = q.filter(MemoryRecord.run_id == run_id)
    rows = q.all()
    deleted = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return MemoryClearResponse(workflow_id=workflow_id, deleted=deleted)


def request_run_stop(db: Session, run_id: str) -> dict[str, str]:
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status in {"succeeded", "failed", "stopped"}:
        return {"run_id": run_id, "status": run.status}

    previous_status = run.status
    run.status = "stopping" if previous_status == "running" else "stopped"
    if run.status == "stopped":
        run.ended_at = datetime.now(timezone.utc)
    db.add(run)
    db.commit()

    # Active runs emit the final stopped event from the worker after its current
    # provider response finishes, preserving a single ordered audit chain.
    if run.status == "stopping":
        return {"run_id": run_id, "status": run.status}

    _, prev_hash = load_last_event_hash(db, run_id)
    now = datetime.now(timezone.utc)
    event_name = "stopped"
    payload, _ = append_trace_to_payload(
        payload={"previous_status": previous_status, "status": run.status, "requested_by": "user"},
        run_id=run_id,
        node_id="run_control",
        event=event_name,
        timestamp=now,
        duration_ms=None,
        prev_hash=prev_hash,
        caused_by="user",
        context_snapshot={"previous_status": previous_status, "status": run.status},
    )
    db.add(
        RunEventRecord(
            run_id=run_id,
            node_id="run_control",
            event=event_name,
            timestamp=now,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    )
    db.commit()
    return {"run_id": run_id, "status": run.status}


def get_director_capabilities() -> list[DirectorCapability]:
    return DIRECTOR_CAPABILITIES


def get_mode_contracts() -> list[ModeContract]:
    return [MODE_CONTRACTS["roleplay"], MODE_CONTRACTS["research"], MODE_CONTRACTS["custom"]]


def apply_director_command(db: Session, run_id: str, req: DirectorCommandRequest) -> DirectorCommandResponse:
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    capability, args, guidance = _parse_director_command(text=text, scope=req.scope, target_node_id=req.target_node_id)
    if req.strict and capability == "set_focus" and len(text) < 6:
        raise HTTPException(status_code=422, detail="strict mode cannot parse command")

    mem = MemoryRecord(
        workflow_id=run.workflow_id,
        run_id=run_id,
        node_id="director_console",
        role="director_override",
        content=guidance[:4000],
        tags_json=json.dumps(["director_override", capability], ensure_ascii=False),
    )
    db.add(mem)
    db.commit()

    base_payload = {
        "command_text": text,
        "scope": req.scope,
        "target_node_id": req.target_node_id,
        "capability_id": capability,
        "normalized_args": args,
        "guidance": guidance,
    }
    _, prev_hash = load_last_event_hash(db, run_id)
    now = datetime.now(timezone.utc)
    signed_payload, _ = append_trace_to_payload(
        payload=base_payload,
        run_id=run_id,
        node_id="director_console",
        event="director_overridden",
        timestamp=now,
        duration_ms=None,
        prev_hash=prev_hash,
        caused_by="director",
        context_snapshot={"scope": req.scope, "target_node_id": req.target_node_id or "", "capability_id": capability},
    )
    event = RunEventRecord(
        run_id=run_id,
        node_id="director_console",
        event="director_overridden",
        timestamp=now,
        payload_json=json.dumps(signed_payload, ensure_ascii=False),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return DirectorCommandResponse(
        run_id=run_id,
        accepted=True,
        applied_capability=capability,
        normalized_args=args,
        guidance=guidance,
        event_seq=event.seq,
    )


def _parse_director_command(text: str, scope: str, target_node_id: str | None) -> tuple[str, dict[str, Any], str]:
    lower = text.lower()
    if any(k in lower for k in ["省钱", "cost", "budget", "便宜", "低成本"]):
        mode = "low" if any(k in lower for k in ["低", "low", "省"]) else ("high" if any(k in lower for k in ["高", "high", "更好"]) else "balanced")
        guidance = f"[Director Override] Cost mode set to {mode}. Keep outputs proportional to budget while preserving core objective."
        return "set_cost_mode", {"mode": mode, "scope": scope}, guidance
    if any(k in lower for k in ["风格", "style", "formal", "creative", "critical", "简洁", "严谨"]):
        style = "formal" if any(k in lower for k in ["formal", "正式", "汇报"]) else (
            "creative" if any(k in lower for k in ["creative", "剧情", "小说"]) else ("critical" if any(k in lower for k in ["critical", "质疑", "挑错"]) else "concise")
        )
        guidance = f"[Director Override] Style set to {style}. All downstream outputs should follow this style constraint."
        return "set_style", {"style": style, "scope": scope}, guidance
    if any(k in lower for k in ["阶段", "phase"]):
        phase_name = text
        for sep in ["到", "to", "phase", "阶段"]:
            phase_name = phase_name.replace(sep, " ")
        phase_name = " ".join(phase_name.split()).strip() or "next phase"
        guidance = f"[Director Override] Shift simulation attention to phase '{phase_name}'."
        return "set_phase", {"phase_name": phase_name, "scope": scope}, guidance
    if any(k in lower for k in ["突发", "事件", "event", "注入"]):
        guidance = f"[Director Override] Inject event and reassess: {text}"
        return "inject_event", {"event": text, "scope": scope}, guidance

    focus = text if len(text) <= 180 else text[:180]
    target_hint = f" Target node: {target_node_id}." if target_node_id else ""
    guidance = f"[Director Override] Focus update: {focus}.{target_hint} Align downstream analysis accordingly."
    args: dict[str, Any] = {"focus": focus, "scope": scope}
    if target_node_id:
        args["target_node_id"] = target_node_id
    return "set_focus", args, guidance


def get_observability_snapshot(db: Session) -> ObservabilitySnapshot:
    now = datetime.now(timezone.utc)
    raw_http = perf_tracker.snapshot(now=now)
    http_metrics = ObservabilityHttpMetrics(
        requests_1m=int(raw_http.get("requests_1m", 0)),
        requests_5m=int(raw_http.get("requests_5m", 0)),
        error_count_5m=int(raw_http.get("error_count_5m", 0)),
        error_rate_5m=float(raw_http.get("error_rate_5m", 0.0)),
        latency_p50_ms_5m=raw_http.get("latency_p50_ms_5m"),
        latency_p95_ms_5m=raw_http.get("latency_p95_ms_5m"),
        slow_requests_5m=int(raw_http.get("slow_requests_5m", 0)),
    )

    since_24h = now - timedelta(hours=24)
    run_rows = db.query(RunRecord).filter(RunRecord.started_at >= since_24h).all()
    node_rows = db.query(NodeRunRecord).filter(NodeRunRecord.started_at >= since_24h).all()

    runs_failed = sum(1 for r in run_rows if r.status == "failed")
    runs_succeeded = sum(1 for r in run_rows if r.status == "succeeded")
    durations = []
    for r in run_rows:
        if r.ended_at:
            durations.append(int((_to_utc(r.ended_at) - _to_utc(r.started_at)).total_seconds() * 1000))
    avg_duration = int(sum(durations) / len(durations)) if durations else None
    slow_nodes = sum(1 for n in node_rows if (n.duration_ms or 0) >= settings.perf_node_slow_ms)
    node_failures = sum(1 for n in node_rows if n.status == "failed")

    runtime_metrics = ObservabilityRunMetrics(
        runs_total=len(run_rows),
        runs_failed_24h=runs_failed,
        runs_succeeded_24h=runs_succeeded,
        avg_run_duration_ms_24h=avg_duration,
        slow_nodes_24h=slow_nodes,
        node_failures_24h=node_failures,
    )

    alerts: list[PerformanceAlert] = []
    if http_metrics.error_rate_5m >= settings.perf_error_rate_warn:
        alerts.append(
            PerformanceAlert(
                code="HIGH_ERROR_RATE",
                severity="critical",
                message=f"HTTP 5xx rate in last 5m is {http_metrics.error_rate_5m:.2%}",
            )
        )
    if (http_metrics.latency_p95_ms_5m or 0) >= settings.perf_latency_warn_ms:
        alerts.append(
            PerformanceAlert(
                code="HIGH_P95_LATENCY",
                severity="warn",
                message=f"HTTP p95 latency in last 5m is {http_metrics.latency_p95_ms_5m}ms",
            )
        )
    if http_metrics.slow_requests_5m >= settings.perf_slow_request_warn_count:
        alerts.append(
            PerformanceAlert(
                code="MANY_SLOW_REQUESTS",
                severity="warn",
                message=f"Slow requests in last 5m: {http_metrics.slow_requests_5m}",
            )
        )
    if runtime_metrics.slow_nodes_24h > 0:
        alerts.append(
            PerformanceAlert(
                code="SLOW_NODES_DETECTED",
                severity="info",
                message=f"Slow nodes in last 24h: {runtime_metrics.slow_nodes_24h}",
            )
        )

    return ObservabilitySnapshot(
        timestamp=now,
        http=http_metrics,
        runtime=runtime_metrics,
        alerts=alerts,
    )


def get_run(db: Session, run_id: str) -> RunDetail:
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    node_rows = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id).order_by(NodeRunRecord.id.asc()).all()
    nodes = [
        NodeRun(
            node_id=row.node_id,
            status=row.status,
            started_at=row.started_at,
            ended_at=row.ended_at,
            duration_ms=row.duration_ms,
            input=json.loads(row.input_json),
            output=json.loads(row.output_json),
            error=json.loads(row.error_json) if row.error_json != "null" else None,
        )
        for row in node_rows
    ]
    return RunDetail(
        id=run.id,
        workflow_id=run.workflow_id,
        status=run.status,  # type: ignore[arg-type]
        input=json.loads(run.input_json),
        output=json.loads(run.output_json),
        error=json.loads(run.error_json) if run.error_json != "null" else None,
        retry_from_run_id=run.retry_from_run_id,
        retry_from_node=run.retry_from_node,
        started_at=run.started_at,
        ended_at=run.ended_at,
        node_runs=nodes,
    )


def get_events(db: Session, run_id: str, after_seq: int, limit: int) -> EventsResponse:
    rows = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.seq > after_seq)
        .order_by(RunEventRecord.seq.asc())
        .limit(limit)
        .all()
    )
    events = [_row_to_event(row) for row in rows]
    return EventsResponse(run_id=run_id, events=events)


def get_full_log(db: Session, run_id: str, output_format: str = "markdown") -> FullLogResponse:
    run_detail = get_run(db, run_id)
    rows = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id).order_by(RunEventRecord.seq.asc()).all()
    events = [_row_to_event(row) for row in rows]
    normalized_format = "json" if output_format == "json" else "markdown"
    if normalized_format == "json":
        content = json.dumps(
            {
                "run": run_detail.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    else:
        content = _build_full_log_markdown(run_detail=run_detail, events=events)
    return FullLogResponse(
        run_id=run_id,
        format=normalized_format,  # type: ignore[arg-type]
        content=content,
        event_count=len(events),
        node_count=len(run_detail.node_runs),
    )


def get_trace_event_context(db: Session, run_id: str, seq: int) -> TraceEventContextResponse:
    row = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id, RunEventRecord.seq == seq).first()
    if row is None:
        raise HTTPException(status_code=404, detail="event not found")
    payload = _safe_json_loads(row.payload_json, {})
    trace = payload.get("_trace", {}) if isinstance(payload, dict) else {}
    parent_ids_raw = trace.get("parent_event_ids", []) if isinstance(trace, dict) else []
    parent_ids = [int(v) for v in parent_ids_raw if isinstance(v, (int, str)) and str(v).isdigit()]
    parent_rows = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.seq.in_(parent_ids))
        .order_by(RunEventRecord.seq.asc())
        .all()
        if parent_ids
        else []
    )
    run_rows = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.seq <= seq)
        .order_by(RunEventRecord.seq.asc())
        .all()
    )
    chain_ok, _, _ = verify_run_event_chain(run_rows)
    return TraceEventContextResponse(
        run_id=run_id,
        seq=seq,
        event=_row_to_event(row),
        parent_events=[_row_to_event(r) for r in parent_rows],
        caused_by=str(trace.get("caused_by", "")) if isinstance(trace, dict) else "",
        context_snapshot=trace.get("context_snapshot", {}) if isinstance(trace, dict) and isinstance(trace.get("context_snapshot", {}), dict) else {},
        chain_ok=bool(chain_ok),
    )


def get_run_checkpoints(db: Session, run_id: str, limit: int = 80) -> RunCheckpointsResponse:
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    rows = (
        db.query(RunEventRecord)
        .filter(RunEventRecord.run_id == run_id, RunEventRecord.event.in_(["succeeded", "waiting_human", "failed"]))
        .order_by(RunEventRecord.seq.desc())
        .limit(max(1, min(limit, 300)))
        .all()
    )
    checkpoints: list[RunCheckpoint] = []
    for row in rows:
        payload = _safe_json_loads(row.payload_json, {})
        trace = payload.get("_trace", {}) if isinstance(payload, dict) else {}
        context_snapshot = trace.get("context_snapshot", {}) if isinstance(trace, dict) else {}
        output = payload.get("output", {}) if isinstance(payload, dict) else {}
        error = payload.get("error") if isinstance(payload, dict) else None
        summary = _checkpoint_summary(output=output, error=error, context_snapshot=context_snapshot)
        checkpoints.append(
            RunCheckpoint(
                seq=int(row.seq),
                node_id=row.node_id,
                event=row.event,  # type: ignore[arg-type]
                timestamp=row.timestamp,
                label=f"#{row.seq} {row.node_id} {row.event}",
                summary=summary,
                can_rollback=True,
            )
        )
    return RunCheckpointsResponse(run_id=run_id, checkpoints=checkpoints)


def build_rollback_request(db: Session, run_id: str, request: RollbackRequest) -> tuple[str, RunRequest]:
    source = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="run not found")
    row = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id, RunEventRecord.seq == request.event_seq).first()
    if row is None:
        raise HTTPException(status_code=404, detail="checkpoint event not found")
    if row.event not in {"succeeded", "waiting_human", "failed"}:
        raise HTTPException(status_code=400, detail="event is not rollback-capable")
    payload = _safe_json_loads(row.payload_json, {})
    trace = payload.get("_trace", {}) if isinstance(payload, dict) else {}
    context_snapshot = trace.get("context_snapshot", {}) if isinstance(trace, dict) and isinstance(trace.get("context_snapshot", {}), dict) else {}
    rollback_input = {
        "_rollback": {
            "from_run_id": run_id,
            "event_seq": int(row.seq),
            "node_id": row.node_id,
            "event": row.event,
            "reason": (request.reason or "").strip(),
            "context_snapshot": context_snapshot,
        }
    }
    return source.workflow_id, RunRequest(input=rollback_input, retry_from_run_id=run_id, retry_from_node=row.node_id)


def _checkpoint_summary(*, output: object, error: object, context_snapshot: object) -> str:
    if isinstance(error, dict) and error:
        message = str(error.get("message") or error.get("code") or "")[:160]
        if message:
            return message
    if isinstance(output, dict):
        for key in ("content", "text", "decision", "global_guidance", "question"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().replace("\n", " ")[:180]
        if output:
            return json.dumps(output, ensure_ascii=False, default=str)[:180]
    if isinstance(context_snapshot, dict):
        node_id = context_snapshot.get("node_id")
        if node_id:
            return f"context snapshot at {node_id}"
    return ""


def verify_run_audit(db: Session, run_id: str) -> AuditVerifyResponse:
    rows = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id).order_by(RunEventRecord.seq.asc()).all()
    if not rows:
        raise HTTPException(status_code=404, detail="run events not found")
    verified, broken_at_seq, message = verify_run_event_chain(rows)
    checked = 0
    if verified:
        checked = len(rows)
    elif broken_at_seq is not None:
        checked = len([r for r in rows if int(r.seq) < broken_at_seq])
    return AuditVerifyResponse(
        run_id=run_id,
        verified=verified,
        total_events=len(rows),
        checked_events=checked,
        broken_at_seq=broken_at_seq,
        message=message,
    )


def get_run_metrics(db: Session, run_id: str) -> RunMetricsResponse:
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    node_rows = db.query(NodeRunRecord).filter(NodeRunRecord.run_id == run_id).order_by(NodeRunRecord.id.asc()).all()
    event_rows = db.query(RunEventRecord).filter(RunEventRecord.run_id == run_id).order_by(RunEventRecord.seq.asc()).all()

    duration_ms: int | None = None
    if run.ended_at is not None:
        start = _to_utc(run.started_at)
        end = _to_utc(run.ended_at)
        duration_ms = int((end - start).total_seconds() * 1000)

    succeeded = sum(1 for n in node_rows if n.status == "succeeded")
    failed = sum(1 for n in node_rows if n.status == "failed")
    skipped = sum(1 for n in node_rows if n.status == "skipped")

    total_interactions = 0
    prompt_chars = 0
    completion_chars = 0
    director_scores: list[float] = []
    director_corrections = sum(1 for row in event_rows if row.event in {"director_corrected", "director_overridden", "director_vote_finished"})

    for n in node_rows:
        node_input = _safe_json_loads(n.input_json, {})
        node_output = _safe_json_loads(n.output_json, {})
        prompt_chars += len(json.dumps(node_input, ensure_ascii=False))
        completion_chars += len(json.dumps(node_output, ensure_ascii=False))

        interactions = node_input.get("_interactions", []) if isinstance(node_input, dict) else []
        if isinstance(interactions, list):
            total_interactions += len(interactions)

        if isinstance(node_output, dict):
            score = node_output.get("best_score")
            if isinstance(score, (int, float)):
                director_scores.append(float(score))

    avg_score = (sum(director_scores) / len(director_scores)) if director_scores else None
    director_effect = DirectorEffect(
        first_score=director_scores[0] if director_scores else None,
        last_score=director_scores[-1] if director_scores else None,
        delta_score=(director_scores[-1] - director_scores[0]) if len(director_scores) >= 2 else None,
        improved=(director_scores[-1] >= director_scores[0]) if len(director_scores) >= 2 else False,
    )

    # Rough estimate: 1 token ~= 4 chars for mixed content.
    estimated_tokens = int((prompt_chars + completion_chars) / 4)

    return RunMetricsResponse(
        run_id=run_id,
        status=run.status,  # type: ignore[arg-type]
        duration_ms=duration_ms,
        total_events=len(event_rows),
        total_nodes=len(node_rows),
        succeeded_nodes=succeeded,
        failed_nodes=failed,
        skipped_nodes=skipped,
        total_interactions=total_interactions,
        director_guidance_count=len(director_scores) + director_corrections,
        director_avg_score=avg_score,
        director_effect=director_effect,
        estimated_prompt_chars=prompt_chars,
        estimated_completion_chars=completion_chars,
        estimated_token_usage=estimated_tokens,
    )


def compare_runs(db: Session, run_a: str, run_b: str) -> RunCompareResponse:
    a = get_run_metrics(db, run_a)
    b = get_run_metrics(db, run_b)

    token_delta = a.estimated_token_usage - b.estimated_token_usage
    duration_delta_ms = None
    if a.duration_ms is not None and b.duration_ms is not None:
        duration_delta_ms = a.duration_ms - b.duration_ms

    winner = "tie"
    # Prefer lower token cost; tie-breaker by fewer failed nodes.
    if a.estimated_token_usage < b.estimated_token_usage:
        winner = "a"
    elif a.estimated_token_usage > b.estimated_token_usage:
        winner = "b"
    else:
        if a.failed_nodes < b.failed_nodes:
            winner = "a"
        elif a.failed_nodes > b.failed_nodes:
            winner = "b"

    return RunCompareResponse(
        run_a=run_a,
        run_b=run_b,
        duration_ms_a=a.duration_ms,
        duration_ms_b=b.duration_ms,
        estimated_token_usage_a=a.estimated_token_usage,
        estimated_token_usage_b=b.estimated_token_usage,
        succeeded_nodes_a=a.succeeded_nodes,
        succeeded_nodes_b=b.succeeded_nodes,
        failed_nodes_a=a.failed_nodes,
        failed_nodes_b=b.failed_nodes,
        token_delta=token_delta,
        duration_delta_ms=duration_delta_ms,
        winner=winner,  # type: ignore[arg-type]
    )


async def generate_run_report(
    db: Session,
    provider: OpenAICompatibleProvider,
    run_id: str,
    request: RunReportRequest,
) -> RunReportResponse:
    run_detail = get_run(db, run_id)
    metrics = get_run_metrics(db, run_id)
    workflow_record = db.query(WorkflowRecord).filter(WorkflowRecord.id == run_detail.workflow_id).first()
    workflow = WorkflowDefinition.model_validate(json.loads(workflow_record.definition_json)) if workflow_record is not None else None
    readable_source = _build_readable_report_source(run_detail=run_detail, workflow=workflow)

    style = (request.style_prompt or "").strip()
    audience = (request.audience or "").strip() or "general"
    report_mode = "narrative" if request.mode == "story" else request.mode
    title = (request.title or "").strip() or _default_report_title(run_id, report_mode)
    length_hint = {"short": "400-700 words", "medium": "800-1300 words", "long": "1400-2200 words"}[request.length]

    evidence_lines = _build_report_evidence_lines(run_detail=run_detail, metrics=metrics)
    evidence_block = "\n".join(evidence_lines[:120])
    output_language = "Chinese" if re.search(r"[\u4e00-\u9fff]", readable_source) else "the same language as the simulation source"
    simulation_mode = str(workflow.environment.simulation.get("ui_mode", "custom")) if workflow else "custom"

    if report_mode == "briefing":
        writer_system = (
            "You are an analyst writing a concise, evidence-grounded simulation report. "
            "Use only the readable simulation material as the source of truth. Do not expose node IDs, prompts, "
            "provider responses, tokens, or internal Director instructions. Do not invent facts outside provided evidence."
        )
        writer_user = dedent(
            f"""
            Write a formal briefing report in Markdown.
            Audience: {audience}
            Simulation mode: {simulation_mode}
            Output language: {output_language}
            Target length: {length_hint}
            Optional style requirement: {style or "none"}

            Required sections:
            1) Background and setup
            2) What happened
            3) Key findings or character/relationship changes
            4) Outcome
            5) Limitations
            6) Recommendations or possible next steps

            Evidence:
            {evidence_block}

            Readable Simulation Material:
            {readable_source[:16000]}
            """
        ).strip()
    else:
        writer_system = (
            "You are a narrative writer adapting readable simulation material into a literary work or script. "
            "Preserve core causality, actors, decisions, and outcomes. Never mention nodes, prompts, tokens, traces, "
            "the report-generation process, or internal Director instructions. "
            "You may dramatize prose, dialogue, pacing, and scene transitions when the user asks for it."
        )
        writer_user = dedent(
            f"""
            Rewrite the simulation process as a polished narrative work in Markdown.
            Audience: {audience}
            Output language: {output_language}
            Target length: {length_hint}
            User style/form requirement: {style or "cinematic narrative with concrete scenes and dialogue"}

            Choose the most suitable structure for the requested form:
            - Novel/prose: chapters or scenes with vivid but grounded narration.
            - Screenplay/script: scene headings, action lines, and dialogue.
            - Game/mission script: beats, radio lines, objectives, and transitions.

            Keep core facts grounded in evidence while making the work readable and dramatic.

            Evidence:
            {evidence_block}

            Readable Simulation Material:
            {readable_source[:16000]}
            """
        ).strip()

    llm = await provider.chat(model="", system_prompt=writer_system, user_prompt=writer_user)
    markdown = str(llm.get("content", "")).strip()
    if not markdown:
        markdown = _fallback_markdown(
            title=title,
            mode=report_mode,
            run_detail=run_detail,
            metrics=metrics,
            evidence=evidence_lines[:20],
            readable_source=readable_source,
        )

    sections = _extract_sections(markdown)
    evidence_index = evidence_lines[:40]

    return RunReportResponse(
        run_id=run_id,
        mode=request.mode,
        title=title,
        report_markdown=markdown if markdown.lstrip().startswith("# ") else f"# {title}\n\n{markdown}",
        sections=sections,
        evidence_index=evidence_index,
    )


def _safe_json_loads(raw: str, default: object) -> object:
    try:
        data = json.loads(raw)
        return data
    except Exception:  # noqa: BLE001
        return default


def _row_to_event(row: RunEventRecord) -> RunEvent:
    return RunEvent(
        seq=row.seq,
        run_id=row.run_id,
        node_id=row.node_id,
        event=row.event,  # type: ignore[arg-type]
        timestamp=row.timestamp,
        duration_ms=row.duration_ms,
        payload=json.loads(row.payload_json),
    )


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_evidence_lines(run_detail: RunDetail, metrics: RunMetricsResponse, events: list[RunEvent]) -> list[str]:
    lines: list[str] = []
    lines.append(f"run_id={run_detail.id}, workflow_id={run_detail.workflow_id}, status={run_detail.status}")
    lines.append(
        f"metrics: duration_ms={metrics.duration_ms}, tokens={metrics.estimated_token_usage}, "
        f"succeeded={metrics.succeeded_nodes}, failed={metrics.failed_nodes}, interactions={metrics.total_interactions}"
    )
    lines.append(
        f"director_effect: count={metrics.director_guidance_count}, avg_score={metrics.director_avg_score}, "
        f"delta={metrics.director_effect.delta_score}, improved={metrics.director_effect.improved}"
    )
    for node in run_detail.node_runs:
        lines.append(
            f"node={node.node_id} status={node.status} duration_ms={node.duration_ms} "
            f"input={json.dumps(node.input, ensure_ascii=False, default=str)[:260]} "
            f"output={json.dumps(node.output, ensure_ascii=False, default=str)[:260]}"
        )
    for e in events[:80]:
        lines.append(
            f"event#{e.seq} node={e.node_id} type={e.event} ts={e.timestamp.isoformat()} "
            f"payload={json.dumps(e.payload, ensure_ascii=False, default=str)[:220]}"
        )
    return lines


def _readable_output(output: object) -> str:
    if isinstance(output, str):
        return output.strip()
    if not isinstance(output, dict):
        return ""
    for key in ("content", "text", "summary", "recommendation", "response"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if text.startswith("{") and text.endswith("}"):
                parsed = _safe_json_loads(text, {})
                nested = _readable_output(parsed)
                if nested:
                    return nested
            return re.sub(r"^```(?:json|markdown|text)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    return ""


def _build_readable_report_source(run_detail: RunDetail, workflow: WorkflowDefinition | None) -> str:
    node_map = {node.id: node for node in workflow.nodes} if workflow else {}
    interactions: list[str] = []
    final_outputs: list[str] = []
    for node_run in run_detail.node_runs:
        text = _readable_output(node_run.output)
        if not text:
            continue
        node = node_map.get(node_run.node_id)
        node_type = node.type if node else ""
        if node_type in {"director", "report"} or node_run.node_id.startswith(("director_", "report_")):
            continue
        config = node.config if node else {}
        label = str(config.get("entity_name") or config.get("profile") or config.get("role") or node_run.node_id)
        if node_run.node_id.startswith("summary_"):
            final_outputs.append(text)
        else:
            interactions.append(f"{label}:\n{text}")

    parts: list[str] = []
    if workflow:
        evidence = workflow.environment.simulation.get("evidence", {})
        evidence_items = evidence.get("items", []) if isinstance(evidence, dict) else []
        if isinstance(evidence_items, list) and evidence_items:
            observed = []
            for item in evidence_items[:20]:
                if isinstance(item, dict):
                    observed.append(f"[{str(item.get('name', 'source'))}]\n{str(item.get('content', ''))[:2000]}")
            if observed:
                parts.extend(["## Observed evidence supplied by the user", "\n\n".join(observed)])
    parts.extend(["## Simulated interactions", "\n\n".join(interactions) or "No readable interactions were recorded."])
    if final_outputs:
        parts.extend(["## Final simulation result", "\n\n".join(final_outputs)])
    return "\n\n".join(parts).strip()


def _build_report_evidence_lines(run_detail: RunDetail, metrics: RunMetricsResponse) -> list[str]:
    lines = [
        f"status={run_detail.status}",
        f"duration_ms={metrics.duration_ms}",
        f"completed_nodes={metrics.succeeded_nodes}",
        f"failed_nodes={metrics.failed_nodes}",
        f"interactions={metrics.total_interactions}",
    ]
    for node in run_detail.node_runs:
        text = _readable_output(node.output)
        if text and not node.node_id.startswith(("director_", "report_")):
            lines.append("- " + " ".join(text.split())[:400])
    return lines


def _build_full_log_markdown(run_detail: RunDetail, events: list[RunEvent]) -> str:
    lines: list[str] = [
        f"# Full Simulation Log: {run_detail.id}",
        "",
        "## Run",
        "",
        f"- workflow_id: `{run_detail.workflow_id}`",
        f"- status: `{run_detail.status}`",
        f"- started_at: `{run_detail.started_at.isoformat()}`",
        f"- ended_at: `{run_detail.ended_at.isoformat() if run_detail.ended_at else '-'}`",
        f"- retry_from_run_id: `{run_detail.retry_from_run_id or '-'}`",
        f"- retry_from_node: `{run_detail.retry_from_node or '-'}`",
        "",
        "## Nodes",
        "",
    ]
    if not run_detail.node_runs:
        lines.extend(["No node run records.", ""])
    for node in run_detail.node_runs:
        lines.extend(
            [
                f"### Node `{node.node_id}`",
                "",
                f"- status: `{node.status}`",
                f"- started_at: `{node.started_at.isoformat() if node.started_at else '-'}`",
                f"- ended_at: `{node.ended_at.isoformat() if node.ended_at else '-'}`",
                f"- duration_ms: `{node.duration_ms if node.duration_ms is not None else '-'}`",
                "",
                "Input:",
                "```json",
                json.dumps(node.input, ensure_ascii=False, indent=2, default=str),
                "```",
                "",
                "Output:",
                "```json",
                json.dumps(node.output, ensure_ascii=False, indent=2, default=str),
                "```",
                "",
            ]
        )
        if node.error is not None:
            lines.extend(["Error:", "```json", json.dumps(node.error, ensure_ascii=False, indent=2, default=str), "```", ""])

    lines.extend(["## Events", ""])
    if not events:
        lines.extend(["No run events.", ""])
    for event in events:
        trace = event.payload.get("_trace", {}) if isinstance(event.payload, dict) else {}
        trace_id = trace.get("trace_id", "") if isinstance(trace, dict) else ""
        caused_by = trace.get("caused_by", "") if isinstance(trace, dict) else ""
        parent_ids = trace.get("parent_event_ids", []) if isinstance(trace, dict) else []
        lines.extend(
            [
                f"### Event #{event.seq if event.seq is not None else '-'}",
                "",
                f"- node_id: `{event.node_id}`",
                f"- event: `{event.event}`",
                f"- timestamp: `{event.timestamp.isoformat()}`",
                f"- duration_ms: `{event.duration_ms if event.duration_ms is not None else '-'}`",
                f"- caused_by: `{caused_by or '-'}`",
                f"- trace_id: `{trace_id or '-'}`",
                f"- parent_event_ids: `{', '.join(str(v) for v in parent_ids) if parent_ids else '-'}`",
                "",
                "Payload:",
                "```json",
                json.dumps(event.payload, ensure_ascii=False, indent=2, default=str),
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _extract_sections(markdown: str) -> list[ReportSection]:
    lines = markdown.splitlines()
    sections: list[ReportSection] = []
    current_heading: str | None = None
    current_body: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if current_heading:
                sections.append(
                    ReportSection(
                        heading=current_heading,
                        summary=" ".join(" ".join(current_body).split())[:300],
                        evidence_refs=[],
                    )
                )
            current_heading = line[3:].strip()
            current_body = []
        else:
            if current_heading:
                current_body.append(line.strip())
    if current_heading:
        sections.append(
            ReportSection(
                heading=current_heading,
                summary=" ".join(" ".join(current_body).split())[:300],
                evidence_refs=[],
            )
        )
    return sections


def _default_report_title(run_id: str, mode: str) -> str:
    if mode in {"story", "narrative"}:
        return f"Simulation Narrative - {run_id}"
    return f"Simulation Briefing - {run_id}"


def _fallback_markdown(
    title: str,
    mode: str,
    run_detail: RunDetail,
    metrics: RunMetricsResponse,
    evidence: list[str],
    readable_source: str = "",
) -> str:
    if mode in {"story", "narrative"}:
        return dedent(
            f"""
            ## Simulation adaptation
            {readable_source or "No readable simulation material was recorded."}
            """
        ).strip()
    return dedent(
        f"""
        ## Background
        Run `{run_detail.id}` was executed under workflow `{run_detail.workflow_id}`.

        ## Process Overview
        Total nodes: {metrics.total_nodes}, succeeded: {metrics.succeeded_nodes}, failed: {metrics.failed_nodes}.

        ## Key Findings
        Estimated token usage: {metrics.estimated_token_usage}. Total interactions: {metrics.total_interactions}.

        ## Director Correction Effect
        Guidance count: {metrics.director_guidance_count}. Score delta: {metrics.director_effect.delta_score}.

        ## Risks and Limitations
        This report is generated from run-time logs and heuristic metrics.

        ## Recommendations
        Reduce failed nodes and monitor interaction intensity for more stable runs.
        """
    ).strip()
