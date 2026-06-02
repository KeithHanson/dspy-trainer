import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as main_mod


MODULES = {"mod-1"}
PLANS: dict[str, dict] = {}
TASKS: dict[str, list[dict]] = {}
NEXT_PLAN_ID = 1


async def fake_connect(self):
    return None


async def fake_disconnect(self):
    return None


async def fake_create_agent_run_plan(
    self,
    project_id,
    module_import_id,
    scenario_id,
    dataset_version,
    bundle_path,
    eval_inputs,
    evaluation_plan_id,
    lm_profile_id,
    runs_per_question,
    max_workers,
):
    global NEXT_PLAN_ID
    if module_import_id not in MODULES:
        return None
    plan_id = f"plan-{NEXT_PLAN_ID}"
    NEXT_PLAN_ID += 1
    effective_eval_inputs = eval_inputs
    if evaluation_plan_id:
        effective_eval_inputs = [
            {"input": {"question": "q1"}, "label": {"expected": "a1"}},
            {"input": {"question": "q2"}, "label": {"expected": "a2"}},
        ]
    plan = {
        "id": plan_id,
        "status": "draft",
        "project_id": project_id,
        "module_import_id": module_import_id,
        "scenario_id": scenario_id,
        "dataset_version": dataset_version,
        "bundle_path": bundle_path,
        "lm_profile_id": lm_profile_id,
        "eval_inputs": effective_eval_inputs,
        "runs_per_question": runs_per_question,
        "max_workers": max_workers,
        "total_tasks": 0,
        "completed_tasks": 0,
        "failed_tasks": 0,
        "failure_reason": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    PLANS[plan_id] = plan
    TASKS[plan_id] = []
    return plan


async def fake_get_agent_run_plan(self, plan_id):
    return PLANS.get(plan_id)


async def fake_list_agent_run_plans(self, limit=50, offset=0):
    items = list(PLANS.values())[::-1]
    return items[offset : offset + limit]


async def fake_enqueue_agent_run_plan(self, plan_id):
    plan = PLANS.get(plan_id)
    if plan is None:
        return None
    items = []
    for q_idx, item in enumerate(plan["eval_inputs"]):
        for a_idx in range(max(1, int(plan["runs_per_question"]))):
            items.append(
                {
                    "id": f"task-{plan_id}-{q_idx}-{a_idx}",
                    "plan_id": plan_id,
                    "status": "queued",
                    "question_index": q_idx,
                    "attempt_index": a_idx,
                    "input_payload": item.get("input", {}),
                    "label_payload": item.get("label", {}),
                    "prediction_payload": None,
                    "score": None,
                    "rationale": None,
                    "error": None,
                    "worker_id": None,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            )
    TASKS[plan_id] = items
    plan["status"] = "queued"
    plan["total_tasks"] = len(items)
    return plan


async def fake_list_agent_run_tasks(self, plan_id, limit, offset):
    if plan_id not in PLANS:
        return None
    items = TASKS.get(plan_id, [])
    page = items[offset : offset + limit]
    return {
        "items": page,
        "limit": limit,
        "offset": offset,
        "count": len(page),
        "total": len(items),
    }


async def fake_delete_agent_run_plan(self, plan_id):
    if plan_id not in PLANS:
        return False
    del PLANS[plan_id]
    TASKS.pop(plan_id, None)
    return True


async def fake_list_agent_run_plans_for_module(self, module_import_id, limit, offset):
    if module_import_id not in MODULES:
        return None

    items = [
        {
            "id": plan["id"],
            "status": plan["status"],
            "plan_name": plan.get("plan_name") or f"plan-{plan['id']}",
            "created_at": plan["created_at"],
            "updated_at": plan["updated_at"],
        }
        for plan in PLANS.values()
        if plan["module_import_id"] == module_import_id
    ]
    items.sort(key=lambda item: item["id"], reverse=True)
    return items[offset : offset + limit]


def _patch_services(monkeypatch):
    monkeypatch.setenv("DSPY_TRAINER_POSTGRES_DSN", "postgresql://postgres:postgres@localhost:5432/dspy_trainer")
    monkeypatch.setattr(main_mod.AppServices, "connect", fake_connect)
    monkeypatch.setattr(main_mod.AppServices, "disconnect", fake_disconnect)
    monkeypatch.setattr(main_mod.AppServices, "create_agent_run_plan", fake_create_agent_run_plan)
    monkeypatch.setattr(main_mod.AppServices, "get_agent_run_plan", fake_get_agent_run_plan)
    monkeypatch.setattr(main_mod.AppServices, "list_agent_run_plans", fake_list_agent_run_plans)
    monkeypatch.setattr(main_mod.AppServices, "enqueue_agent_run_plan", fake_enqueue_agent_run_plan)
    monkeypatch.setattr(main_mod.AppServices, "list_agent_run_tasks", fake_list_agent_run_tasks)
    monkeypatch.setattr(main_mod.AppServices, "delete_agent_run_plan", fake_delete_agent_run_plan)
    monkeypatch.setattr(main_mod.AppServices, "list_agent_run_plans_for_module", fake_list_agent_run_plans_for_module)


def _reset_state():
    global NEXT_PLAN_ID
    PLANS.clear()
    TASKS.clear()
    NEXT_PLAN_ID = 1


def test_agent_run_plan_create_enqueue_and_list(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/agent-run-plans",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "scenario_id": "scn-1",
                "dataset_version": "v1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "eval_inputs": [
                    {"input": {"question": "q1"}, "label": {"expected": "a1"}},
                    {"input": {"question": "q2"}, "label": {"expected": "a2"}},
                ],
                "runs_per_question": 3,
                "max_workers": 2,
            },
        )
        assert created.status_code == 200
        plan_id = created.json()["id"]

        enqueued = client.post(f"/agent-run-plans/{plan_id}/enqueue")
        assert enqueued.status_code == 200
        assert enqueued.json()["status"] == "queued"
        assert enqueued.json()["total_tasks"] == 6

        tasks = client.get(f"/agent-run-plans/{plan_id}/tasks")
        assert tasks.status_code == 200
        assert tasks.json()["total"] == 6

        listed = client.get("/agent-run-plans")
        assert listed.status_code == 200
        assert len(listed.json()) == 1


def test_agent_run_plan_not_found_paths(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        missing_create = client.post(
            "/agent-run-plans",
            json={
                "project_id": "proj-1",
                "module_import_id": "missing",
                "scenario_id": "scn-1",
                "dataset_version": "v1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "eval_inputs": [],
                "runs_per_question": 1,
                "max_workers": 1,
            },
        )
        assert missing_create.status_code == 404
        assert client.get("/agent-run-plans/missing").status_code == 404
        assert client.post("/agent-run-plans/missing/enqueue").status_code == 404
        assert client.get("/agent-run-plans/missing/tasks").status_code == 404
        assert client.delete("/agent-run-plans/missing").status_code == 404


def test_agent_run_plan_delete(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)
    with TestClient(main_mod.app) as client:
        created = client.post(
            "/agent-run-plans",
            json={
                "project_id": "proj-1",
                "module_import_id": "mod-1",
                "scenario_id": "scn-1",
                "dataset_version": "v1",
                "bundle_path": "examples/module_bundles/simple_echo_agent",
                "eval_inputs": [],
                "runs_per_question": 1,
                "max_workers": 1,
            },
        )
        assert created.status_code == 200
        plan_id = created.json()["id"]

        deleted = client.delete(f"/agent-run-plans/{plan_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        missing = client.get(f"/agent-run-plans/{plan_id}")
        assert missing.status_code == 404


def test_module_agent_run_plans_list(monkeypatch):
    _reset_state()
    MODULES.add("mod-2")
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        for module_id in ("mod-1", "mod-2"):
            for index in range(2):
                payload = {
                    "project_id": "proj-1",
                    "module_import_id": module_id,
                    "scenario_id": f"scenario-{index}",
                    "dataset_version": "v1",
                    "bundle_path": "examples/module_bundles/simple_echo_agent",
                    "eval_inputs": [
                        {"input": {"question": "q"}, "label": {"expected": "a"}},
                    ],
                    "runs_per_question": 1,
                    "max_workers": 1,
                }
                created = client.post("/agent-run-plans", json=payload)
                assert created.status_code == 200

        response = client.get("/modules/mod-1/agent-run-plans", params={"limit": 10, "offset": 0})
        assert response.status_code == 200
        plans = response.json()
        assert len(plans) == 2
        assert all(plan["id"].startswith("plan-") for plan in plans)
        assert all(plan["plan_name"] for plan in plans)


def test_module_agent_run_plans_list_missing_module_returns_404(monkeypatch):
    _reset_state()
    _patch_services(monkeypatch)

    with TestClient(main_mod.app) as client:
        response = client.get("/modules/missing/agent-run-plans")
        assert response.status_code == 404
        assert response.json()["error"] == "module not found"
