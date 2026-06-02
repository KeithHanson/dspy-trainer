import asyncio
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "module_bundles"


def _build_services() -> AppServices:
    return AppServices(
        Settings(
            postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer",
        )
    )


def test_derive_demo_dataset_from_run_passes(monkeypatch):
    services = _build_services()
    bundle_path = str(FIXTURES / "valid_bundle")

    async def fake_get_agent_run_plan(plan_id):
        return {
            "id": plan_id,
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": bundle_path,
        }

    async def fake_list_agent_run_tasks(plan_id, limit, offset):
        items = [
            {
                "id": "item-pass-label",
                "plan_id": plan_id,
                "score": 1.0,
                "input_payload": {"question": "France capital?"},
                "label_payload": {"expected": "Paris"},
                "prediction_payload": {"answer": "Paris"},
                "rationale": "exact_match",
            },
            {
                "id": "item-fail",
                "plan_id": plan_id,
                "score": 0.0,
                "input_payload": {"question": "France capital?"},
                "label_payload": {"expected": "Paris"},
                "prediction_payload": {"answer": "London"},
                "rationale": "wrong_answer",
            },
            {
                "id": "item-pass-no-target",
                "plan_id": plan_id,
                "score": 1.0,
                "input_payload": {"question": "Spain capital?"},
                "label_payload": {},
                "prediction_payload": {},
                "rationale": "accepted_but_empty",
            },
        ]
        page = items[offset : offset + limit]
        return {"items": page, "count": len(page), "total": len(items), "limit": limit, "offset": offset}

    monkeypatch.setattr(services, "get_agent_run_plan", fake_get_agent_run_plan)
    monkeypatch.setattr(services, "list_agent_run_tasks", fake_list_agent_run_tasks)

    dataset = asyncio.run(
        services.derive_optimization_dataset(
            project_id="proj-1",
            module_import_id="mod-1",
            name="Run Passes",
            dataset_kind="demo",
            source_type="eval_passes",
            source_run_plan_ids=["plan-1"],
            source_filters={},
            persist=False,
        )
    )

    assert dataset is not None
    assert dataset["preview"] is True
    assert dataset["record_count"] == 1
    assert dataset["input_keys"] == ["question"]
    assert dataset["label_keys"] == ["expected"]
    assert dataset["records"][0]["label"] == {"expected": "Paris"}
    assert dataset["records"][0]["prediction"] == {"answer": "Paris"}
    assert dataset["records"][0]["label_provenance"] == "label_payload"
    assert dataset["provenance_summary"]["excluded_reasons"] == {
        "score_below_threshold": 1,
        "missing_demo_target": 1,
    }
    assert dataset["source_filters"]["score_threshold"] == 0.8


def test_derive_feedback_dataset_includes_failures_and_can_persist(monkeypatch):
    services = _build_services()
    bundle_path = str(FIXTURES / "valid_bundle")

    async def fake_get_agent_run_plan(plan_id):
        return {
            "id": plan_id,
            "project_id": "proj-1",
            "module_import_id": "mod-1",
            "bundle_path": bundle_path,
        }

    async def fake_list_agent_run_tasks(plan_id, limit, offset):
        items = [
            {
                "id": "item-pass",
                "plan_id": plan_id,
                "score": 1.0,
                "input_payload": {"question": "France capital?"},
                "label_payload": {"expected": "Paris"},
                "prediction_payload": {"answer": "Paris"},
                "rationale": "exact_match",
            },
            {
                "id": "item-fail",
                "plan_id": plan_id,
                "score": 0.0,
                "input_payload": {"question": "France capital?"},
                "label_payload": {},
                "prediction_payload": {"answer": "London"},
                "rationale": "wrong_answer",
            },
        ]
        page = items[offset : offset + limit]
        return {"items": page, "count": len(page), "total": len(items), "limit": limit, "offset": offset}

    captured: dict[str, Any] = {}

    async def fake_create_optimization_dataset(**kwargs):
        captured.update(kwargs)
        return {"id": "ods-1", **kwargs}

    monkeypatch.setattr(services, "get_agent_run_plan", fake_get_agent_run_plan)
    monkeypatch.setattr(services, "list_agent_run_tasks", fake_list_agent_run_tasks)
    monkeypatch.setattr(services, "create_optimization_dataset", fake_create_optimization_dataset)

    dataset = asyncio.run(
        services.derive_optimization_dataset(
            project_id="proj-1",
            module_import_id="mod-1",
            name="Run Feedback",
            dataset_kind="feedback",
            source_type="eval_feedback",
            source_run_plan_ids=["plan-1"],
            source_filters={},
            persist=True,
        )
    )

    assert dataset is not None
    assert dataset["id"] == "ods-1"
    assert captured["optimizer_contract"] == "dspy_example_v1"
    assert len(captured["records"]) == 2
    assert captured["records"][0]["passed"] is True
    assert captured["records"][1]["passed"] is False
    assert captured["records"][1]["feedback"] == "wrong_answer"
    assert captured["provenance_summary"]["passing_records"] == 1
    assert captured["provenance_summary"]["failing_records"] == 1
