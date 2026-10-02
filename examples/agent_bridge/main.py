from __future__ import annotations

import time

from fastapi import FastAPI

from bridge_schemas import AUVTaskRequest, AUVTaskResponse
from handler import handle_task

app = FastAPI(title="AUV Agent Bridge", version="1.0.0")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/agent/tasks", response_model=AUVTaskResponse)
def submit_task(req: AUVTaskRequest) -> AUVTaskResponse:
    start = time.perf_counter()
    try:
        result = handle_task(req.input)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return AUVTaskResponse(
            task_id=req.task_id,
            status="succeeded",
            output={"text": str(result.get("text", "")), "raw": result},
            metrics={"latency_ms": elapsed_ms},
            errors=[],
        )
    except Exception as exc:  # noqa: BLE001
        return AUVTaskResponse(
            task_id=req.task_id,
            status="failed",
            output={},
            metrics={},
            errors=[{"message": str(exc)}],
        )
