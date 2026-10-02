from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "AUV Backend"
    database_url: str = os.getenv("AUV_DATABASE_URL", "sqlite:///./data/auv.db")
    openai_base_url: str = os.getenv("AUV_OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_api_key: str = os.getenv("AUV_OPENAI_API_KEY", "")
    openai_model: str = os.getenv("AUV_OPENAI_MODEL", "gpt-4o-mini")
    image_api_key: str = os.getenv("AUV_IMAGE_API_KEY", "")
    video_api_key: str = os.getenv("AUV_VIDEO_API_KEY", "")
    media_output_dir: str = os.getenv("AUV_MEDIA_OUTPUT_DIR", "./data/media")
    request_timeout_seconds: int = int(os.getenv("AUV_REQUEST_TIMEOUT_SECONDS", "60"))
    provider_retry_count: int = int(os.getenv("AUV_PROVIDER_RETRY_COUNT", "2"))
    perf_latency_warn_ms: int = int(os.getenv("AUV_PERF_LATENCY_WARN_MS", "1200"))
    perf_error_rate_warn: float = float(os.getenv("AUV_PERF_ERROR_RATE_WARN", "0.05"))
    perf_slow_request_warn_count: int = int(os.getenv("AUV_PERF_SLOW_REQUEST_WARN_COUNT", "20"))
    perf_node_slow_ms: int = int(os.getenv("AUV_PERF_NODE_SLOW_MS", "10000"))
    run_worker_count: int = int(os.getenv("AUV_RUN_WORKER_COUNT", "4"))
    run_queue_max_size: int = int(os.getenv("AUV_RUN_QUEUE_MAX_SIZE", "200"))


settings = Settings()
