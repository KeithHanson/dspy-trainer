import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


PROFILES: dict[str, dict] = {}
NEXT_PROFILE_ID = 1
FORWARDED_KEYS: list[str | None] = []


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_lm_profile(self, name, model, api_base, model_type, default_params, lm_class_path, api_key):
    global NEXT_PROFILE_ID
    FORWARDED_KEYS.append(api_key)
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
        "has_api_key": bool(api_key),
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    PROFILES[profile_id] = profile
    return profile


async def fake_list_lm_profiles(self):
    return list(PROFILES.values())[::-1]


async def fake_get_lm_profile(self, lm_profile_id):
    return PROFILES.get(lm_profile_id)


async def fake_update_lm_profile(self, lm_profile_id, name, model, api_base, model_type, default_params, lm_class_path, api_key):
    FORWARDED_KEYS.append(api_key)
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
    if api_key is not None:
        current["has_api_key"] = bool(api_key)
    return current


async def fake_test_lm_profile_connection(self, lm_profile_id):
    current = PROFILES.get(lm_profile_id)
    if current is None:
        return None
    return {"ok": True, "model": current["model"], "reply": "connection-ok", "raw": ["connection-ok"]}


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
    monkeypatch.setattr(main_mod.AppServices, "test_lm_profile_connection", fake_test_lm_profile_connection)


def _reset_state():
    global NEXT_PROFILE_ID
    PROFILES.clear()
    FORWARDED_KEYS.clear()
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
                "api_base": "https://api.openai.com",
                "model_type": "responses",
                "default_params": {"temperature": 0.0},
                "lm_class_path": "dspy.LM",
                "api_key": "sk-provider-create",
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
        assert fetched.json()["has_api_key"] is True

        updated = client.patch(
            f"/lm-profiles/{profile_id}",
            json={"name": "Codex Stable", "default_params": {"temperature": 0.1}, "api_key": "sk-provider-update"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Codex Stable"
        assert updated.json()["default_params"]["temperature"] == 0.1
        assert "api_key" not in created.json()
        assert "api_key" not in updated.json()
        assert FORWARDED_KEYS == ["sk-provider-create", "sk-provider-update"]

        deleted = client.delete(f"/lm-profiles/{profile_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = client.get(f"/lm-profiles/{profile_id}")
        assert missing.status_code == 404


def test_lm_profile_test_connection(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/lm-profiles",
            json={
                "name": "Probe",
                "model": "openai/codex-5.3",
                "api_base": "https://api.openai.com",
                "model_type": "responses",
                "default_params": {"temperature": 0.0},
                "api_key": "sk-provider-create",
            },
        )
        profile_id = created.json()["id"]
        tested = client.post(f"/lm-profiles/{profile_id}/test-connection")
        assert tested.status_code == 200
        assert tested.json()["ok"] is True
        assert tested.json()["reply"] == "connection-ok"


def test_lm_profile_create_accepts_legacy_upstream_api_key_alias(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/lm-profiles",
            json={
                "name": "Legacy",
                "model": "openai/codex-5.3",
                "api_base": "https://api.openai.com",
                "model_type": "responses",
                "default_params": {},
                "upstream_api_key": "sk-legacy",
            },
        )
        assert created.status_code == 200
        assert created.json()["has_api_key"] is True
