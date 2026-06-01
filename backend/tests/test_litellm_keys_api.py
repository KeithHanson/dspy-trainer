import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


KEYS: dict[str, dict] = {}


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_list_litellm_keys(self):
    return {"keys": list(KEYS.values())}


async def fake_create_litellm_key(self, models, aliases, metadata, duration, key_alias, team_id, user_id):
    key = f"sk-{len(KEYS) + 1}"
    payload = {
        "key": key,
        "models": models,
        "aliases": aliases,
        "metadata": metadata,
        "duration": duration,
        "key_alias": key_alias,
        "team_id": team_id,
        "user_id": user_id,
        "blocked": False,
    }
    KEYS[key] = payload
    return payload


async def fake_get_litellm_key_info(self, key):
    return KEYS.get(key, {"key": key, "missing": True})


async def fake_update_litellm_key(self, key, models, aliases, metadata, duration, max_budget, rpm_limit, tpm_limit):
    payload = KEYS.get(key, {"key": key})
    if models is not None:
        payload["models"] = models
    if aliases is not None:
        payload["aliases"] = aliases
    if metadata is not None:
        payload["metadata"] = metadata
    if duration is not None:
        payload["duration"] = duration
    if max_budget is not None:
        payload["max_budget"] = max_budget
    if rpm_limit is not None:
        payload["rpm_limit"] = rpm_limit
    if tpm_limit is not None:
        payload["tpm_limit"] = tpm_limit
    KEYS[key] = payload
    return payload


async def fake_revoke_litellm_key(self, key):
    payload = KEYS.get(key, {"key": key})
    payload["blocked"] = True
    KEYS[key] = payload
    return payload


async def fake_restore_litellm_key(self, key):
    payload = KEYS.get(key, {"key": key})
    payload["blocked"] = False
    KEYS[key] = payload
    return payload


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "list_litellm_keys", fake_list_litellm_keys)
    monkeypatch.setattr(main_mod.AppServices, "create_litellm_key", fake_create_litellm_key)
    monkeypatch.setattr(main_mod.AppServices, "get_litellm_key_info", fake_get_litellm_key_info)
    monkeypatch.setattr(main_mod.AppServices, "update_litellm_key", fake_update_litellm_key)
    monkeypatch.setattr(main_mod.AppServices, "revoke_litellm_key", fake_revoke_litellm_key)
    monkeypatch.setattr(main_mod.AppServices, "restore_litellm_key", fake_restore_litellm_key)


def _reset_state():
    KEYS.clear()


def test_litellm_keys_api(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/litellm/keys",
            json={
                "models": ["openai/codex-5.3"],
                "aliases": {"default": "openai/codex-5.3"},
                "metadata": {"owner": "team"},
                "duration": "30d",
            },
        )
        assert created.status_code == 200
        key = created.json()["key"]

        listed = client.get("/litellm/keys")
        assert listed.status_code == 200
        assert len(listed.json()["keys"]) == 1

        fetched = client.get(f"/litellm/keys/{key}")
        assert fetched.status_code == 200
        assert fetched.json()["key"] == key

        updated = client.patch(
            f"/litellm/keys/{key}",
            json={"key": key, "metadata": {"owner": "platform"}, "rpm_limit": 120},
        )
        assert updated.status_code == 200
        assert updated.json()["metadata"]["owner"] == "platform"
        assert updated.json()["rpm_limit"] == 120

        revoked = client.post(f"/litellm/keys/{key}/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["blocked"] is True

        restored = client.post(f"/litellm/keys/{key}/restore")
        assert restored.status_code == 200
        assert restored.json()["blocked"] is False
