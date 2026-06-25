from functools import lru_cache
import os
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DSPY_TRAINER_", extra="ignore")

    environment: str = Field(default="development")
    backend_host: str = Field(default="0.0.0.0")
    backend_port: int = Field(default=8000)

    redis_url: str = Field(default="redis://localhost:6379/0")
    queue_name: str = Field(default="dspy-trainer:jobs")
    worker_registry_prefix: str = Field(default="dspy-trainer:workers")
    total_workers: int = Field(default=8)
    endpoint_worker_registry_prefix: str = Field(default="dspy-trainer:endpoint-workers")
    total_endpoint_workers: int = Field(default=2)
    endpoint_queue_prefix: str = Field(default="dspy-trainer:endpoint-queues")
    endpoint_worker_assignment_prefix: str = Field(default="dspy-trainer:endpoint-worker-assignments")
    endpoint_invocation_channel_prefix: str = Field(default="dspy-trainer:endpoint-invocations")

    postgres_dsn: str = Field(default="")
    checkout_root: str = Field(default="/tmp/dspy-trainer/checkouts")
    github_pat: str = Field(default="", alias="GITHUB_PAT")
    git_commit_name: str = Field(default="DSPy Trainer", alias="GIT_COMMIT_NAME")
    git_commit_email: str = Field(default="dspy-trainer@local", alias="GIT_COMMIT_EMAIL")
    module_env_encryption_key: str = Field(default="")

    mlflow_tracking_uri: str = Field(default="http://localhost:5001")
    litellm_base_url: str = Field(default="http://localhost:4000")
    litellm_api_key: str = Field(default="")
    cors_allow_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173")

    @field_validator("postgres_dsn")
    @classmethod
    def validate_postgres_dsn(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DSPY_TRAINER_POSTGRES_DSN is required")
        return value

    def cors_origins_list(self) -> list[str]:
        return get_cors_origins_from_values(
            cors_allow_origins=self.cors_allow_origins,
            vite_api_base_url="http://localhost:8000",
            vite_mlflow_base_url="http://localhost:5001",
            vite_litellm_base_url="http://localhost:4000",
        )


def _normalize_origin(candidate: str) -> str:
    value = str(candidate or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return value


def _base_host_origin(candidate: str) -> str:
    value = str(candidate or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme}://{parsed.hostname}"
    return ""


def get_cors_origins_from_values(
    cors_allow_origins: str,
    vite_api_base_url: str,
    vite_mlflow_base_url: str,
    vite_litellm_base_url: str,
) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()

    def add_origin(candidate: str) -> None:
        origin = _normalize_origin(candidate)
        if not origin:
            return
        if origin not in seen:
            seen.add(origin)
            origins.append(origin)

    def add_origin_with_base_host(candidate: str) -> None:
        add_origin(candidate)
        base_origin = _base_host_origin(candidate)
        if base_origin:
            add_origin(base_origin)

    for origin in cors_allow_origins.split(","):
        add_origin(origin)
    add_origin_with_base_host(vite_api_base_url)
    add_origin_with_base_host(vite_mlflow_base_url)
    add_origin_with_base_host(vite_litellm_base_url)
    return origins


def get_cors_origins_from_env() -> list[str]:
    return get_cors_origins_from_values(
        cors_allow_origins=os.getenv(
            "DSPY_TRAINER_CORS_ALLOW_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
        ),
        vite_api_base_url=os.getenv("VITE_API_BASE_URL", "http://localhost:8000"),
        vite_mlflow_base_url=os.getenv("VITE_MLFLOW_BASE_URL", "http://localhost:5001"),
        vite_litellm_base_url=os.getenv("VITE_LITELLM_BASE_URL", "http://localhost:4000"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
