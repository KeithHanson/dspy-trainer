import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


PROFILES: dict[str, dict] = {}
NEXT_PROFILE_ID = 1


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_lm_profile(self, name, model, api_base, model_type, default_params, lm_class_path):
    global NEXT_PROFILE_ID
    profile_id = f"lm-{NEXT_PROFILE_ID}"
    NEXT_PROFILE_ID += 1
    profile = {
        "id": profile_id,
        "name": name,
        "model": model,
        "api_base": api_base,
        "model_type": model_type,
        "default_params": default_params,
        "lm_class_path": lm_class_path,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    PROFILES[profile_id] = profile
    return profile


async def fake_list_lm_profiles(self):
    return list(PROFILES.values())[::-1]


async def fake_get_lm_profile(self, lm_profile_id):
    return PROFILES.get(lm_profile_id)


async def fake_update_lm_profile(self, lm_profile_id, name, model, api_base, model_type, default_params, lm_class_path):
    current = PROFILES.get(lm_profile_id)
    if current is None:
        return None
    if name is not None:
        current["name"] = name
    if model is not None:
        current["model"] = model
    if api_base is not None:
        current["api_base"] = api_base
    if model_type is not None:
        current["model_type"] = model_type
    if default_params is not None:
        current["default_params"] = default_params
    current["lm_class_path"] = lm_class_path
    return current


async def fake_delete_lm_profile(self, lm_profile_id):
    if lm_profile_id not in PROFILES:
        return False
    del PROFILES[lm_profile_id]
    return True


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "create_lm_profile", fake_create_lm_profile)
    monkeypatch.setattr(main_mod.AppServices, "list_lm_profiles", fake_list_lm_profiles)
    monkeypatch.setattr(main_mod.AppServices, "get_lm_profile", fake_get_lm_profile)
    monkeypatch.setattr(main_mod.AppServices, "update_lm_profile", fake_update_lm_profile)
    monkeypatch.setattr(main_mod.AppServices, "delete_lm_profile", fake_delete_lm_profile)


def _reset_state():
    global NEXT_PROFILE_ID
    PROFILES.clear()
    NEXT_PROFILE_ID = 1


def test_lm_profile_crud(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/lm-profiles",
            json={
                "name": "Codex Responses",
                "model": "openai/codex-5.3",
                "api_base": "http://litellm-proxy:4000",
                "model_type": "responses",
                "default_params": {"temperature": 0.0},
                "lm_class_path": "dspy.LM",
            },
        )
        assert created.status_code == 200
        profile_id = created.json()["id"]

        listed = client.get("/lm-profiles")
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        fetched = client.get(f"/lm-profiles/{profile_id}")
        assert fetched.status_code == 200
        assert fetched.json()["model"] == "openai/codex-5.3"

        updated = client.patch(
            f"/lm-profiles/{profile_id}",
            json={"name": "Codex Stable", "default_params": {"temperature": 0.1}},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Codex Stable"
        assert updated.json()["default_params"]["temperature"] == 0.1

        deleted = client.delete(f"/lm-profiles/{profile_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = client.get(f"/lm-profiles/{profile_id}")
        assert missing.status_code == 404
