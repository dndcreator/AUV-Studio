from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from urllib.parse import unquote

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .execution.engine import WorkflowEngine
from .execution.provider import build_provider
from .evidence import parse_evidence_upload
from .models import RunRecord
from .media_service import (
    MediaServiceError,
    download_media_asset,
    generate_run_media,
    get_media_config,
    refresh_media_generation,
    save_media_config,
    test_media_config,
)
from .node_specs import get_interaction_modes, get_node_specs
from .observability import perf_tracker
from .queue_manager import QueueTask, RunQueueManager
from .schemas import (
    AuditVerifyResponse,
    DirectorCapability,
    DirectorCommandRequest,
    DirectorCommandResponse,
    EventsResponse,
    FullLogResponse,
    NodeSpec,
    ObservabilitySnapshot,
    QueueStatusResponse,
    HumanResponseRequest,
    MemoryClearResponse,
    MemoryItem,
    MediaConfigRequest,
    MediaConfigResponse,
    MediaConfigTestResponse,
    MediaGenerateRequest,
    MediaGenerationResponse,
    MediaType,
    ModelConfigRequest,
    ModelConfigResponse,
    ModelConfigTestRequest,
    ModelConfigTestResponse,
    ModeContract,
    RollbackRequest,
    RollbackResponse,
    RunReportRequest,
    RunReportResponse,
    PlanCompileRequest,
    PlanCompileResponse,
    RunCheckpointsResponse,
    RetryRequest,
    RunCompareResponse,
    RunDetail,
    RunMetricsResponse,
    TraceEventContextResponse,
    RunRequest,
    TemplateCreateRequest,
    TemplateDetail,
    TemplateSummary,
    WorkflowDefinition,
    WorkflowSummary,
)
from .service import (
    apply_director_command,
    apply_stored_model_config,
    compare_runs,
    compile_workflow_from_plan,
    clear_memories,
    create_or_update_template,
    create_workflow,
    generate_run_report,
    get_events,
    get_full_log,
    get_observability_snapshot,
    get_mode_contracts,
    get_model_config,
    get_director_capabilities,
    build_rollback_request,
    get_run,
    get_run_checkpoints,
    get_trace_event_context,
    get_run_metrics,
    verify_run_audit,
    list_memories,
    get_template,
    get_workflow,
    list_templates,
    list_workflows,
    save_model_config,
    request_run_stop,
    test_model_config,
    upsert_workflow,
)

provider = build_provider()
workflow_engine = WorkflowEngine(provider=provider)
run_queue_manager = RunQueueManager(worker_count=settings.run_worker_count, max_size=settings.run_queue_max_size)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        apply_stored_model_config(db, provider)
    finally:
        db.close()
    run_queue_manager.start(workflow_engine)
    try:
        yield
    finally:
        await run_queue_manager.stop()


app = FastAPI(title="AUV Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def perf_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    start = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        latency_ms = int((perf_counter() - start) * 1000)
        perf_tracker.record_http(
            path=request.url.path,
            method=request.method,
            status_code=status_code,
            latency_ms=latency_ms,
        )


def _mark_run_failed_queue_full(db: Session, rec: RunRecord) -> None:
    rec.status = "failed"
    rec.error_json = json.dumps({"code": "QUEUE_FULL", "message": "run queue is full"})
    rec.ended_at = datetime.now(timezone.utc)
    db.add(rec)
    db.commit()


def _enqueue_or_429(*, run_id: str, workflow: WorkflowDefinition, request: RunRequest) -> None:
    try:
        run_queue_manager.enqueue_nowait(
            QueueTask(
                run_id=run_id,
                workflow=workflow,
                request=request,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
    except asyncio.QueueFull as exc:
        raise HTTPException(status_code=429, detail="run queue is full, please retry later") from exc


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/model-config", response_model=ModelConfigResponse)
def get_model_config_api(db: Session = Depends(get_db)) -> ModelConfigResponse:
    return get_model_config(db)


@app.put("/api/model-config", response_model=ModelConfigResponse)
def save_model_config_api(body: ModelConfigRequest, db: Session = Depends(get_db)) -> ModelConfigResponse:
    return save_model_config(db=db, provider=provider, req=body)


@app.post("/api/model-config/test", response_model=ModelConfigTestResponse)
async def test_model_config_api(body: ModelConfigTestRequest, db: Session = Depends(get_db)) -> ModelConfigTestResponse:
    return await test_model_config(db=db, req=body)


@app.get("/api/media-config/{media_type}", response_model=MediaConfigResponse)
def get_media_config_api(media_type: MediaType, db: Session = Depends(get_db)) -> MediaConfigResponse:
    return get_media_config(db, media_type)


@app.put("/api/media-config/{media_type}", response_model=MediaConfigResponse)
def save_media_config_api(
    media_type: MediaType,
    body: MediaConfigRequest,
    db: Session = Depends(get_db),
) -> MediaConfigResponse:
    try:
        request = body.model_copy(update={"media_type": media_type})
        return save_media_config(db, request)
    except MediaServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/media-config/{media_type}/test", response_model=MediaConfigTestResponse)
async def test_media_config_api(
    media_type: MediaType,
    body: MediaConfigRequest,
    db: Session = Depends(get_db),
) -> MediaConfigTestResponse:
    try:
        request = body.model_copy(update={"media_type": media_type})
        return await test_media_config(db, request)
    except MediaServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/queue/status", response_model=QueueStatusResponse)
def queue_status_api() -> QueueStatusResponse:
    return QueueStatusResponse.model_validate(run_queue_manager.snapshot())


@app.get("/api/observability", response_model=ObservabilitySnapshot)
def observability_api(db: Session = Depends(get_db)) -> ObservabilitySnapshot:
    return get_observability_snapshot(db)


@app.get("/api/node-specs", response_model=list[NodeSpec])
def node_specs_api() -> list[NodeSpec]:
    return get_node_specs()


@app.get("/api/interaction-modes", response_model=list[str])
def interaction_modes_api() -> list[str]:
    return get_interaction_modes()


@app.get("/api/director/capabilities", response_model=list[DirectorCapability])
def director_capabilities_api() -> list[DirectorCapability]:
    return get_director_capabilities()


@app.get("/api/mode-contracts", response_model=list[ModeContract])
def mode_contracts_api() -> list[ModeContract]:
    return get_mode_contracts()


@app.get("/api/protocols/text-agent-v1")
def text_agent_protocol_api() -> dict[str, Any]:
    return {
        "name": "AUV Text Agent Protocol",
        "version": "1.0",
        "task_submit": {
            "method": "POST",
            "path": "/agent/tasks",
            "request_fields": [
                "protocol_version",
                "task_id",
                "run_id",
                "node_id",
                "context.environment",
                "context.global_guidance",
                "context.interactions",
                "context.task_goal",
                "input.text (enriched prompt)",
                "input.raw_prompt",
                "input.background.environment",
                "input.background.global_guidance",
                "input.background.interactions",
                "input.background.memory",
                "constraints.max_tokens",
                "constraints.timeout_ms",
            ],
            "response_fields": [
                "task_id",
                "status",
                "output.text",
                "metrics.latency_ms",
                "metrics.token_in",
                "metrics.token_out",
                "errors",
            ],
        },
        "supported_reply_mode": ["sync"],
        "accepts": ["text/plain"],
        "produces": ["text/plain"],
    }


@app.get("/api/templates", response_model=list[TemplateSummary])
def list_templates_api(db: Session = Depends(get_db)) -> list[TemplateSummary]:
    return list_templates(db)


@app.get("/api/templates/{template_id}", response_model=TemplateDetail)
def get_template_api(template_id: str, db: Session = Depends(get_db)) -> TemplateDetail:
    return get_template(db, template_id)


@app.post("/api/templates", response_model=TemplateDetail)
def create_template_api(body: TemplateCreateRequest, db: Session = Depends(get_db)) -> TemplateDetail:
    return create_or_update_template(db, body)


@app.post("/api/templates/compile", response_model=PlanCompileResponse)
async def compile_template_api(request: PlanCompileRequest) -> PlanCompileResponse:
    return await compile_workflow_from_plan(provider=provider, request=request)


@app.post("/api/evidence/parse")
async def parse_evidence_api(
    data: bytes = Body(media_type="application/octet-stream"),
    x_filename: str = Header(default="evidence.zip"),
) -> dict[str, Any]:
    try:
        return parse_evidence_upload(unquote(x_filename), data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/workflows/{workflow_id}/memories", response_model=list[MemoryItem])
def list_memories_api(
    workflow_id: str,
    run_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[MemoryItem]:
    _ = get_workflow(db, workflow_id)
    return list_memories(db, workflow_id=workflow_id, run_id=run_id, limit=limit)


@app.delete("/api/workflows/{workflow_id}/memories", response_model=MemoryClearResponse)
def clear_memories_api(
    workflow_id: str,
    run_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> MemoryClearResponse:
    _ = get_workflow(db, workflow_id)
    return clear_memories(db, workflow_id=workflow_id, run_id=run_id)


@app.post("/api/workflows", response_model=WorkflowDefinition)
def create_workflow_api(workflow: WorkflowDefinition, db: Session = Depends(get_db)) -> WorkflowDefinition:
    return create_workflow(db, workflow)


@app.get("/api/workflows", response_model=list[WorkflowSummary])
def list_workflows_api(db: Session = Depends(get_db)) -> list[WorkflowSummary]:
    return list_workflows(db)


@app.get("/api/workflows/{workflow_id}", response_model=WorkflowDefinition)
def get_workflow_api(workflow_id: str, db: Session = Depends(get_db)) -> WorkflowDefinition:
    return get_workflow(db, workflow_id)


@app.put("/api/workflows/{workflow_id}", response_model=WorkflowDefinition)
def update_workflow_api(workflow_id: str, workflow: WorkflowDefinition, db: Session = Depends(get_db)) -> WorkflowDefinition:
    if workflow.id != workflow_id:
        raise HTTPException(status_code=400, detail="workflow id mismatch")
    return upsert_workflow(db, workflow)


@app.post("/api/workflows/{workflow_id}/run")
async def run_workflow_api(workflow_id: str, request: RunRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    workflow = get_workflow(db, workflow_id)
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    rec = RunRecord(
        id=run_id,
        workflow_id=workflow_id,
        status="pending",
        input_json="{}",
        output_json="{}",
        error_json="null",
        retry_from_run_id=request.retry_from_run_id,
        retry_from_node=request.retry_from_node,
        started_at=datetime.now(timezone.utc),
    )
    db.add(rec)
    db.commit()
    try:
        _enqueue_or_429(run_id=run_id, workflow=workflow, request=request)
    except HTTPException:
        _mark_run_failed_queue_full(db, rec)
        raise
    return {"run_id": run_id, "status": "queued"}


@app.post("/api/runs/{run_id}/retry")
async def retry_run_api(run_id: str, body: RetryRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    source = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="run not found")
    workflow = get_workflow(db, source.workflow_id)
    request = RunRequest(
        input={},
        retry_from_run_id=run_id,
        retry_from_node=body.node_id,
    )
    new_run_id = f"run_{uuid.uuid4().hex[:12]}"
    rec = RunRecord(
        id=new_run_id,
        workflow_id=source.workflow_id,
        status="pending",
        input_json="{}",
        output_json="{}",
        error_json="null",
        retry_from_run_id=run_id,
        retry_from_node=body.node_id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(rec)
    db.commit()
    try:
        _enqueue_or_429(run_id=new_run_id, workflow=workflow, request=request)
    except HTTPException:
        _mark_run_failed_queue_full(db, rec)
        raise
    return {"run_id": new_run_id, "status": "queued"}


@app.get("/api/runs/{run_id}/checkpoints", response_model=RunCheckpointsResponse)
def get_run_checkpoints_api(
    run_id: str,
    limit: int = Query(default=80, ge=1, le=300),
    db: Session = Depends(get_db),
) -> RunCheckpointsResponse:
    return get_run_checkpoints(db, run_id=run_id, limit=limit)


@app.post("/api/runs/{run_id}/rollback", response_model=RollbackResponse)
async def rollback_run_api(run_id: str, body: RollbackRequest, db: Session = Depends(get_db)) -> RollbackResponse:
    workflow_id, request = build_rollback_request(db, run_id=run_id, request=body)
    workflow = get_workflow(db, workflow_id)
    new_run_id = f"run_{uuid.uuid4().hex[:12]}"
    rec = RunRecord(
        id=new_run_id,
        workflow_id=workflow_id,
        status="pending",
        input_json=json.dumps(request.input, ensure_ascii=False),
        output_json="{}",
        error_json="null",
        retry_from_run_id=run_id,
        retry_from_node=request.retry_from_node,
        started_at=datetime.now(timezone.utc),
    )
    db.add(rec)
    db.commit()
    try:
        _enqueue_or_429(run_id=new_run_id, workflow=workflow, request=request)
    except HTTPException:
        _mark_run_failed_queue_full(db, rec)
        raise
    return RollbackResponse(
        run_id=new_run_id,
        status="queued",
        source_run_id=run_id,
        event_seq=body.event_seq,
        retry_from_node=request.retry_from_node or "",
    )


@app.post("/api/runs/{run_id}/human-response")
async def human_response_api(run_id: str, body: HumanResponseRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    source = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="run not found")
    if source.status != "waiting_human":
        raise HTTPException(status_code=409, detail="run is not waiting for human intervention")
    checkpoint_node = body.node_id or source.retry_from_node
    if not checkpoint_node:
        try:
            checkpoint_node = str((json.loads(source.error_json) or {}).get("node_id") or "")
        except Exception:  # noqa: BLE001
            checkpoint_node = ""
    if not checkpoint_node:
        raise HTTPException(status_code=400, detail="node_id is required for human response")

    workflow = get_workflow(db, source.workflow_id)
    request = RunRequest(
        input={
            "_human_response": {
                "node_id": checkpoint_node,
                "response": body.response,
                "responder": body.responder,
                "metadata": body.metadata,
            }
        },
        retry_from_run_id=run_id,
        retry_from_node=checkpoint_node,
    )
    _enqueue_or_429(run_id=run_id, workflow=workflow, request=request)
    source.status = "pending"
    source.error_json = "null"
    source.retry_from_run_id = run_id
    source.retry_from_node = checkpoint_node
    source.ended_at = None
    db.add(source)
    db.commit()
    return {"run_id": run_id, "status": "queued"}


@app.post("/api/runs/{run_id}/director-command", response_model=DirectorCommandResponse)
def director_command_api(run_id: str, body: DirectorCommandRequest, db: Session = Depends(get_db)) -> DirectorCommandResponse:
    return apply_director_command(db=db, run_id=run_id, req=body)


@app.post("/api/runs/{run_id}/stop")
def stop_run_api(run_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    return request_run_stop(db, run_id)


@app.get("/api/runs/{run_id}", response_model=RunDetail)
def get_run_api(run_id: str, db: Session = Depends(get_db)) -> RunDetail:
    return get_run(db, run_id)


@app.get("/api/runs/{run_id}/events", response_model=EventsResponse)
def get_run_events_api(
    run_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> EventsResponse:
    return get_events(db, run_id=run_id, after_seq=after_seq, limit=limit)


@app.get("/api/runs/{run_id}/full-log", response_model=FullLogResponse)
def get_run_full_log_api(
    run_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json)$"),
    db: Session = Depends(get_db),
) -> FullLogResponse:
    return get_full_log(db, run_id=run_id, output_format=format)


@app.get("/api/runs/{run_id}/trace/{seq}", response_model=TraceEventContextResponse)
def get_run_trace_event_api(run_id: str, seq: int, db: Session = Depends(get_db)) -> TraceEventContextResponse:
    return get_trace_event_context(db, run_id=run_id, seq=seq)


@app.get("/api/runs/{run_id}/audit/verify", response_model=AuditVerifyResponse)
def verify_run_audit_api(run_id: str, db: Session = Depends(get_db)) -> AuditVerifyResponse:
    return verify_run_audit(db, run_id=run_id)


@app.get("/api/runs/{run_id}/metrics", response_model=RunMetricsResponse)
def get_run_metrics_api(run_id: str, db: Session = Depends(get_db)) -> RunMetricsResponse:
    return get_run_metrics(db, run_id)


@app.get("/api/compare-runs", response_model=RunCompareResponse)
def compare_runs_api(
    run_a: str = Query(..., min_length=1),
    run_b: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> RunCompareResponse:
    return compare_runs(db, run_a=run_a, run_b=run_b)


@app.post("/api/runs/{run_id}/report", response_model=RunReportResponse)
async def generate_run_report_api(run_id: str, request: RunReportRequest, db: Session = Depends(get_db)) -> RunReportResponse:
    return await generate_run_report(db=db, provider=provider, run_id=run_id, request=request)


@app.post("/api/runs/{run_id}/media", response_model=MediaGenerationResponse)
async def generate_run_media_api(
    run_id: str,
    request: MediaGenerateRequest,
    db: Session = Depends(get_db),
) -> MediaGenerationResponse:
    try:
        return await generate_run_media(db=db, text_provider=provider, run_id=run_id, request=request)
    except MediaServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/media/generations/{generation_id}", response_model=MediaGenerationResponse)
async def get_media_generation_api(generation_id: str, db: Session = Depends(get_db)) -> MediaGenerationResponse:
    try:
        return await refresh_media_generation(db, generation_id)
    except MediaServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/media/generations/{generation_id}/assets/{asset_id}/content")
async def get_media_asset_api(generation_id: str, asset_id: str, db: Session = Depends(get_db)) -> Response:
    try:
        content, media_type, filename = await download_media_asset(db, generation_id, asset_id)
    except MediaServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
