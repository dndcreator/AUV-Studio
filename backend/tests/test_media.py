from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.media_service import (
    _SESSION_KEYS,
    MediaServiceError,
    generate_run_media,
    refresh_media_generation,
    save_media_config,
    test_media_config as check_media_config,
)
from app.models import MediaConfigRecord, RunRecord, WorkflowRecord
from app.schemas import MediaConfigRequest, MediaGenerateRequest


class PromptProvider:
    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict:
        return {"content": '{"shots":[{"prompt":"A precise cinematic shot"}]}', "raw": {}}


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def add_run(db: Session) -> None:
    definition = {
        "id": "wf_media",
        "name": "Media",
        "version": 1,
        "nodes": [],
        "edges": [],
        "entry_nodes": [],
        "environment": {"profile": "", "scenario": "", "facts": [], "constraints": [], "glossary": {}},
    }
    db.add(WorkflowRecord(id="wf_media", name="Media", version=1, definition_json=json.dumps(definition)))
    db.add(RunRecord(id="run_media", workflow_id="wf_media", status="succeeded", input_json="{}", output_json="{}"))
    db.commit()


def response(method: str, url: str, payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request(method, url))


def test_media_config_never_persists_api_key() -> None:
    db = make_db()
    _SESSION_KEYS.clear()
    saved = save_media_config(
        db,
        MediaConfigRequest(
            media_type="image",
            provider="openai_compatible",
            base_url="https://images.example/v1",
            model="image-model",
            api_key="secret-media-key",
            endpoint_path="/images/generations",
        ),
    )
    row = db.query(MediaConfigRecord).one()
    assert saved.has_api_key is True
    assert saved.masked_api_key != "secret-media-key"
    assert "api_key" not in row.__table__.columns


def test_media_key_is_not_reused_for_a_different_endpoint(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = make_db()
    _SESSION_KEYS.clear()
    save_media_config(
        db,
        MediaConfigRequest(
            media_type="image",
            provider="openai_compatible",
            base_url="https://api.openai.com/v1",
            model="gpt-image-1",
            api_key="openai-secret",
        ),
    )
    captured: dict[str, object] = {}

    class Client:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def get(self, url: str, headers: dict):  # type: ignore[override]
            captured["headers"] = headers
            return response("GET", url, {})

    monkeypatch.setattr("app.media_service.httpx.AsyncClient", Client)
    result = asyncio.run(check_media_config(
        db,
        MediaConfigRequest(
            media_type="image",
            provider="openai_compatible",
            base_url="https://other-provider.example/v1",
            model="image-model",
        ),
    ))
    assert result.ok is False
    assert result.message == "API key is missing."
    assert captured == {}


def test_media_config_rejects_protocol_mismatch() -> None:
    db = make_db()
    with pytest.raises(MediaServiceError, match="Video generation"):
        save_media_config(
            db,
            MediaConfigRequest(
                media_type="video",
                provider="openai_compatible",
                base_url="https://images.example/v1",
                model="image-model",
            ),
        )


def test_openai_compatible_image_generation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = make_db()
    add_run(db)
    _SESSION_KEYS.clear()
    save_media_config(
        db,
        MediaConfigRequest(
            media_type="image",
            provider="openai_compatible",
            base_url="https://images.example/v1",
            model="image-model",
            api_key="key",
            endpoint_path="/images/generations",
        ),
    )
    called: dict[str, object] = {}

    class Client:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, headers: dict, json: dict):  # type: ignore[override]
            called.update(url=url, headers=headers, json=json)
            return response("POST", url, {"data": [{"url": "https://cdn.example/frame.png"}]})

    monkeypatch.setattr("app.media_service.httpx.AsyncClient", Client)
    result = asyncio.run(
        generate_run_media(
            db,
            PromptProvider(),  # type: ignore[arg-type]
            "run_media",
            MediaGenerateRequest(media_type="image", source_text="A completed screenplay", shot_count=1),
        )
    )
    assert result.status == "completed"
    assert result.assets[0].url == "https://cdn.example/frame.png"
    assert called["url"] == "https://images.example/v1/images/generations"
    assert called["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer key"}


def test_openai_video_job_refresh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = make_db()
    add_run(db)
    _SESSION_KEYS.clear()
    save_media_config(
        db,
        MediaConfigRequest(
            media_type="video",
            provider="openai_video",
            base_url="https://video.example/v1",
            model="video-model",
            api_key="key",
            endpoint_path="/videos",
        ),
    )

    class Client:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, headers: dict, files: dict):  # type: ignore[override]
            return response("POST", url, {"id": "video_job_1", "status": "queued"})

        async def get(self, url: str, headers: dict):  # type: ignore[override]
            return response("GET", url, {"id": "video_job_1", "status": "completed"})

    monkeypatch.setattr("app.media_service.httpx.AsyncClient", Client)
    created = asyncio.run(
        generate_run_media(
            db,
            PromptProvider(),  # type: ignore[arg-type]
            "run_media",
            MediaGenerateRequest(media_type="video", source_text="A completed screenplay", seconds=4),
        )
    )
    assert created.status == "queued"
    completed = asyncio.run(refresh_media_generation(db, created.generation_id))
    assert completed.status == "completed"
    assert completed.assets[0].url.endswith(f"/{created.generation_id}/assets/asset_1/content")


def test_generic_media_adapter_contract(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = make_db()
    add_run(db)
    _SESSION_KEYS.clear()
    save_media_config(
        db,
        MediaConfigRequest(
            media_type="image",
            provider="generic_http",
            base_url="http://127.0.0.1:8188",
            model="workflow-a",
            endpoint_path="/generate",
        ),
    )
    posted: dict[str, object] = {}

    class Client:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, headers: dict, json: dict):  # type: ignore[override]
            posted.update(url=url, json=json)
            return response("POST", url, {"status": "completed", "assets": [{"url": "http://127.0.0.1/output.png", "mime_type": "image/png"}]})

    monkeypatch.setattr("app.media_service.httpx.AsyncClient", Client)
    result = asyncio.run(
        generate_run_media(
            db,
            PromptProvider(),  # type: ignore[arg-type]
            "run_media",
            MediaGenerateRequest(media_type="image", source_text="Source"),
        )
    )
    assert result.status == "completed"
    assert posted["url"] == "http://127.0.0.1:8188/generate"
    assert posted["json"] == {
        "media_type": "image",
        "model": "workflow-a",
        "prompt": "A precise cinematic shot",
        "options": {"size": "1024x1024", "quality": "standard", "seconds": 4},
    }


def test_generic_adapter_normalizes_nested_image_response(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = make_db()
    add_run(db)
    _SESSION_KEYS.clear()
    save_media_config(
        db,
        MediaConfigRequest(
            media_type="image",
            provider="generic_http",
            base_url="http://127.0.0.1:8188",
            model="workflow-a",
        ),
    )

    class Client:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def post(self, url: str, headers: dict, json: dict):  # type: ignore[override]
            return response("POST", url, {"status": "done", "result": {"images": ["http://127.0.0.1/result.png"]}})

    monkeypatch.setattr("app.media_service.httpx.AsyncClient", Client)
    result = asyncio.run(
        generate_run_media(
            db,
            PromptProvider(),  # type: ignore[arg-type]
            "run_media",
            MediaGenerateRequest(media_type="image", source_text="Source"),
        )
    )
    assert result.status == "completed"
    assert result.assets[0].url == "http://127.0.0.1/result.png"
