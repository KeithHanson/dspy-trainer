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
                    "assigned_endpoint_id": params[3],
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
            assigned_endpoint_id="endpoint-1",
            hostname="host-1",
            pid=101,
            now=now,
        )
        await services.register_endpoint_worker(
            worker_id="endpoint-worker-2",
            runtime_instance_id="runtime-2",
            status="preparing",
            assigned_endpoint_id="endpoint-1",
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
        assert [item["worker_id"] for item in payload["items"]] == [
            "endpoint-worker-1",
            "endpoint-worker-2",
            "endpoint-worker-3",
        ]
        assert payload["items"][-1]["status"] == "stale"
        assert payload["items"][-1]["assigned_endpoint_id"] is None

    asyncio.run(scenario())
