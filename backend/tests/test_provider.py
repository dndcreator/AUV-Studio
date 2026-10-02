from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.execution import provider as provider_module
from app.execution.provider import OpenAICompatibleProvider, provider_accepts_keyless, provider_defaults
from app.models import ModelConfigRecord
from app.schemas import ModelConfigRequest
from app.service import save_model_config


class _Response:
    def __init__(self, data: dict) -> None:
        self._data = data
        self.status_code = 200
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class _Client:
    calls: list[dict] = []
    response: dict = {}

    def __init__(self, *, timeout: int) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, *, headers: dict, json: dict) -> _Response:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _Response(self.response)


def test_ollama_native_chat_is_keyless_and_uses_api_chat(monkeypatch) -> None:
    _Client.calls = []
    _Client.response = {"message": {"role": "assistant", "content": "local answer"}}
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _Client)
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434",
        api_key="",
        timeout_seconds=10,
        retry_count=0,
        default_model="llama3.2",
        provider_kind="ollama",
    )

    result = asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="hello"))

    assert result["content"] == "local answer"
    assert _Client.calls[0]["url"] == "http://localhost:11434/api/chat"
    assert _Client.calls[0]["json"]["stream"] is False
    assert _Client.calls[0]["json"]["model"] == "llama3.2"
    assert "Authorization" not in _Client.calls[0]["headers"]


def test_eval_output_limit_is_forwarded_to_provider(monkeypatch) -> None:
    _Client.calls = []
    _Client.response = {"choices": [{"message": {"content": "bounded"}}]}
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _Client)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:1234/v1",
        api_key="",
        timeout_seconds=10,
        retry_count=0,
        default_model="local-model",
        max_output_tokens=240,
    )
    asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="hello"))
    assert _Client.calls[0]["json"]["max_tokens"] == 240


def test_deepseek_eval_can_disable_thinking(monkeypatch) -> None:
    _Client.calls = []
    _Client.response = {"choices": [{"message": {"content": "structured answer"}, "finish_reason": "stop"}]}
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _Client)
    provider = OpenAICompatibleProvider(
        base_url="https://api.deepseek.com",
        api_key="key",
        timeout_seconds=10,
        retry_count=0,
        default_model="deepseek-flash",
        max_output_tokens=350,
        thinking_mode="disabled",
    )
    asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="hello"))
    payload = _Client.calls[0]["json"]
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["reasoning_effort"] == "none"


def test_local_openai_compatible_endpoint_does_not_require_key(monkeypatch) -> None:
    _Client.calls = []
    _Client.response = {"choices": [{"message": {"content": "lm studio answer"}}]}
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _Client)
    provider = OpenAICompatibleProvider(
        base_url="http://127.0.0.1:1234/v1",
        api_key="",
        timeout_seconds=10,
        retry_count=0,
        default_model="local-model",
    )

    result = asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="hello"))

    assert result["content"] == "lm studio answer"
    assert _Client.calls[0]["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert provider_accepts_keyless("openai_compatible", "http://[::1]:8000/v1") is True
    assert provider_accepts_keyless("openai_compatible", "https://api.example.com/v1") is False


def test_llama_cpp_preset_uses_openai_protocol_without_key(monkeypatch) -> None:
    _Client.calls = []
    _Client.response = {"choices": [{"message": {"content": "llama.cpp answer"}}]}
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _Client)
    base_url, model = provider_defaults("llama_cpp")
    provider = OpenAICompatibleProvider(
        base_url=base_url,
        api_key="",
        timeout_seconds=10,
        retry_count=0,
        default_model=model,
        provider_kind="llama_cpp",
    )

    result = asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="hello"))

    assert result["content"] == "llama.cpp answer"
    assert provider.provider_kind == "llama_cpp"
    assert _Client.calls[0]["url"] == "http://localhost:8080/v1/chat/completions"
    assert provider_accepts_keyless("llama_cpp", "http://192.168.1.20:8080/v1") is True
    assert provider_defaults("lm_studio")[0] == "http://localhost:1234/v1"
    assert provider_defaults("vllm")[0] == "http://localhost:8000/v1"


def test_remote_openai_compatible_without_key_retains_mock_fallback(monkeypatch) -> None:
    def _unexpected_client(**_kwargs: object) -> None:
        raise AssertionError("remote keyless mock must not perform an HTTP request")

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _unexpected_client)
    provider = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="",
        timeout_seconds=10,
        retry_count=0,
        default_model="remote-model",
    )

    result = asyncio.run(provider.chat(model="", system_prompt="system", user_prompt="hello"))

    assert result["raw"] == {"mock": True}


def test_switching_from_cloud_to_local_does_not_forward_saved_cloud_key() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    provider = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="",
        timeout_seconds=10,
        retry_count=0,
    )
    save_model_config(
        db,
        provider,
        ModelConfigRequest(
            provider="openai_compatible",
            base_url="https://api.example.com/v1",
            default_model="cloud-model",
            api_key="cloud-secret",
        ),
    )

    saved = save_model_config(
        db,
        provider,
        ModelConfigRequest(provider="ollama", base_url="", default_model="", api_key=None),
    )
    record = db.query(ModelConfigRecord).filter(ModelConfigRecord.id == "default").one()

    assert saved.base_url == "http://localhost:11434"
    assert saved.default_model == "llama3.2"
    assert saved.requires_api_key is False
    assert record.api_key == ""
    assert provider.api_key == ""
    assert provider.provider_kind == "ollama"
