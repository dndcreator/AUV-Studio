from __future__ import annotations

from time import perf_counter

from sqlalchemy.orm import Session

from .config import settings
from .execution.provider import OpenAICompatibleProvider, build_provider_with_override, provider_accepts_keyless, provider_defaults
from .models import ModelConfigRecord
from .schemas import ModelConfigRequest, ModelConfigResponse, ModelConfigTestRequest, ModelConfigTestResponse
def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def get_model_config(db: Session) -> ModelConfigResponse:
    rec = db.query(ModelConfigRecord).filter(ModelConfigRecord.id == "default").first()
    if rec is not None:
        return ModelConfigResponse(
            provider=rec.provider,
            base_url=rec.base_url,
            default_model=rec.default_model,
            has_api_key=bool(rec.api_key),
            masked_api_key=_mask_secret(rec.api_key),
            requires_api_key=not provider_accepts_keyless(rec.provider, rec.base_url),
            source="database",
        )
    source = "environment" if settings.openai_api_key else "default"
    return ModelConfigResponse(
        provider="openai_compatible",
        base_url=settings.openai_base_url,
        default_model=settings.openai_model,
        has_api_key=bool(settings.openai_api_key),
        masked_api_key=_mask_secret(settings.openai_api_key),
        requires_api_key=not provider_accepts_keyless("openai_compatible", settings.openai_base_url),
        source=source,
    )


def save_model_config(db: Session, provider: OpenAICompatibleProvider, req: ModelConfigRequest) -> ModelConfigResponse:
    rec = db.query(ModelConfigRecord).filter(ModelConfigRecord.id == "default").first()
    api_key = req.api_key
    fallback_base_url, fallback_model = provider_defaults(req.provider)
    next_base_url = req.base_url.strip().rstrip("/") or fallback_base_url
    next_model = req.default_model.strip() or fallback_model
    if rec is None:
        initial_key = api_key.strip() if api_key is not None else ""
        if not initial_key and req.provider == "openai_compatible" and next_base_url == settings.openai_base_url.rstrip("/"):
            initial_key = settings.openai_api_key
        rec = ModelConfigRecord(
            id="default",
            provider=req.provider,
            base_url=next_base_url,
            default_model=next_model,
            api_key=initial_key,
        )
    else:
        endpoint_changed = rec.provider != req.provider or rec.base_url.rstrip("/") != next_base_url
        rec.provider = req.provider
        rec.base_url = next_base_url
        rec.default_model = next_model
        if api_key is not None:
            rec.api_key = api_key.strip()
        elif endpoint_changed:
            rec.api_key = ""
    db.add(rec)
    db.commit()
    provider.configure(base_url=rec.base_url, api_key=rec.api_key, default_model=rec.default_model, provider_kind=rec.provider)
    return get_model_config(db)


def apply_stored_model_config(db: Session, provider: OpenAICompatibleProvider) -> None:
    rec = db.query(ModelConfigRecord).filter(ModelConfigRecord.id == "default").first()
    if rec is not None:
        provider.configure(base_url=rec.base_url, api_key=rec.api_key, default_model=rec.default_model, provider_kind=rec.provider)


async def test_model_config(db: Session, req: ModelConfigTestRequest) -> ModelConfigTestResponse:
    rec = db.query(ModelConfigRecord).filter(ModelConfigRecord.id == "default").first()
    fallback_base_url, fallback_model = provider_defaults(req.provider)
    base_url = req.base_url.strip().rstrip("/") or fallback_base_url
    api_key = (req.api_key or "").strip()
    if not api_key and rec is not None and rec.provider == req.provider and rec.base_url.rstrip("/") == base_url:
        api_key = rec.api_key
    if not api_key and req.provider == "openai_compatible" and base_url == settings.openai_base_url.rstrip("/"):
        api_key = settings.openai_api_key
    if not api_key and not provider_accepts_keyless(req.provider, base_url):
        return ModelConfigTestResponse(ok=False, message="API key is missing. Add a key or configure AUV_OPENAI_API_KEY.")

    probe = build_provider_with_override(base_url=base_url, api_key=api_key, provider_kind=req.provider)
    start = perf_counter()
    try:
        await probe.chat(
            model=req.default_model.strip() or fallback_model,
            system_prompt="You are a connectivity probe. Reply with ok.",
            user_prompt="Reply with exactly: ok",
        )
    except Exception as exc:  # noqa: BLE001
        return ModelConfigTestResponse(ok=False, message=f"Model test failed: {exc}", latency_ms=int((perf_counter() - start) * 1000))
    return ModelConfigTestResponse(ok=True, message="Model connection works.", latency_ms=int((perf_counter() - start) * 1000))
