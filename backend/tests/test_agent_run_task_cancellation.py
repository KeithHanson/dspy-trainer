import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


class _FakeRow(dict):
    def __getattr__(self, name):
        return self[name]


class _FakeConn:
    def __init__(self, state):
        self.state = state

    async def fetchrow(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "from agent_run_tasks t join agent_run_plans p" in normalized:
            task_id = str(params[0])
            task = self.state["task"]
            plan = self.state["plan"]
            profile = self.state["profile"]
            if task_id != task["id"]:
                return None
            return _FakeRow(
                {
                    "id": task["id"],
                    "plan_id": task["plan_id"],
                    "status": task["status"],
                    "input_payload": task["input_payload"],
                    "label_payload": task["label_payload"],
                    "bundle_path": plan["bundle_path"],
                    "module_import_id": plan["module_import_id"],
                    "max_workers": plan["max_workers"],
                    "mlflow_experiment_id": plan["mlflow_experiment_id"],
                    "mlflow_parent_run_id": plan["mlflow_parent_run_id"],
                    "project_id": plan["project_id"],
                    "plan_status": plan["status"],
                    "lm_profile_id": profile["id"],
                    "lm_model": profile["model"],
                    "lm_api_base": profile["api_base"],
                    "lm_model_type": profile["model_type"],
                    "lm_default_params": profile["default_params"],
                    "lm_class_path": profile["lm_class_path"],
                    "lm_virtual_key": profile["virtual_key"],
                }
            )
        if normalized.startswith("select id, status from agent_run_tasks where id = $1"):
            return _FakeRow({"id": self.state["task"]["id"], "status": self.state["task"]["status"]})
        return None

    async def fetchval(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if "select count(*) from agent_run_tasks where plan_id = $1 and status = 'running'" in normalized:
            return 0 if self.state["task"]["status"] != "running" else 1
        if normalized.startswith("select status from agent_run_plans where id = $1"):
            return self.state["plan"]["status"]
        if normalized.startswith("select count(*) from agent_run_tasks where plan_id = $1"):
            return 1
        if "select count(*) from agent_run_tasks where plan_id = $1 and status = 'succeeded'" in normalized:
            return 0
        if "select count(*) from agent_run_tasks where plan_id = $1 and status = 'failed'" in normalized:
            return 0
        if "select count(*) from agent_run_tasks where plan_id = $1 and status in ('pending','queued','running')" in normalized:
            return 0 if self.state["task"]["status"] == "canceled" else 1
        return None

    async def execute(self, query, *params):
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("update agent_run_tasks set status='running'"):
            self.state["task"]["status"] = "running"
            self.state["task"]["worker_id"] = params[1]
            self.state["task"]["worker_log"] = params[2]
            return "UPDATE 1"
        if normalized.startswith("update agent_run_plans set status='running'"):
            self.state["plan"]["status"] = "running"
            return "UPDATE 1"
        if normalized.startswith("update agent_run_tasks set status='canceled'"):
            self.state["task"]["status"] = "canceled"
            self.state["task"]["error"] = params[1]
            self.state["task"]["worker_log"] = params[2]
            return "UPDATE 1"
        if normalized.startswith("update agent_run_plans set total_tasks"):
            return "UPDATE 1"
        return "UPDATE 0"


class _FakeAcquire:
    def __init__(self, state):
        self.conn = _FakeConn(state)

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, state):
        self.state = state

    def acquire(self):
        return _FakeAcquire(self.state)


class FakeProcess:
    def __init__(self):
        self._alive = True
        self.terminated = False
        self.killed = False

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True
        self._alive = False

    def join(self, timeout=None):
        return None

    def kill(self):
        self.killed = True
        self._alive = False


class FakeQueue:
    def get_nowait(self):
        raise __import__("queue").Empty


def _build_state():
    return {
        "plan": {
            "id": "plan-1",
            "status": "queued",
            "bundle_path": "examples/module_bundles/simple_echo_agent",
            "module_import_id": "mod-1",
            "max_workers": 1,
            "mlflow_experiment_id": None,
            "mlflow_parent_run_id": None,
            "project_id": "proj-1",
        },
        "task": {
            "id": "task-1",
            "plan_id": "plan-1",
            "status": "queued",
            "input_payload": json.dumps({"question": "q1"}),
            "label_payload": json.dumps({"expected": "a1"}),
            "worker_id": None,
            "worker_log": "",
            "error": None,
        },
        "profile": {
            "id": "lm-1",
            "model": "openai/gpt-4o-mini",
            "api_base": "http://litellm:4000",
            "model_type": "responses",
            "default_params": json.dumps({}),
            "lm_class_path": None,
            "virtual_key": "vk",
        },
    }


def test_run_agent_run_task_terminates_inflight_work_when_plan_is_canceled(monkeypatch):
    state = _build_state()
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    setattr(services, "postgres_pool", FakePool(state))

    process = FakeProcess()
    queue = FakeQueue()

    async def fake_requirements(_bundle_path, cancel_check=None):
        return None

    async def fake_runtime_env(_module_id):
        return {}

    async def fake_reconcile(_plan_id):
        return None

    async def fake_queue_more(_plan_id):
        return None

    async def fake_plan_status(_plan_id):
        if state["task"]["status"] == "running":
            state["plan"]["status"] = "canceled"
        return state["plan"]["status"]

    monkeypatch.setattr(services, "ensure_bundle_requirements_installed", fake_requirements)
    monkeypatch.setattr(services, "get_module_runtime_environment", fake_runtime_env)
    monkeypatch.setattr(services, "_reconcile_agent_run_plan", fake_reconcile)
    monkeypatch.setattr(services, "_queue_more_agent_run_tasks", fake_queue_more)
    monkeypatch.setattr(services, "_get_agent_run_plan_status", fake_plan_status)
    monkeypatch.setattr(services, "_start_agent_run_eval_process", lambda payload: (process, queue))

    result = asyncio.run(services.run_agent_run_task("task-1", worker_id="worker-1"))

    assert process.terminated is True
    assert result is not None
    assert result["status"] == "canceled"
    assert state["task"]["status"] == "canceled"
    assert state["task"]["error"] == "eval run canceled by operator"
