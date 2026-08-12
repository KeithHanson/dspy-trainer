import asyncio
from datetime import datetime, timedelta, timezone
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services import AppServices


class _RegistryConn:
    def __init__(self, state):
        self.state = state
        self.queries: list[str] = []

    async def fetchrow(self, query, *params):
        self.queries.append(query)
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("insert into endpoint_worker_registrations"):
            worker_id = str(params[0])
            existing = self.state["workers"].get(worker_id)
            created_at = existing["created_at"] if existing else params[11]
            row = {
                "worker_id": worker_id,
                "runtime_instance_id": params[1],
                "status": params[2],
                "assigned_endpoint_id": params[3],
                "task_id": params[4],
                "last_seen_at": params[5],
                "heartbeat_expires_at": params[6],
                "hostname": params[7],
                "pid": params[8],
                "runtime_metadata": json.loads(params[9]),
                "last_error": params[10],
                "created_at": created_at,
                "updated_at": params[11],
            }
            self.state["workers"][worker_id] = row
            return row
        if normalized.startswith("update endpoint_worker_registrations set runtime_instance_id = coalesce($2, runtime_instance_id)"):
            worker_id = str(params[0])
            row = self.state["workers"].get(worker_id)
            if row is None:
                return None
            runtime_instance_id = params[1]
            if "where worker_id = $1 and runtime_instance_id = $2" in normalized and row["runtime_instance_id"] != runtime_instance_id:
                return None
            row.update(
                {
                    "runtime_instance_id": runtime_instance_id or row["runtime_instance_id"],
                    "status": params[2],
                    "task_id": params[4],
                    "last_seen_at": params[5],
                    "heartbeat_expires_at": params[6],
                    "hostname": params[7],
                    "pid": params[8],
                    "runtime_metadata": json.loads(params[9]),
                    "last_error": params[10],
                    "updated_at": params[5],
                }
            )
            return dict(row)
        if normalized.startswith("select worker_id, runtime_instance_id, status, assigned_endpoint_id, task_id, last_seen_at,") and "where worker_id = $1" in normalized:
            row = self.state["workers"].get(str(params[0]))
            return None if row is None else dict(row)
        return None

    async def fetch(self, query, *params):
        del params
        self.queries.append(query)
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("select worker_id, runtime_instance_id, status, assigned_endpoint_id, task_id, last_seen_at,"):
            return [
                dict(row)
                for _, row in sorted(
                    self.state["workers"].items(), key=lambda item: (item[1]["created_at"], item[0])
                )
            ]
        return []

    async def execute(self, query, *params):
        self.queries.append(query)
        normalized = " ".join(query.strip().lower().split())
        if normalized.startswith("update endpoint_worker_registrations set status = 'stale', updated_at = $1"):
            count = 0
            stale_time = params[0]
            for row in self.state["workers"].values():
                if row["heartbeat_expires_at"] <= stale_time and row["status"] != "stale":
                    row["status"] = "stale"
                    row["updated_at"] = stale_time
                    count += 1
            return f"UPDATE {count}"
        if normalized.startswith("update endpoint_worker_registrations set assigned_endpoint_id = $2,"):
            worker_id = str(params[0])
            row = self.state["workers"].get(worker_id)
            if row is None:
                return "UPDATE 0"
            row["assigned_endpoint_id"] = params[1]
            row["updated_at"] = params[2]
            return "UPDATE 1"
        return "OK"


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RegistryPool:
    def __init__(self):
        self.state = {"workers": {}}
        self.conn = _RegistryConn(self.state)

    def acquire(self):
        return _Acquire(self.conn)


class _Redis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False, xx=False):
        del ex
        if nx and key in self.values:
            return False
        if xx and key not in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def keys(self, pattern):
        prefix = pattern[:-1] if pattern.endswith("*") else pattern
        return [key for key in self.values if key.startswith(prefix)]


def _make_services() -> AppServices:
    services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
    services.postgres_pool = _RegistryPool()
    return services


def test_init_db_creates_endpoint_worker_registry_schema_and_indexes():
    services = _make_services()

    asyncio.run(services.init_db())

    queries = services.postgres_pool.conn.queries
    assert any("create table if not exists endpoint_worker_registrations" in query for query in queries)
    assert any("worker_id text primary key" in query for query in queries)
    assert any("runtime_instance_id text not null" in query for query in queries)
    assert any("assigned_endpoint_id text references bundle_endpoints(id) on delete set null" in query for query in queries)
    assert any("heartbeat_expires_at timestamptz not null" in query for query in queries)
    assert any("runtime_metadata jsonb not null default '{}'::jsonb" in query for query in queries)
    assert any("idx_endpoint_worker_registrations_heartbeat_expires_at" in query for query in queries)
    assert any("idx_endpoint_worker_registrations_assigned_endpoint_id" in query for query in queries)
    assert any("idx_endpoint_worker_registrations_status" in query for query in queries)


def test_endpoint_worker_registry_register_heartbeat_and_stale_transition():
    async def scenario() -> None:
        services = _make_services()
        registered_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
        registered = await services.register_endpoint_worker(
            worker_id="endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="idle",
            hostname="host-1",
            pid=101,
            runtime_metadata={"boot": "one"},
            now=registered_at,
        )

        assert registered["worker_id"] == "endpoint-worker-1"
        assert registered["status"] == "idle"
        assert registered["is_live"] is True
        assert registered["heartbeat_expires_at"] == (registered_at + timedelta(seconds=15)).isoformat()

        heartbeat_at = registered_at + timedelta(seconds=5)
        heartbeat = await services.heartbeat_endpoint_worker(
            "endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="running",
            task_id="inv-1",
            runtime_metadata={"boot": "one", "state": "busy"},
            now=heartbeat_at,
        )

        assert heartbeat is not None
        assert heartbeat["status"] == "running"
        assert heartbeat["task_id"] == "inv-1"
        assert heartbeat["last_seen_at"] == heartbeat_at.isoformat()

        stale_count = await services.mark_stale_endpoint_workers(now=heartbeat_at + timedelta(seconds=16))
        assert stale_count == 1

        workers = await services.list_endpoint_worker_registrations(now=heartbeat_at + timedelta(seconds=16))
        assert len(workers) == 1
        assert workers[0]["worker_id"] == "endpoint-worker-1"
        assert workers[0]["status"] == "stale"
        assert workers[0]["raw_status"] == "stale"
        assert workers[0]["is_live"] is False
        assert workers[0]["is_stale"] is True

    asyncio.run(scenario())


def test_list_endpoint_workers_uses_registry_backed_summaries():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        await services.register_endpoint_worker(
            worker_id="endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="listening",
            hostname="host-1",
            pid=101,
            now=now,
        )
        await services.register_endpoint_worker(
            worker_id="endpoint-worker-2",
            runtime_instance_id="runtime-2",
            status="preparing",
            hostname="host-2",
            pid=102,
            now=now,
        )
        await services.register_endpoint_worker(
            worker_id="endpoint-worker-3",
            runtime_instance_id="runtime-3",
            status="failed",
            hostname="host-3",
            pid=103,
            now=now,
        )

        await services._set_endpoint_worker_assignment("endpoint-worker-1", "endpoint-1")
        await services._set_endpoint_worker_assignment("endpoint-worker-2", "endpoint-1")

        await services.mark_stale_endpoint_workers(now=now + timedelta(seconds=16))
        await services.heartbeat_endpoint_worker(
            "endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="listening",
            assigned_endpoint_id="endpoint-1",
            hostname="host-1",
            pid=101,
            now=now + timedelta(seconds=5),
        )
        await services.heartbeat_endpoint_worker(
            "endpoint-worker-2",
            runtime_instance_id="runtime-2",
            status="preparing",
            assigned_endpoint_id="endpoint-1",
            hostname="host-2",
            pid=102,
            now=now + timedelta(seconds=5),
        )

        payload = await services.list_endpoint_workers(now=now + timedelta(seconds=16))

        assert payload["total_workers"] == 3
        assert payload["reported_workers"] == 3
        assert payload["available_workers"] == 1
        assert payload["busy_workers"] == 2
        assert payload["live_workers"] == 2
        assert payload["stale_workers"] == 1
        assert payload["assigned_workers"] == 2
        assert payload["unassigned_workers"] == 1
        assert payload["ready_workers"] == 1
        assert payload["warming_workers"] == 1
        assert payload["running_workers"] == 0
        assert payload["failed_workers"] == 0
        assert payload["summary"] == {
            "live_workers": 2,
            "stale_workers": 1,
            "assigned_workers": 2,
            "unassigned_workers": 1,
            "ready_workers": 1,
            "warming_workers": 1,
            "running_workers": 0,
            "failed_workers": 0,
        }
        assert [item["worker_id"] for item in payload["items"]] == [
            "endpoint-worker-1",
            "endpoint-worker-2",
            "endpoint-worker-3",
        ]
        assert payload["items"][-1]["status"] == "stale"
        assert payload["items"][-1]["assigned_endpoint_id"] is None

    asyncio.run(scenario())


def test_reconcile_endpoint_worker_assignments_uses_registered_workers_without_static_ids():
    async def scenario() -> None:
        services = _make_services()
        services.redis = _Redis()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1"}

        async def resolve_module_execution_state(module_id: str):
            return {"module_id": module_id, "bundle_revision_id": "rev-1"}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_module_execution_state = resolve_module_execution_state  # type: ignore[method-assign]

        registered_1 = await services.register_endpoint_worker(
            runtime_instance_id="runtime-1",
            status="idle",
            hostname="host-1",
            pid=101,
            now=now,
        )
        registered_2 = await services.register_endpoint_worker(
            runtime_instance_id="runtime-2",
            status="idle",
            hostname="host-2",
            pid=102,
            now=now + timedelta(seconds=1),
        )

        await services.reconcile_endpoint_worker_assignments()

        workers = await services.list_endpoint_worker_registrations(now=now)
        inventory_2 = json.loads(services.redis.values[f"dspy-trainer:endpoint-worker-inventory:{registered_2['worker_id']}"])

        assert workers[0]["worker_id"] == registered_1["worker_id"]
        assert workers[0]["assigned_endpoint_id"] is None
        assert workers[1]["worker_id"] == registered_2["worker_id"]
        assert workers[1]["assigned_endpoint_id"] == "endpoint-1"
        assert inventory_2["endpoint_id"] == "endpoint-1"
        assert inventory_2["desired_revision_id"] == "rev-1"
        assert f"dspy-trainer:endpoint-worker-assignments:{registered_1['worker_id']}" not in services.redis.values

    asyncio.run(scenario())


def test_reconcile_endpoint_worker_assignments_prioritizes_live_workers_over_newer_stale_workers():
    async def scenario() -> None:
        services = _make_services()
        services.redis = _Redis()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1"}

        async def resolve_module_execution_state(module_id: str):
            return {"module_id": module_id, "bundle_revision_id": "rev-1"}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_module_execution_state = resolve_module_execution_state  # type: ignore[method-assign]

        live_worker = await services.register_endpoint_worker(
            runtime_instance_id="runtime-1",
            status="idle",
            hostname="host-1",
            pid=101,
            now=now,
        )
        stale_worker = await services.register_endpoint_worker(
            runtime_instance_id="runtime-2",
            status="idle",
            hostname="host-2",
            pid=102,
            now=now + timedelta(seconds=1),
        )
        await services.heartbeat_endpoint_worker(
            live_worker["worker_id"],
            runtime_instance_id="runtime-1",
            status="idle",
            hostname="host-1",
            pid=101,
            now=now + timedelta(seconds=10),
        )

        reconcile_at = now + timedelta(seconds=17)
        await services.mark_stale_endpoint_workers(now=reconcile_at)
        await services.reconcile_endpoint_worker_assignments()
        ordered_worker_ids = await services._registered_endpoint_worker_ids_for_assignment(now=reconcile_at)
        workers = await services.list_endpoint_worker_registrations(now=reconcile_at)

        assert ordered_worker_ids[:2] == [live_worker["worker_id"], stale_worker["worker_id"]]
        assert next(item for item in workers if item["worker_id"] == live_worker["worker_id"])["assigned_endpoint_id"] == "endpoint-1"
        assert next(item for item in workers if item["worker_id"] == stale_worker["worker_id"])["assigned_endpoint_id"] is None
        assert f"dspy-trainer:endpoint-worker-assignments:{stale_worker['worker_id']}" not in services.redis.values

    asyncio.run(scenario())


def test_registry_assignment_remains_control_plane_owned_across_worker_heartbeats():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1"}

        async def resolve_module_execution_state(module_id: str):
            return {"module_id": module_id, "bundle_revision_id": "rev-1"}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_module_execution_state = resolve_module_execution_state  # type: ignore[method-assign]

        worker = await services.register_endpoint_worker(
            runtime_instance_id="runtime-1",
            status="idle",
            hostname="host-1",
            pid=101,
            now=now,
        )
        await services.heartbeat_endpoint_worker(
            worker["worker_id"],
            runtime_instance_id="runtime-1",
            status="listening",
            assigned_endpoint_id="endpoint-2",
            runtime_metadata={"endpoint_id": "endpoint-1", "desired_revision_id": "rev-1", "warmed_revision_id": "rev-1"},
            now=now + timedelta(seconds=1),
        )

        assignment = await services.get_endpoint_worker_assignment(worker["worker_id"])
        registration = await services._get_endpoint_worker_registration(worker["worker_id"], now=now + timedelta(seconds=1))

        assert assignment == {
            "worker_id": worker["worker_id"],
            "endpoint_id": "endpoint-1",
            "desired_revision_id": "rev-1",
            "is_live": True,
        }
        assert registration is not None
        assert registration["assigned_endpoint_id"] == "endpoint-1"
        assert registration["endpoint_id"] == "endpoint-1"

    asyncio.run(scenario())
