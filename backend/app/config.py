import os
import re
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.revision_images import MAX_BUILD_LOG_BYTES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DSPY_TRAINER_", extra="ignore")

    environment: str = Field(default="development")
    backend_host: str = Field(default="0.0.0.0")
    backend_port: int = Field(default=8000)

    redis_url: str = Field(default="redis://localhost:6379/0")
    queue_name: str = Field(default="dspy-trainer:jobs")
    worker_registry_prefix: str = Field(default="dspy-trainer:workers")
    total_workers: int = Field(default=8)
    bundle_install_max_concurrency: int = Field(default=8)
    endpoint_worker_registry_prefix: str = Field(default="dspy-trainer:endpoint-workers")
    endpoint_worker_heartbeat_ttl_seconds: int = Field(default=300)
    endpoint_queue_prefix: str = Field(default="dspy-trainer:endpoint-queues")
    endpoint_invocation_channel_prefix: str = Field(default="dspy-trainer:endpoint-invocations")

    postgres_dsn: str = Field(default="")
    checkout_root: str = Field(default="/tmp/dspy-trainer/checkouts")
    github_pat: str = Field(default="", alias="GITHUB_PAT")
    git_commit_name: str = Field(default="DSPy Trainer", alias="GIT_COMMIT_NAME")
    git_commit_email: str = Field(default="dspy-trainer@local", alias="GIT_COMMIT_EMAIL")
    module_env_encryption_key: str = Field(default="")

    mlflow_tracking_uri: str = Field(default="http://localhost:5001")
    cors_allow_origins: str = Field(default="http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173,http://127.0.0.1:5173")

    @field_validator("postgres_dsn")
    @classmethod
    def validate_postgres_dsn(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DSPY_TRAINER_POSTGRES_DSN is required")
        return value

    @field_validator("bundle_install_max_concurrency")
    @classmethod
    def validate_bundle_install_max_concurrency(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("DSPY_TRAINER_BUNDLE_INSTALL_MAX_CONCURRENCY must be at least 1")
        return int(value)


    @field_validator("endpoint_worker_heartbeat_ttl_seconds")
    @classmethod
    def validate_endpoint_worker_heartbeat_ttl_seconds(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("DSPY_TRAINER_ENDPOINT_WORKER_HEARTBEAT_TTL_SECONDS must be at least 1")
        return int(value)

    def cors_origins_list(self) -> list[str]:
        return get_cors_origins_from_values(
            cors_allow_origins=self.cors_allow_origins,
            vite_api_base_url=os.getenv("VITE_API_BASE_URL", "/api"),
            vite_mlflow_base_url=os.getenv("VITE_MLFLOW_BASE_URL", "/mlflow"),
        )


class DeployerSettings(Settings):
    deployer_leader_timeout_seconds: float = Field(default=15.0)
    deployer_claim_timeout_seconds: float = Field(default=300.0)
    deployer_poll_interval_seconds: float = Field(default=1.0)
    deployer_build_log_max_bytes: int = Field(default=MAX_BUILD_LOG_BYTES)
    deployer_backend_base_image_id: str = Field(default="")
    deployer_image_repository: str = Field(default="dspy-trainer-revision")
    deployer_platform_version: str = Field(default="local")
    deployment_id: str = Field(default="")
    compose_project_name: str = Field(default="")
    compose_network_name: str = Field(default="")

    @field_validator(
        "deployer_leader_timeout_seconds",
        "deployer_claim_timeout_seconds",
        "deployer_poll_interval_seconds",
    )
    @classmethod
    def validate_positive_duration(cls, value: float) -> float:
        if float(value) <= 0:
            raise ValueError("deployer coordinator durations must be positive")
        return float(value)

    @field_validator("deployer_build_log_max_bytes")
    @classmethod
    def validate_build_log_limit(cls, value: int) -> int:
        if not 1 <= int(value) <= MAX_BUILD_LOG_BYTES:
            raise ValueError(
                f"DSPY_TRAINER_DEPLOYER_BUILD_LOG_MAX_BYTES must be between 1 and {MAX_BUILD_LOG_BYTES}"
            )
        return int(value)

    @field_validator("deployer_backend_base_image_id")
    @classmethod
    def validate_base_image_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
            raise ValueError(
                "DSPY_TRAINER_DEPLOYER_BACKEND_BASE_IMAGE_ID must be an immutable sha256 image ID"
            )
        return normalized

    @field_validator(
        "deployer_image_repository",
        "deployer_platform_version",
        "deployment_id",
        "compose_project_name",
        "compose_network_name",
    )
    @classmethod
    def validate_deployer_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("deployer identity values must not be empty")
        return normalized
def _normalize_origin(candidate: str) -> str:
    value = str(candidate or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return ""


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
    return origins


def get_cors_origins_from_env() -> list[str]:
    return get_cors_origins_from_values(
        cors_allow_origins=os.getenv(
            "DSPY_TRAINER_CORS_ALLOW_ORIGINS",
            "http://localhost:8080,http://127.0.0.1:8080,http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
        ),
        vite_api_base_url=os.getenv("VITE_API_BASE_URL", "/api"),
        vite_mlflow_base_url=os.getenv("VITE_MLFLOW_BASE_URL", "/mlflow"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
@lru_cache
def get_deployer_settings() -> DeployerSettings:
    return DeployerSettings()
