import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


def _build_services() -> AppServices:
    return AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))


def test_provision_litellm_model_sets_base_model_for_azure(monkeypatch):
    services = _build_services()
    captured = {}

    async def fake_litellm_request(method, path, payload=None, query=None):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(services, "_litellm_request", fake_litellm_request)

    asyncio.run(
        services._provision_litellm_model(
            profile_ref="profile-1",
            profile_name="Azure Eval",
            model="azure/codex-5.3-eval-deployment-1",
            api_base="https://example.cognitiveservices.azure.com",
            model_type="responses",
            upstream_api_key="sk-upstream",
        )
    )

    assert captured["payload"]["litellm_params"]["base_model"] == "codex-5.3"


def test_sync_litellm_model_update_sets_base_model_for_azure(monkeypatch):
    services = _build_services()
    captured = {}

    async def fake_litellm_request(method, path, payload=None, query=None):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(services, "_litellm_request", fake_litellm_request)

    asyncio.run(
        services._sync_litellm_model_update(
            profile_ref="profile-1",
            profile_name="Azure Eval",
            model="azure/codex-5.3-eval-deployment-1",
            api_base="https://example.cognitiveservices.azure.com",
            model_type="responses",
            upstream_api_key="sk-upstream",
            include_litellm_params=True,
        )
    )

    assert captured["payload"]["litellm_params"]["base_model"] == "codex-5.3"
