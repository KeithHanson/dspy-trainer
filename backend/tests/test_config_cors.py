import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_cors_origins_from_env


def test_cors_origins_include_explicit_and_vite_public_origins_without_duplicates(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_CORS_ALLOW_ORIGINS", "https://agents.abatix.com, http://localhost:3000 ")
    monkeypatch.setenv("VITE_API_BASE_URL", "https://agents.abatix.com:8000/modules")
    monkeypatch.setenv("VITE_MLFLOW_BASE_URL", "https://agents.abatix.com:5001/#/experiments/1")
    monkeypatch.setenv("VITE_LITELLM_BASE_URL", "https://agents.abatix.com:4000")

    assert get_cors_origins_from_env() == [
        "https://agents.abatix.com",
        "http://localhost:3000",
        "https://agents.abatix.com:8000",
        "https://agents.abatix.com:5001",
        "https://agents.abatix.com:4000",
    ]
