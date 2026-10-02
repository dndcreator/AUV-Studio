from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .execution.provider import OpenAICompatibleProvider, provider_accepts_keyless
from .models import MediaConfigRecord, MediaGenerationRecord, RunRecord, WorkflowRecord
from .schemas import (
    MediaAsset,
    MediaConfigRequest,
    MediaConfigResponse,
    MediaConfigTestResponse,
    MediaGenerateRequest,
    MediaGenerationResponse,
    MediaProvider,
    MediaType,
    WorkflowDefinition,
)
from .service import _build_readable_report_source, get_run


class MediaServiceError(RuntimeError):
    pass


_SESSION_KEYS: dict[str, tuple[str, str, str]] = {}


def _defaults(media_type: MediaType) -> tuple[MediaProvider, str, str, str, str]:
    if media_type == "video":
        return "openai_video", settings.openai_base_url, "sora-2", "/videos", ""
    return "openai_compatible", settings.openai_base_url, "gpt-image-1", "/images/generations", ""


def _provider_paths(media_type: MediaType, provider: MediaProvider) -> tuple[str, str]:
    if provider == "generic_http":
        return "/generate", "/jobs/{job_id}"
    if provider == "openai_video":
        return "/videos", ""
    return "/images/generations", ""


def _validate_provider(media_type: MediaType, provider: MediaProvider) -> None:
    if media_type == "image" and provider == "openai_video":
        raise MediaServiceError("Image generation cannot use the OpenAI video protocol.")
    if media_type == "video" and provider == "openai_compatible":
        raise MediaServiceError("Video generation must use OpenAI video or a generic HTTP adapter.")


def _environment_key(media_type: MediaType) -> str:
    return settings.video_api_key if media_type == "video" else settings.image_api_key


def _key_for(media_type: MediaType, provider: MediaProvider, base_url: str) -> tuple[str, str]:
    saved = _SESSION_KEYS.get(media_type)
    normalized_base = base_url.rstrip("/")
    if saved and saved[0] == provider and saved[1] == normalized_base:
        return saved[2], "session"
    default_provider, default_base, _, _, _ = _defaults(media_type)
    if provider == default_provider and normalized_base == default_base.rstrip("/"):
        key = _environment_key(media_type)
        if key:
            return key, "environment"
    return "", ""


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    return "****" if len(value) <= 8 else f"{value[:4]}...{value[-4:]}"


def _validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MediaServiceError("Media base URL must use http or https.")
    return url


def _config_values(db: Session, media_type: MediaType) -> tuple[MediaProvider, str, str, str, str, str, str]:
    default_provider, default_base, default_model, default_endpoint, default_status = _defaults(media_type)
    row = db.query(MediaConfigRecord).filter(MediaConfigRecord.media_type == media_type).first()
    if row is None:
        key, key_source = _key_for(media_type, default_provider, default_base)
        source = "environment" if key_source == "environment" else "default"
        return default_provider, default_base, default_model, default_endpoint, default_status, key, source
    key, _ = _key_for(media_type, row.provider, row.base_url)  # type: ignore[arg-type]
    return row.provider, row.base_url, row.model, row.endpoint_path, row.status_path, key, "database"  # type: ignore[return-value]


def get_media_config(db: Session, media_type: MediaType) -> MediaConfigResponse:
    provider, base_url, model, endpoint_path, status_path, key, source = _config_values(db, media_type)
    requires_key = provider != "generic_http" and not provider_accepts_keyless("openai_compatible", base_url)
    return MediaConfigResponse(
        media_type=media_type,
        provider=provider,
        base_url=base_url,
        model=model,
        endpoint_path=endpoint_path,
        status_path=status_path,
        has_api_key=bool(key),
        masked_api_key=_mask_secret(key),
        requires_api_key=requires_key,
        source=source,  # type: ignore[arg-type]
    )


def save_media_config(db: Session, request: MediaConfigRequest) -> MediaConfigResponse:
    _validate_provider(request.media_type, request.provider)
    base_url = _validate_base_url(request.base_url)
    model = request.model.strip()
    if not model:
        raise MediaServiceError("Media model is required.")
    default_endpoint, default_status = _provider_paths(request.media_type, request.provider)
    row = db.query(MediaConfigRecord).filter(MediaConfigRecord.media_type == request.media_type).first()
    if row is None:
        row = MediaConfigRecord(media_type=request.media_type)
    row.provider = request.provider
    row.base_url = base_url
    row.model = model
    row.endpoint_path = request.endpoint_path.strip() or default_endpoint
    row.status_path = request.status_path.strip() or default_status
    if request.api_key is not None:
        if request.api_key.strip():
            _SESSION_KEYS[request.media_type] = (request.provider, base_url, request.api_key.strip())
        else:
            _SESSION_KEYS.pop(request.media_type, None)
    db.add(row)
    db.commit()
    return get_media_config(db, request.media_type)


async def test_media_config(db: Session, request: MediaConfigRequest) -> MediaConfigTestResponse:
    _validate_provider(request.media_type, request.provider)
    base_url = _validate_base_url(request.base_url)
    saved_key, _ = _key_for(request.media_type, request.provider, base_url)
    key = (request.api_key or "").strip() or saved_key
    if request.provider != "generic_http" and not key and not provider_accepts_keyless("openai_compatible", base_url):
        return MediaConfigTestResponse(ok=False, message="API key is missing.")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    target = base_url if request.provider == "generic_http" else f"{base_url}/models/{quote(request.model.strip(), safe='')}"
    start = perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(target, headers=headers)
        if response.status_code >= 500 or response.status_code in {401, 403}:
            return MediaConfigTestResponse(
                ok=False,
                message=f"Media endpoint returned HTTP {response.status_code}.",
                latency_ms=int((perf_counter() - start) * 1000),
            )
    except Exception as exc:  # noqa: BLE001
        return MediaConfigTestResponse(ok=False, message=f"Media connection failed: {exc}")
    return MediaConfigTestResponse(ok=True, message="Media endpoint is reachable.", latency_ms=int((perf_counter() - start) * 1000))


async def generate_run_media(
    db: Session,
    text_provider: OpenAICompatibleProvider,
    run_id: str,
    request: MediaGenerateRequest,
) -> MediaGenerationResponse:
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run is None:
        raise MediaServiceError(f"Run not found: {run_id}")
    provider, base_url, model, endpoint_path, status_path, key, _ = _config_values(db, request.media_type)
    if provider != "generic_http" and not key and not provider_accepts_keyless("openai_compatible", base_url):
        raise MediaServiceError(f"{request.media_type} API key is missing")
    source = request.source_text.strip() or _run_source(db, run_id)
    if not source:
        raise MediaServiceError("No readable report, script, or run material is available.")
    prompts = await _plan_media_prompts(
        text_provider=text_provider,
        source=source,
        media_type=request.media_type,
        style=request.style_prompt,
        shot_count=request.shot_count,
    )
    generation_id = f"media_{uuid.uuid4().hex[:12]}"
    record = MediaGenerationRecord(
        id=generation_id,
        run_id=run_id,
        media_type=request.media_type,
        provider=provider,
        model=model,
        status="in_progress",
        prompts_json=json.dumps(prompts, ensure_ascii=False),
        output_json="{}",
        error_json="null",
    )
    db.add(record)
    db.commit()

    assets: list[dict[str, Any]] = []
    try:
        for index, prompt in enumerate(prompts):
            asset_id = f"asset_{index + 1}"
            if provider == "openai_compatible":
                asset = await _generate_openai_image(
                    generation_id=generation_id,
                    asset_id=asset_id,
                    base_url=base_url,
                    endpoint_path=endpoint_path,
                    key=key,
                    model=model,
                    prompt=prompt,
                    request=request,
                )
            elif provider == "openai_video":
                asset = await _create_openai_video(
                    asset_id=asset_id,
                    base_url=base_url,
                    endpoint_path=endpoint_path,
                    key=key,
                    model=model,
                    prompt=prompt,
                    request=request,
                )
            else:
                asset = await _generate_generic(
                    asset_id=asset_id,
                    media_type=request.media_type,
                    base_url=base_url,
                    endpoint_path=endpoint_path,
                    key=key,
                    model=model,
                    prompt=prompt,
                    request=request,
                )
            assets.append(asset)
        record.status = _generation_status(assets)
        record.output_json = json.dumps({"assets": assets, "status_path": status_path}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        record.status = "failed"
        record.error_json = json.dumps({"message": str(exc)}, ensure_ascii=False)
        db.add(record)
        db.commit()
        raise MediaServiceError(str(exc)) from exc
    db.add(record)
    db.commit()
    return _record_response(record)


async def refresh_media_generation(db: Session, generation_id: str) -> MediaGenerationResponse:
    record = db.query(MediaGenerationRecord).filter(MediaGenerationRecord.id == generation_id).first()
    if record is None:
        raise MediaServiceError(f"Media generation not found: {generation_id}")
    if record.status not in {"queued", "in_progress", "partial"}:
        return _record_response(record)
    provider, base_url, _, _, configured_status_path, key, _ = _config_values(db, record.media_type)  # type: ignore[arg-type]
    if provider != record.provider:
        raise MediaServiceError("Media provider configuration changed while jobs were running.")
    output = _json_object(record.output_json)
    assets = output.get("assets", []) if isinstance(output.get("assets", []), list) else []
    status_path = str(output.get("status_path") or configured_status_path)
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("status") not in {"queued", "in_progress"}:
            continue
        if provider == "openai_video":
            await _refresh_openai_video(asset=asset, generation_id=generation_id, base_url=base_url, key=key)
        elif provider == "generic_http" and status_path and asset.get("job_id"):
            await _refresh_generic(asset=asset, base_url=base_url, status_path=status_path, key=key)
    record.status = _generation_status(assets)
    record.output_json = json.dumps({**output, "assets": assets}, ensure_ascii=False)
    db.add(record)
    db.commit()
    return _record_response(record)


async def download_media_asset(db: Session, generation_id: str, asset_id: str) -> tuple[bytes, str, str]:
    record = db.query(MediaGenerationRecord).filter(MediaGenerationRecord.id == generation_id).first()
    if record is None:
        raise MediaServiceError(f"Media generation not found: {generation_id}")
    output = _json_object(record.output_json)
    asset = next((item for item in output.get("assets", []) if isinstance(item, dict) and item.get("asset_id") == asset_id), None)
    if asset is None:
        raise MediaServiceError(f"Media asset not found: {asset_id}")
    local_path = str(asset.get("local_path", ""))
    if local_path:
        root = Path(settings.media_output_dir).resolve()
        path = Path(local_path).resolve()
        if root not in path.parents or not path.is_file():
            raise MediaServiceError("Media asset path is invalid.")
        return path.read_bytes(), str(asset.get("mime_type") or "application/octet-stream"), path.name
    if record.provider == "openai_video" and asset.get("job_id"):
        _, base_url, _, _, _, key, _ = _config_values(db, "video")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.get(f"{base_url}/videos/{asset['job_id']}/content", headers=headers)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "video/mp4"), f"{asset_id}.mp4"
    raise MediaServiceError("This asset is hosted by its provider and cannot be proxied.")


def _run_source(db: Session, run_id: str) -> str:
    detail = get_run(db, run_id)
    workflow_record = db.query(WorkflowRecord).filter(WorkflowRecord.id == detail.workflow_id).first()
    workflow = WorkflowDefinition.model_validate(json.loads(workflow_record.definition_json)) if workflow_record else None
    return _build_readable_report_source(run_detail=detail, workflow=workflow)


async def _plan_media_prompts(
    *, text_provider: OpenAICompatibleProvider, source: str, media_type: MediaType, style: str, shot_count: int
) -> list[str]:
    prompt = (
        f"Convert the source into exactly {shot_count} standalone {media_type} generation prompts. "
        "Preserve named characters, setting, chronology, visual continuity, and concrete actions. "
        "Do not include analysis or markdown. Return JSON only as "
        '{"shots":[{"prompt":"..."}]}.\n\n'
        f"STYLE:\n{style or 'cinematic, coherent, production-ready'}\n\nSOURCE:\n{source[:18000]}"
    )
    try:
        response = await text_provider.chat(
            model="",
            system_prompt="You are a visual adaptation planner. Produce model-ready prompts grounded in the supplied work.",
            user_prompt=prompt,
        )
        parsed = _extract_json(str(response.get("content", "")))
        shots = parsed.get("shots", []) if isinstance(parsed, dict) else []
        prompts = [str(item.get("prompt", "")).strip() for item in shots if isinstance(item, dict) and str(item.get("prompt", "")).strip()]
        if prompts:
            return prompts[:shot_count]
    except Exception:  # noqa: BLE001
        pass
    fallback = f"{style.strip()}\n\n{source[:6000]}".strip()
    return [fallback for _ in range(shot_count)]


async def _generate_openai_image(**kwargs: Any) -> dict[str, Any]:
    base_url = kwargs["base_url"]
    endpoint = kwargs["endpoint_path"] or "/images/generations"
    key = kwargs["key"]
    request: MediaGenerateRequest = kwargs["request"]
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": kwargs["model"],
        "prompt": kwargs["prompt"],
        "n": 1,
        "size": request.size,
        "quality": request.quality,
    }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(f"{base_url}{_path(endpoint)}", headers=headers, json=payload)
    _raise_provider_error(response)
    data = response.json()
    item = _first_media_item(data)
    asset = _base_asset(kwargs["asset_id"], "image", kwargs["prompt"])
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"], validate=True)
        if len(raw) > 30 * 1024 * 1024:
            raise MediaServiceError("Generated image exceeds the 30 MB local limit.")
        path = _write_media_file(kwargs["generation_id"], kwargs["asset_id"], raw, ".png")
        asset.update({"status": "completed", "local_path": str(path), "mime_type": "image/png", "url": _asset_url(kwargs["generation_id"], kwargs["asset_id"])})
    elif item.get("url"):
        asset.update({"status": "completed", "url": str(item["url"]), "mime_type": "image/png"})
    else:
        raise MediaServiceError("Image provider returned neither b64_json nor url.")
    return asset


async def _create_openai_video(**kwargs: Any) -> dict[str, Any]:
    endpoint = kwargs["endpoint_path"] or "/videos"
    headers = {"Authorization": f"Bearer {kwargs['key']}"} if kwargs["key"] else {}
    request: MediaGenerateRequest = kwargs["request"]
    files = {
        "model": (None, kwargs["model"]),
        "prompt": (None, kwargs["prompt"]),
        "seconds": (None, str(request.seconds)),
        "size": (None, request.size),
    }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(f"{kwargs['base_url']}{_path(endpoint)}", headers=headers, files=files)
    _raise_provider_error(response)
    data = response.json()
    job_id = str(data.get("id", ""))
    if not job_id:
        raise MediaServiceError("Video provider returned no job id.")
    status = _normalize_asset_status(str(data.get("status", "queued")))
    return {**_base_asset(kwargs["asset_id"], "video", kwargs["prompt"]), "status": status, "job_id": job_id, "mime_type": "video/mp4"}


async def _generate_generic(**kwargs: Any) -> dict[str, Any]:
    endpoint = kwargs["endpoint_path"] or "/generate"
    headers = {"Content-Type": "application/json"}
    if kwargs["key"]:
        headers["Authorization"] = f"Bearer {kwargs['key']}"
    request: MediaGenerateRequest = kwargs["request"]
    payload = {
        "media_type": kwargs["media_type"],
        "model": kwargs["model"],
        "prompt": kwargs["prompt"],
        "options": {"size": request.size, "quality": request.quality, "seconds": request.seconds},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(f"{kwargs['base_url']}{_path(endpoint)}", headers=headers, json=payload)
    _raise_provider_error(response)
    data = response.json()
    return _generic_asset(data, kwargs["asset_id"], kwargs["media_type"], kwargs["prompt"])


async def _refresh_openai_video(*, asset: dict[str, Any], generation_id: str, base_url: str, key: str) -> None:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url}/videos/{asset['job_id']}", headers=headers)
    _raise_provider_error(response)
    data = response.json()
    asset["status"] = _normalize_asset_status(str(data.get("status", "in_progress")))
    if asset["status"] == "completed":
        asset["url"] = _asset_url(generation_id, str(asset["asset_id"]))
    elif asset["status"] == "failed":
        asset["error"] = str(data.get("error", "Video generation failed."))


async def _refresh_generic(*, asset: dict[str, Any], base_url: str, status_path: str, key: str) -> None:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    path = status_path.replace("{job_id}", str(asset["job_id"]))
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url}{_path(path)}", headers=headers)
    _raise_provider_error(response)
    updated = _generic_asset(response.json(), str(asset["asset_id"]), asset["media_type"], str(asset["prompt"]))
    asset.update(updated)


def _generic_asset(data: dict[str, Any], asset_id: str, media_type: MediaType, prompt: str) -> dict[str, Any]:
    envelope = data
    for key in ("result", "output"):
        nested = envelope.get(key)
        if isinstance(nested, dict):
            envelope = nested
            break
    item = _first_media_item(envelope)
    return {
        **_base_asset(asset_id, media_type, prompt),
        "status": _normalize_asset_status(str(data.get("status", envelope.get("status", item.get("status", "completed"))))),
        "job_id": str(data.get("job_id") or data.get("id") or envelope.get("job_id") or envelope.get("id") or item.get("job_id") or ""),
        "url": str(item.get("url") or item.get("image_url") or item.get("video_url") or ""),
        "data_url": _as_data_url(item.get("data_url") or item.get("b64_json"), media_type),
        "mime_type": str(item.get("mime_type", "image/png" if media_type == "image" else "video/mp4")),
        "error": str(data.get("error") or envelope.get("error") or ""),
    }


def _first_media_item(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("data", "assets", "images", "videos", "output"):
        candidates = data.get(key)
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                return first
            if isinstance(first, str):
                return {"url": first}
    return data


def _as_data_url(value: Any, media_type: MediaType) -> str:
    raw = str(value or "")
    if not raw or raw.startswith("data:"):
        return raw
    mime = "image/png" if media_type == "image" else "video/mp4"
    return f"data:{mime};base64,{raw}"


def _base_asset(asset_id: str, media_type: MediaType, prompt: str) -> dict[str, Any]:
    return {"asset_id": asset_id, "media_type": media_type, "status": "queued", "prompt": prompt, "url": "", "data_url": "", "job_id": "", "mime_type": "", "error": ""}


def _record_response(record: MediaGenerationRecord) -> MediaGenerationResponse:
    output = _json_object(record.output_json)
    return MediaGenerationResponse(
        generation_id=record.id,
        run_id=record.run_id,
        media_type=record.media_type,  # type: ignore[arg-type]
        provider=record.provider,  # type: ignore[arg-type]
        model=record.model,
        status=record.status,  # type: ignore[arg-type]
        assets=[MediaAsset.model_validate(item) for item in output.get("assets", []) if isinstance(item, dict)],
    )


def _generation_status(assets: list[dict[str, Any]]) -> str:
    statuses = {str(asset.get("status", "failed")) for asset in assets}
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"failed"}:
        return "failed"
    if "completed" in statuses and ("failed" in statuses or "queued" in statuses or "in_progress" in statuses):
        return "partial"
    if "in_progress" in statuses:
        return "in_progress"
    return "queued"


def _normalize_asset_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"completed", "succeeded", "success", "done"}:
        return "completed"
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return "failed"
    if normalized in {"running", "processing", "in_progress"}:
        return "in_progress"
    return "queued"


def _write_media_file(generation_id: str, asset_id: str, content: bytes, suffix: str) -> Path:
    root = Path(settings.media_output_dir)
    directory = root / generation_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{asset_id}{suffix}"
    path.write_bytes(content)
    return path.resolve()


def _asset_url(generation_id: str, asset_id: str) -> str:
    return f"/api/media/generations/{generation_id}/assets/{asset_id}/content"


def _path(value: str) -> str:
    return value if value.startswith("/") else f"/{value}"


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _extract_json(raw: str) -> dict[str, Any]:
    candidate = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001
        match = re.search(r"\{[\s\S]*\}", candidate)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001
            return {}


def _raise_provider_error(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text[:1000]
        raise MediaServiceError(f"Media provider returned HTTP {response.status_code}: {detail}") from exc
