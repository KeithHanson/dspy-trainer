from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DSPY_TRAINER_", extra="ignore")

    environment: str = Field(default="development")
    backend_host: str = Field(default="0.0.0.0")
    backend_port: int = Field(default=8000)

    redis_url: str = Field(default="redis://localhost:6379/0")
    queue_name: str = Field(default="dspy-trainer:jobs")

    postgres_dsn: str = Field(default="")

    mlflow_tracking_uri: str = Field(default="http://localhost:5000")
    litellm_base_url: str = Field(default="http://localhost:4000")
    litellm_api_key: str = Field(default="")

    @field_validator("postgres_dsn")
    @classmethod
    def validate_postgres_dsn(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DSPY_TRAINER_POSTGRES_DSN is required")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
