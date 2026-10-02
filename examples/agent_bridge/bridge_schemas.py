from __future__ import annotations

from pydantic import BaseModel, Field


class BridgeTaskInput(BaseModel):
    text: str = ""
    raw_prompt: str = ""
    background: dict = Field(default_factory=dict)


class AUVTaskRequest(BaseModel):
    protocol_version: str = "1.0"
    task_id: str
    run_id: str
    node_id: str
    context: dict = Field(default_factory=dict)
    input: BridgeTaskInput = Field(default_factory=BridgeTaskInput)
    constraints: dict = Field(default_factory=dict)
    reply_mode: str = "sync"


class AUVTaskResponse(BaseModel):
    task_id: str
    status: str
    output: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    errors: list[dict] = Field(default_factory=list)
