import asyncio
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


class FakeRedis:
    def __init__(self, payloads=None):
        self.payloads = payloads or {}
        self.commands = []

    async def keys(self, pattern):
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        return [key for key in self.payloads.keys() if key.startswith(prefix)]

    async def get(self, key):
        return self.payloads.get(key)

    async def execute_command(self, *args):
        self.commands.append(args)
        return None


def _make_endpoint_services():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    services.postgres_pool = object()
    return services


def test_list_workers_reports_configured_total_even_when_some_workers_are_missing():
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer", total_workers=8))
    setattr(
        services,
        "redis",
        FakeRedis(
            {
                "dspy-trainer:workers:worker-1": '{"worker_id":"worker-1","status":"listening","task_id":null,"last_seen":"2026-01-01T00:00:00+00:00"}',
                "dspy-trainer:workers:worker-2": '{"worker_id":"worker-2","status":"running","task_id":"task-2","last_seen":"2026-01-01T00:00:00+00:00"}',
            }
        ),
    )

    payload = asyncio.run(services.list_workers())

    assert payload["total_workers"] == 8
    assert payload["reported_workers"] == 2
    assert payload["available_workers"] == 1
    assert payload["busy_workers"] == 1
    assert [item["worker_id"] for item in payload["items"]] == ["worker-1", "worker-2"]


def test_list_endpoint_workers_exposes_revision_state(monkeypatch):
    services = _make_endpoint_services()

    async def fake_list_endpoint_worker_registrations(*, now=None):
        return [
            {
                "worker_id": "endpoint-worker-1",
                "status": "listening",
                "raw_status": "listening",
                "assigned_endpoint_id": "endpoint-1",
                "endpoint_id": "endpoint-1",
                "desired_revision_id": "rev-22222222",
                "warmed_revision_id": "rev-22222222",
                "kind": "endpoint",
                "is_live": True,
                "deploy_state": "ready",
                "is_revision_ready": True,
                "state_summary": "ready",
                "state_label": "Ready",
            },
            {
                "worker_id": "endpoint-worker-2",
                "status": "listening",
                "raw_status": "listening",
                "assigned_endpoint_id": "endpoint-1",
                "endpoint_id": "endpoint-1",
                "desired_revision_id": "rev-22222222",
                "warmed_revision_id": "rev-11111111",
                "kind": "endpoint",
                "is_live": True,
                "deploy_state": "revision_mismatch",
                "is_revision_ready": False,
                "state_summary": "Desired revision rev-2222 is not warmed yet.",
                "state_label": "Stale",
            },
            {
                "worker_id": "endpoint-worker-3",
                "status": "preparing",
                "raw_status": "preparing",
                "assigned_endpoint_id": "endpoint-2",
                "endpoint_id": "endpoint-2",
                "desired_revision_id": "rev-33333333",
                "warmed_revision_id": "rev-22222222",
                "kind": "endpoint",
                "is_live": True,
                "deploy_state": "warming",
                "is_revision_ready": False,
                "state_summary": "warming",
                "state_label": "Preparing",
            },
            {
                "worker_id": "endpoint-worker-4",
                "status": "idle",
                "raw_status": "idle",
                "assigned_endpoint_id": None,
                "endpoint_id": None,
                "desired_revision_id": None,
                "warmed_revision_id": None,
                "kind": "endpoint",
                "is_live": True,
                "deploy_state": "unassigned",
                "is_revision_ready": True,
                "state_summary": "idle",
                "state_label": "Idle",
            },
        ]

    monkeypatch.setattr(services, "list_endpoint_worker_registrations", fake_list_endpoint_worker_registrations)
    payload = asyncio.run(services.list_endpoint_workers())

    assert payload["available_workers"] == 2
    assert payload["live_workers"] == 4
    assert payload["stale_workers"] == 0
    assert payload["assigned_workers"] == 3
    assert payload["unassigned_workers"] == 1
    assert payload["ready_workers"] == 2
    assert payload["warming_workers"] == 1
    assert payload["running_workers"] == 0
    assert payload["failed_workers"] == 0
    assert payload["items"][0]["desired_revision_id"] == "rev-22222222"
    assert payload["items"][0]["deploy_state"] == "ready"
    assert payload["items"][1]["warmed_revision_id"] == "rev-11111111"
    assert payload["items"][1]["deploy_state"] == "revision_mismatch"
    assert "rev-2222" in payload["items"][1]["state_summary"]
    assert payload["items"][2]["deploy_state"] == "warming"
    assert payload["items"][3]["state_label"] == "Idle"
    assert payload["items"][3]["deploy_state"] == "unassigned"


def test_list_endpoint_workers_does_not_count_listening_revision_mismatch_as_available(monkeypatch):
    services = _make_endpoint_services()

    async def fake_list_endpoint_worker_registrations(*, now=None):
        return [
            {
                "worker_id": "endpoint-worker-1",
                "status": "listening",
                "raw_status": "listening",
                "assigned_endpoint_id": "endpoint-1",
                "endpoint_id": "endpoint-1",
                "desired_revision_id": "rev-22222222",
                "warmed_revision_id": "rev-11111111",
                "kind": "endpoint",
                "is_live": True,
                "deploy_state": "revision_mismatch",
                "is_revision_ready": False,
            },
            {
                "worker_id": "endpoint-worker-2",
                "status": "listening",
                "raw_status": "listening",
                "assigned_endpoint_id": "endpoint-1",
                "endpoint_id": "endpoint-1",
                "desired_revision_id": "rev-22222222",
                "warmed_revision_id": "rev-22222222",
                "kind": "endpoint",
                "is_live": True,
                "deploy_state": "ready",
                "is_revision_ready": True,
            },
            {
                "worker_id": "endpoint-worker-3",
                "status": "idle",
                "raw_status": "idle",
                "assigned_endpoint_id": None,
                "endpoint_id": None,
                "desired_revision_id": None,
                "warmed_revision_id": None,
                "kind": "endpoint",
                "is_live": True,
                "deploy_state": "unassigned",
                "is_revision_ready": True,
            },
        ]

    monkeypatch.setattr(services, "list_endpoint_worker_registrations", fake_list_endpoint_worker_registrations)
    payload = asyncio.run(services.list_endpoint_workers())

    assert payload["available_workers"] == 2
    assert payload["busy_workers"] == 1
    assert payload["live_workers"] == 3
    assert payload["stale_workers"] == 0
    assert payload["assigned_workers"] == 2
    assert payload["unassigned_workers"] == 1
    assert payload["ready_workers"] == 2
    assert payload["items"][0]["deploy_state"] == "revision_mismatch"
    assert payload["items"][0]["is_revision_ready"] is False
    assert payload["items"][1]["deploy_state"] == "ready"
    assert payload["items"][2]["deploy_state"] == "unassigned"


def test_enqueue_endpoint_invocation_succeeds_once_worker_is_listening_on_current_revision(monkeypatch):
    services = _make_endpoint_services()
    redis = FakeRedis()
    setattr(services, "redis", redis)

    async def fake_reconcile():
        return None

    async def fake_get_bundle_endpoint(endpoint_id):
        return {"id": endpoint_id, "module_import_id": "mod-1"}

    async def fake_resolve_module_execution_state(module_id):
        return {"module_id": module_id, "bundle_revision_id": "rev-2"}

    async def fake_list_endpoint_workers(*, now=None):
        return {
            "items": [
                {
                    "worker_id": "endpoint-worker-1",
                    "status": "listening",
                    "raw_status": "listening",
                    "endpoint_id": "endpoint-1",
                    "assigned_endpoint_id": "endpoint-1",
                    "desired_revision_id": "rev-2",
                    "warmed_revision_id": "rev-2",
                    "is_live": True,
                }
            ]
        }

    monkeypatch.setattr(services, "reconcile_endpoint_worker_assignments", fake_reconcile)
    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_module_execution_state", fake_resolve_module_execution_state)
    monkeypatch.setattr(services, "list_endpoint_workers", fake_list_endpoint_workers)

    invocation_id = asyncio.run(
        services.enqueue_endpoint_invocation("endpoint-1", {"question": "hello"}, stream=False, invocation_id="inv-1")
    )

    assert invocation_id == "inv-1"
    assert redis.commands == [
        (
            "LPUSH",
            "dspy-trainer:endpoint-queues:endpoint-1",
            '{"type": "endpoint_invocation", "invocation_id": "inv-1", "endpoint_id": "endpoint-1", "input_payload": {"question": "hello"}, "stream": false}',
        )
    ]


def test_enqueue_endpoint_invocation_rejects_worker_with_stale_desired_revision(monkeypatch):
    services = _make_endpoint_services()
    redis = FakeRedis()
    setattr(services, "redis", redis)

    async def fake_reconcile():
        return None

    async def fake_get_bundle_endpoint(endpoint_id):
        return {"id": endpoint_id, "module_import_id": "mod-1"}

    async def fake_resolve_module_execution_state(module_id):
        return {"module_id": module_id, "bundle_revision_id": "rev-2"}

    async def fake_list_endpoint_workers(*, now=None):
        return {
            "items": [
                {
                    "worker_id": "endpoint-worker-1",
                    "status": "listening",
                    "raw_status": "listening",
                    "endpoint_id": "endpoint-1",
                    "assigned_endpoint_id": "endpoint-1",
                    "desired_revision_id": "rev-1",
                    "warmed_revision_id": "rev-2",
                    "is_live": True,
                }
            ]
        }

    monkeypatch.setattr(services, "reconcile_endpoint_worker_assignments", fake_reconcile)
    monkeypatch.setattr(services, "get_bundle_endpoint", fake_get_bundle_endpoint)
    monkeypatch.setattr(services, "resolve_module_execution_state", fake_resolve_module_execution_state)
    monkeypatch.setattr(services, "list_endpoint_workers", fake_list_endpoint_workers)

    try:
        asyncio.run(services.enqueue_endpoint_invocation("endpoint-1", {"question": "hello"}, stream=False, invocation_id="inv-1"))
    except RuntimeError as exc:
        assert str(exc) == "endpoint has no ready endpoint workers"
    else:
        raise AssertionError("expected enqueue_endpoint_invocation to reject stale worker")

    assert redis.commands == []
