from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import settings

LOCAL_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "ollama": ("http://localhost:11434", "llama3.2"),
    "llama_cpp": ("http://localhost:8080/v1", "local-model"),
    "lm_studio": ("http://localhost:1234/v1", "local-model"),
    "vllm": ("http://localhost:8000/v1", "local-model"),
    "localai": ("http://localhost:8080/v1", "local-model"),
    "tgi": ("http://localhost:8080/v1", "tgi"),
    "text_generation_webui": ("http://localhost:5000/v1", "local-model"),
}


class ProviderError(RuntimeError):
    pass


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int,
        retry_count: int,
        default_model: str = "",
        provider_kind: str = "openai_compatible",
        max_output_tokens: int | None = None,
        thinking_mode: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self.default_model = default_model
        self.provider_kind = _normalize_configured_provider(provider_kind)
        self.max_output_tokens = max_output_tokens
        self.thinking_mode = thinking_mode if thinking_mode in {"enabled", "disabled"} else None

    def configure(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        provider_kind: str | None = None,
    ) -> None:
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        if api_key is not None:
            self.api_key = api_key
        if default_model is not None:
            self.default_model = default_model
        if provider_kind is not None:
            self.provider_kind = _normalize_configured_provider(provider_kind)

    async def chat(self, model: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        effective_model = model.strip() or self.default_model or settings.openai_model
        if not self.api_key and not provider_accepts_keyless(self.provider_kind, self.base_url):
            return {
                "content": f"[mock-response] model={effective_model} system={system_prompt[:30]} user={user_prompt[:80]}",
                "raw": {"mock": True},
            }

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
        protocol_kind = _normalize_provider_kind(self.provider_kind)
        if protocol_kind == "ollama":
            url = f"{self.base_url}/api/chat"
            payload = {"model": effective_model, "messages": messages, "stream": False}
            if self.max_output_tokens is not None:
                payload["options"] = {"num_predict": self.max_output_tokens}
        else:
            url = f"{self.base_url}/chat/completions"
            payload = {"model": effective_model, "messages": messages}
            if self.max_output_tokens is not None:
                payload["max_tokens"] = self.max_output_tokens
            if self.thinking_mode is not None:
                payload["thinking"] = {"type": self.thinking_mode}
                payload["reasoning_effort"] = "none" if self.thinking_mode == "disabled" else "low"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    detail = _response_error_detail(resp)
                    raise ProviderError(
                        f"Provider request failed: HTTP {resp.status_code} for {url}; model={effective_model}; detail={detail}"
                    ) from exc
                data = resp.json()
                if protocol_kind == "ollama":
                    content = data.get("message", {}).get("content") or data.get("response", "")
                else:
                    choice = data["choices"][0]
                    message = choice.get("message", {}) if isinstance(choice, dict) else {}
                    content = message.get("content", "") if isinstance(message, dict) else ""
                if not isinstance(content, str) or not content.strip():
                    finish_reason = choice.get("finish_reason", "") if protocol_kind != "ollama" and isinstance(choice, dict) else ""
                    reasoning_only = bool(message.get("reasoning_content")) if protocol_kind != "ollama" and isinstance(message, dict) else False
                    raise ProviderError(
                        "Provider returned no text content; "
                        f"provider={self.provider_kind}; model={effective_model}; "
                        f"finish_reason={finish_reason or 'unknown'}; reasoning_only={reasoning_only}"
                    )
                return {"content": content, "raw": data}
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.retry_count:
                    await asyncio.sleep(2**attempt)
                else:
                    break
        raise ProviderError(f"Provider request failed: {last_error}") from last_error


def _response_error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        return str(data)[:1000]
    except Exception:  # noqa: BLE001
        return resp.text[:1000]


def _normalize_provider_kind(value: str) -> str:
    return "ollama" if str(value).strip().lower() == "ollama" else "openai_compatible"


def _normalize_configured_provider(value: str) -> str:
    kind = str(value).strip().lower()
    return kind if kind in {"openai_compatible", *LOCAL_PROVIDER_DEFAULTS.keys()} else "openai_compatible"


def _is_local_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").strip().lower()
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_private or address.is_link_local
    except ValueError:
        return False


def provider_accepts_keyless(provider_kind: str, base_url: str) -> bool:
    kind = str(provider_kind).strip().lower()
    return kind in LOCAL_PROVIDER_DEFAULTS or _is_local_url(base_url)


def provider_defaults(provider_kind: str) -> tuple[str, str]:
    return LOCAL_PROVIDER_DEFAULTS.get(str(provider_kind).strip().lower(), (settings.openai_base_url, settings.openai_model))


def build_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout_seconds=settings.request_timeout_seconds,
        retry_count=settings.provider_retry_count,
        default_model=settings.openai_model,
        provider_kind="openai_compatible",
    )


def build_provider_with_override(
    base_url: str | None,
    api_key: str | None,
    provider_kind: str = "openai_compatible",
) -> OpenAICompatibleProvider:
    default_base_url, default_model = provider_defaults(provider_kind)
    return OpenAICompatibleProvider(
        base_url=base_url or default_base_url,
        api_key=api_key if api_key is not None else settings.openai_api_key,
        timeout_seconds=settings.request_timeout_seconds,
        retry_count=settings.provider_retry_count,
        default_model=default_model,
        provider_kind=provider_kind,
    )
