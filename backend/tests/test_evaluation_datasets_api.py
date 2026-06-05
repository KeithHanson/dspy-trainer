import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


DATASETS: dict[str, dict] = {}
PLANS_BY_DATASET: dict[str, int] = {}
NEXT_DATASET_ID = 1


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_evaluation_dataset(self, project_id, name, description, module_import_id, records):
    global NEXT_DATASET_ID
    dataset_id = f"dataset-{NEXT_DATASET_ID}"
    NEXT_DATASET_ID += 1
    dataset = {
        "id": dataset_id,
        "project_id": project_id,
        "name": name,
        "description": description,
        "module_import_id": module_import_id,
        "bundle_name": "policy-bot",
        "bundle_version": "1.0.0",
        "records": records,
        "record_count": len(records),
        "input_keys": ["question"],
        "label_keys": ["expected"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    DATASETS[dataset_id] = dataset
    return dataset


async def fake_list_evaluation_datasets(self):
    return list(DATASETS.values())[::-1]


async def fake_get_evaluation_dataset(self, dataset_id):
    return DATASETS.get(dataset_id)


async def fake_update_evaluation_dataset(self, dataset_id, project_id, name, description, module_import_id, records):
    dataset = DATASETS.get(dataset_id)
    if dataset is None:
        return None
    dataset.update(
        {
            "project_id": project_id,
            "name": name,
            "description": description,
            "module_import_id": module_import_id,
            "records": records,
            "record_count": len(records),
        }
    )
    return dataset


async def fake_duplicate_evaluation_dataset(self, dataset_id, name=None):
    source = DATASETS.get(dataset_id)
    if source is None:
        return None
    return await fake_create_evaluation_dataset(
        self,
        project_id=source["project_id"],
        name=name or f"{source['name']} copy",
        description=source.get("description"),
        module_import_id=source["module_import_id"],
        records=source["records"],
    )


async def fake_delete_evaluation_dataset(self, dataset_id):
    if dataset_id not in DATASETS:
        return False
    if PLANS_BY_DATASET.get(dataset_id, 0) > 0:
        raise ValueError("dataset is referenced by one or more evaluation plans")
    del DATASETS[dataset_id]
    return True


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "create_evaluation_dataset", fake_create_evaluation_dataset)
    monkeypatch.setattr(main_mod.AppServices, "list_evaluation_datasets", fake_list_evaluation_datasets)
    monkeypatch.setattr(main_mod.AppServices, "get_evaluation_dataset", fake_get_evaluation_dataset)
    monkeypatch.setattr(main_mod.AppServices, "update_evaluation_dataset", fake_update_evaluation_dataset)
    monkeypatch.setattr(main_mod.AppServices, "duplicate_evaluation_dataset", fake_duplicate_evaluation_dataset)
    monkeypatch.setattr(main_mod.AppServices, "delete_evaluation_dataset", fake_delete_evaluation_dataset)


def _reset_state():
    global NEXT_DATASET_ID
    DATASETS.clear()
    PLANS_BY_DATASET.clear()
    NEXT_DATASET_ID = 1


def test_create_list_get_update_duplicate_and_delete_evaluation_datasets(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/evaluation-datasets",
            json={
                "project_id": "proj-1",
                "name": "Support dataset",
                "description": "First pass",
                "module_import_id": "mod-1",
                "records": [{"id": "item-1", "input": {"question": "q1"}, "label": {"expected": "a1"}}],
            },
        )
        assert created.status_code == 200
        dataset_id = created.json()["id"]

        listed = client.get("/evaluation-datasets")
        assert listed.status_code == 200
        assert listed.json()[0]["id"] == dataset_id

        fetched = client.get(f"/evaluation-datasets/{dataset_id}")
        assert fetched.status_code == 200
        assert fetched.json()["record_count"] == 1

        updated = client.patch(
            f"/evaluation-datasets/{dataset_id}",
            json={
                "project_id": "proj-1",
                "name": "Support dataset v2",
                "description": "Revised",
                "module_import_id": "mod-1",
                "records": [{"id": "item-2", "input": {"question": "q2"}, "label": {"expected": "a2"}}],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Support dataset v2"

        duplicated = client.post(f"/evaluation-datasets/{dataset_id}/duplicate", json={"name": "Support dataset copy"})
        assert duplicated.status_code == 200
        assert duplicated.json()["name"] == "Support dataset copy"
        assert duplicated.json()["module_import_id"] == "mod-1"

        deleted = client.delete(f"/evaluation-datasets/{dataset_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True


def test_delete_evaluation_dataset_blocks_when_plan_references_it(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/evaluation-datasets",
            json={
                "project_id": "proj-1",
                "name": "Support dataset",
                "module_import_id": "mod-1",
                "records": [{"id": "item-1", "input": {"question": "q1"}, "label": {"expected": "a1"}}],
            },
        )
        dataset_id = created.json()["id"]
        PLANS_BY_DATASET[dataset_id] = 1

        deleted = client.delete(f"/evaluation-datasets/{dataset_id}")
        assert deleted.status_code == 409
        assert "referenced" in deleted.json()["error"]
