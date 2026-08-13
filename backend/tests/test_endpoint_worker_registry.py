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
                    "task_id": params[3],
                    "last_seen_at": params[4],
                    "heartbeat_expires_at": params[5],
                    "hostname": params[6],
                    "pid": params[7],
                    "runtime_metadata": json.loads(params[8]),
                    "last_error": params[9],
                    "updated_at": params[4],
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
        if normalized.startswith("delete from endpoint_worker_registrations"):
            count = len(self.state["workers"])
            self.state["workers"].clear()
            return f"DELETE {count}"
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

    async def close(self):
        return None


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

    async def aclose(self):
        return None


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


def test_backend_startup_clears_endpoint_worker_registrations_before_runtime_reregistration():
    async def scenario() -> None:
        services = _make_services()
        registered_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
        await services.register_endpoint_worker(
            worker_id="endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="idle",
            now=registered_at,
        )
        await services.register_endpoint_worker(
            worker_id="endpoint-worker-2",
            runtime_instance_id="runtime-2",
            status="running",
            now=registered_at + timedelta(seconds=1),
        )

        cleared = await services.clear_endpoint_worker_registrations()

        assert cleared == 2
        assert await services.list_endpoint_worker_registrations(now=registered_at + timedelta(seconds=2)) == []

    asyncio.run(scenario())


def test_connect_backend_clears_stale_endpoint_worker_registrations_on_backend_startup(monkeypatch):
    async def scenario() -> None:
        seeded_pool = _RegistryPool()
        seeded_pool.state["workers"]["stale-worker"] = {
            "worker_id": "stale-worker",
            "runtime_instance_id": "runtime-stale",
            "status": "stale",
            "assigned_endpoint_id": None,
            "task_id": None,
            "last_seen_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "heartbeat_expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "hostname": "host-stale",
            "pid": 1,
            "runtime_metadata": {"boot": "old"},
            "last_error": None,
            "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        }

        async def fake_create_pool(*args, **kwargs):
            del args, kwargs
            return seeded_pool

        class _HttpClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def aclose(self):
                return None

        monkeypatch.setattr("app.services.redis.Redis.from_url", lambda *args, **kwargs: _Redis())
        monkeypatch.setattr("app.services.asyncpg.create_pool", fake_create_pool)
        monkeypatch.setattr("app.services.httpx.AsyncClient", _HttpClient)

        services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
        await services.connect_backend()

        workers = await services.list_endpoint_worker_registrations(now=datetime(2099, 1, 1, tzinfo=timezone.utc))
        assert workers == []
        assert any("delete from endpoint_worker_registrations" in query.lower() for query in seeded_pool.conn.queries)

        await services.disconnect()

    asyncio.run(scenario())


def test_connect_does_not_clear_endpoint_worker_registrations_for_non_backend_startup(monkeypatch):
    async def scenario() -> None:
        seeded_pool = _RegistryPool()
        seeded_pool.state["workers"]["worker-1"] = {
            "worker_id": "worker-1",
            "runtime_instance_id": "runtime-1",
            "status": "idle",
            "assigned_endpoint_id": None,
            "task_id": None,
            "last_seen_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "heartbeat_expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "hostname": "host-1",
            "pid": 1,
            "runtime_metadata": {"boot": "current"},
            "last_error": None,
            "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        }
        seeded_pool.state["workers"]["worker-2"] = {
            "worker_id": "worker-2",
            "runtime_instance_id": "runtime-2",
            "status": "running",
            "assigned_endpoint_id": None,
            "task_id": "task-2",
            "last_seen_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "heartbeat_expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "hostname": "host-2",
            "pid": 2,
            "runtime_metadata": {"boot": "current"},
            "last_error": None,
            "created_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        }

        async def fake_create_pool(*args, **kwargs):
            del args, kwargs
            return seeded_pool

        class _HttpClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def aclose(self):
                return None

        monkeypatch.setattr("app.services.redis.Redis.from_url", lambda *args, **kwargs: _Redis())
        monkeypatch.setattr("app.services.asyncpg.create_pool", fake_create_pool)
        monkeypatch.setattr("app.services.httpx.AsyncClient", _HttpClient)

        services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
        await services.connect()

        workers = await services.list_endpoint_worker_registrations(now=datetime(2099, 1, 1, tzinfo=timezone.utc))
        assert [worker["worker_id"] for worker in workers] == ["worker-1", "worker-2"]
        assert not any("delete from endpoint_worker_registrations" in query.lower() for query in seeded_pool.conn.queries)

        await services.disconnect()

    asyncio.run(scenario())


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
        assert registered["heartbeat_expires_at"] == (registered_at + timedelta(minutes=5)).isoformat()

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

        nearly_stale_at = heartbeat_at + timedelta(minutes=5) - timedelta(seconds=1)
        stale_count = await services.mark_stale_endpoint_workers(now=nearly_stale_at)
        assert stale_count == 0

        workers = await services.list_endpoint_worker_registrations(now=nearly_stale_at)
        assert len(workers) == 1
        assert workers[0]["worker_id"] == "endpoint-worker-1"
        assert workers[0]["status"] == "running"
        assert workers[0]["raw_status"] == "running"
        assert workers[0]["is_live"] is True
        assert workers[0]["is_stale"] is False

        stale_at = heartbeat_at + timedelta(minutes=5)
        stale_count = await services.mark_stale_endpoint_workers(now=stale_at)
        assert stale_count == 1

        workers = await services.list_endpoint_worker_registrations(now=stale_at)
        assert len(workers) == 1
        assert workers[0]["worker_id"] == "endpoint-worker-1"
        assert workers[0]["status"] == "stale"
        assert workers[0]["raw_status"] == "stale"
        assert workers[0]["is_live"] is False
        assert workers[0]["is_stale"] is True

    asyncio.run(scenario())


def test_endpoint_worker_registry_marks_only_heartbeat_expiry_as_stale():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        await services.register_endpoint_worker(
            worker_id="endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="preparing",
            assigned_endpoint_id="endpoint-1",
            hostname="host-1",
            pid=101,
            runtime_metadata={"endpoint_id": "endpoint-1", "desired_revision_id": "rev-2", "warmed_revision_id": "rev-1"},
            now=now,
        )

        live_workers = await services.list_endpoint_worker_registrations(now=now + timedelta(minutes=4, seconds=59))
        assert live_workers[0]["status"] == "preparing"
        assert live_workers[0]["is_live"] is True
        assert live_workers[0]["deploy_state"] == "warming"
        assert live_workers[0]["state_label"] == "Preparing"

        stale_workers = await services.list_endpoint_worker_registrations(now=now + timedelta(minutes=5))
        assert stale_workers[0]["status"] == "stale"
        assert stale_workers[0]["is_live"] is False
        assert stale_workers[0]["deploy_state"] == "revision_mismatch"
        assert stale_workers[0]["state_summary"] == "Heartbeat expired. Assigned endpoint expects revision rev-2; worker was last warmed on rev-1."

    asyncio.run(scenario())


def test_endpoint_worker_heartbeat_preserves_revision_metadata_when_later_payloads_are_minimal():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        await services.register_endpoint_worker(
            worker_id="endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="listening",
            hostname="host-1",
            pid=101,
            runtime_metadata={
                "endpoint_id": "endpoint-1",
                "desired_revision_id": "rev-1",
                "warmed_revision_id": "rev-1",
                "platform": {"python_executable": "/venv/bin/python", "argv": ["endpoint-worker.py"]},
            },
            now=now,
        )
        await services._set_endpoint_worker_assignment("endpoint-worker-1", "endpoint-1")

        heartbeat = await services.heartbeat_endpoint_worker(
            "endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="listening",
            hostname="host-1",
            pid=101,
            runtime_metadata={"platform": {"argv": ["endpoint-worker.py", "--heartbeat"]}},
            now=now + timedelta(seconds=1),
        )
        payload = await services.list_endpoint_workers(now=now + timedelta(seconds=1))

        assert heartbeat is not None
        assert heartbeat["desired_revision_id"] == "rev-1"
        assert heartbeat["warmed_revision_id"] == "rev-1"
        assert heartbeat["runtime_metadata"]["platform"]["python_executable"] == "/venv/bin/python"
        assert heartbeat["runtime_metadata"]["platform"]["argv"] == ["endpoint-worker.py", "--heartbeat"]
        assert payload["ready_workers"] == 1
        assert payload["items"][0]["deploy_state"] == "ready"
        assert payload["items"][0]["desired_revision_id"] == "rev-1"
        assert payload["items"][0]["warmed_revision_id"] == "rev-1"

    asyncio.run(scenario())


def test_endpoint_worker_heartbeat_sql_uses_contiguous_parameters_without_assignment_hole():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        await services.register_endpoint_worker(
            worker_id="endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="idle",
            now=now,
        )

        heartbeat = await services.heartbeat_endpoint_worker(
            "endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="listening",
            assigned_endpoint_id="endpoint-1",
            runtime_metadata={"endpoint_id": "endpoint-1"},
            now=now + timedelta(seconds=1),
        )

        assert heartbeat is not None
        assert heartbeat["assigned_endpoint_id"] is None
        update_query = " ".join(
            next(
                query
                for query in reversed(services.postgres_pool.conn.queries)
                if "update endpoint_worker_registrations" in query.lower()
            ).split()
        )
        assert "task_id = $4" in update_query
        assert "updated_at = $5" in update_query
        assert "$10" in update_query
        assert "$11" not in update_query

    asyncio.run(scenario())


class _ReadinessRedis:
    def __init__(self, *, ping_result=True, ping_error: Exception | None = None):
        self.ping_result = ping_result
        self.ping_error = ping_error
        self.ping_awaited = False

    async def ping(self):
        self.ping_awaited = True
        if self.ping_error is not None:
            raise self.ping_error
        return self.ping_result


def test_readiness_awaits_redis_ping():
    async def scenario() -> None:
        services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
        services.redis = _ReadinessRedis(ping_result=True)

        readiness = await services.readiness()

        assert readiness.redis is True
        assert services.redis.ping_awaited is True

    asyncio.run(scenario())


def test_readiness_reports_redis_failure_when_ping_raises():
    async def scenario() -> None:
        services = AppServices(Settings(postgres_dsn="postgresql://postgres:postgres@localhost:5432/dspy_trainer"))
        services.redis = _ReadinessRedis(ping_error=RuntimeError("redis unavailable"))

        readiness = await services.readiness()

        assert readiness.redis is False
        assert services.redis.ping_awaited is True

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

        await services.mark_stale_endpoint_workers(now=now + timedelta(minutes=5, seconds=1))
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

        payload = await services.list_endpoint_workers(now=now + timedelta(minutes=5, seconds=1))

        assert payload["total_workers"] == 3
        assert payload["reported_workers"] == 3
        assert payload["available_workers"] == 0
        assert payload["busy_workers"] == 3
        assert payload["live_workers"] == 2
        assert payload["stale_workers"] == 1
        assert payload["assigned_workers"] == 2
        assert payload["unassigned_workers"] == 1
        assert payload["ready_workers"] == 0
        assert payload["warming_workers"] == 1
        assert payload["running_workers"] == 0
        assert payload["failed_workers"] == 0
        assert payload["summary"] == {
            "live_workers": 2,
            "stale_workers": 1,
            "assigned_workers": 2,
            "unassigned_workers": 1,
            "ready_workers": 0,
            "warming_workers": 1,
            "running_workers": 0,
            "failed_workers": 0,
        }
        assert [item["worker_id"] for item in payload["items"]] == [
            "endpoint-worker-1",
            "endpoint-worker-2",
            "endpoint-worker-3",
        ]
        assert payload["items"][0]["deploy_state"] == "revision_metadata_missing"
        assert payload["items"][0]["state_summary"] == "Listening for assigned endpoint traffic, but revision metadata has not been reported yet."
        assert payload["items"][-1]["status"] == "stale"
        assert payload["items"][-1]["assigned_endpoint_id"] is None

    asyncio.run(scenario())


def test_list_endpoint_workers_registry_summary_excludes_listening_revision_mismatch_from_ready_counts():
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
            runtime_metadata={"endpoint_id": "endpoint-1", "desired_revision_id": "rev-2", "warmed_revision_id": "rev-1"},
            now=now,
        )
        await services.register_endpoint_worker(
            worker_id="endpoint-worker-2",
            runtime_instance_id="runtime-2",
            status="idle",
            hostname="host-2",
            pid=102,
            now=now,
        )

        payload = await services.list_endpoint_workers(now=now)

        assert payload["total_workers"] == 2
        assert payload["reported_workers"] == 2
        assert payload["available_workers"] == 1
        assert payload["busy_workers"] == 1
        assert payload["ready_workers"] == 1
        assert payload["summary"]["ready_workers"] == 1
        assert payload["items"][0]["deploy_state"] == "revision_mismatch"
        assert payload["items"][0]["is_revision_ready"] is False
        assert payload["items"][1]["deploy_state"] == "unassigned"

    asyncio.run(scenario())


def test_list_endpoint_workers_registry_marks_assigned_listening_workers_without_revision_metadata():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        await services.register_endpoint_worker(
            worker_id="endpoint-worker-1",
            runtime_instance_id="runtime-1",
            status="listening",
            hostname="host-1",
            pid=101,
            runtime_metadata={"endpoint_id": "endpoint-1"},
            now=now,
        )
        await services._set_endpoint_worker_assignment("endpoint-worker-1", "endpoint-1")
        await services.register_endpoint_worker(
            worker_id="endpoint-worker-2",
            runtime_instance_id="runtime-2",
            status="idle",
            hostname="host-2",
            pid=102,
            now=now,
        )

        payload = await services.list_endpoint_workers(now=now)

        assert payload["available_workers"] == 1
        assert payload["busy_workers"] == 1
        assert payload["ready_workers"] == 1
        assert payload["items"][0]["deploy_state"] == "revision_metadata_missing"
        assert payload["items"][0]["state_summary"] == "Listening for assigned endpoint traffic, but revision metadata has not been reported yet."
        assert payload["items"][0]["is_revision_ready"] is False
        assert payload["items"][1]["deploy_state"] == "unassigned"

    asyncio.run(scenario())


def test_list_endpoint_workers_registry_parses_string_runtime_metadata_for_ready_workers():
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
            runtime_metadata={"endpoint_id": "endpoint-1", "desired_revision_id": "rev-1", "warmed_revision_id": "rev-1"},
            now=now,
        )
        services.postgres_pool.conn.state["workers"]["endpoint-worker-1"]["runtime_metadata"] = json.dumps(
            services.postgres_pool.conn.state["workers"]["endpoint-worker-1"]["runtime_metadata"]
        )

        payload = await services.list_endpoint_workers(now=now)

        assert payload["items"][0]["deploy_state"] == "ready"
        assert payload["items"][0]["desired_revision_id"] == "rev-1"
        assert payload["items"][0]["warmed_revision_id"] == "rev-1"
        assert payload["items"][0]["is_revision_ready"] is True
        assert payload["ready_workers"] == 1

    asyncio.run(scenario())


def test_reconcile_endpoint_worker_assignments_uses_registered_workers_without_static_ids():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1", "deployed_revision_id": "rev-1"}

        async def resolve_bundle_revision_execution_state(revision_id: str):
            return {"module_id": "mod-1", "bundle_revision_id": revision_id}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_bundle_revision_execution_state = resolve_bundle_revision_execution_state  # type: ignore[method-assign]

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

        assert workers[0]["worker_id"] == registered_1["worker_id"]
        assert workers[0]["assigned_endpoint_id"] is None
        assert workers[1]["worker_id"] == registered_2["worker_id"]
        assert workers[1]["assigned_endpoint_id"] == "endpoint-1"

    asyncio.run(scenario())


def test_reconcile_endpoint_worker_assignments_prioritizes_live_workers_over_newer_stale_workers():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1", "deployed_revision_id": "rev-1"}

        async def resolve_bundle_revision_execution_state(revision_id: str):
            return {"module_id": "mod-1", "bundle_revision_id": revision_id}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_bundle_revision_execution_state = resolve_bundle_revision_execution_state  # type: ignore[method-assign]

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

    asyncio.run(scenario())


def test_reconcile_endpoint_worker_assignments_preserves_ready_assigned_workers_over_newer_idle_workers():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1", "deployed_revision_id": "rev-1"}

        async def resolve_bundle_revision_execution_state(revision_id: str):
            return {"module_id": "mod-1", "bundle_revision_id": revision_id}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_bundle_revision_execution_state = resolve_bundle_revision_execution_state  # type: ignore[method-assign]

        ready_worker = await services.register_endpoint_worker(
            runtime_instance_id="runtime-1",
            status="listening",
            assigned_endpoint_id="endpoint-1",
            hostname="host-1",
            pid=101,
            runtime_metadata={"endpoint_id": "endpoint-1", "desired_revision_id": "rev-1", "warmed_revision_id": "rev-1"},
            now=now,
        )
        idle_worker = await services.register_endpoint_worker(
            runtime_instance_id="runtime-2",
            status="idle",
            hostname="host-2",
            pid=102,
            now=now + timedelta(seconds=1),
        )

        await services.reconcile_endpoint_worker_assignments()
        workers = await services.list_endpoint_worker_registrations(now=now + timedelta(seconds=1))

        assert next(item for item in workers if item["worker_id"] == ready_worker["worker_id"])["assigned_endpoint_id"] == "endpoint-1"
        assert next(item for item in workers if item["worker_id"] == idle_worker["worker_id"])["assigned_endpoint_id"] is None

    asyncio.run(scenario())


def test_endpoint_ready_for_invocation_survives_reconcile_with_newer_idle_worker():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1", "deployed_revision_id": "rev-1"}

        async def resolve_bundle_revision_execution_state(revision_id: str):
            return {"module_id": "mod-1", "bundle_revision_id": revision_id}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_bundle_revision_execution_state = resolve_bundle_revision_execution_state  # type: ignore[method-assign]

        await services.register_endpoint_worker(
            runtime_instance_id="runtime-1",
            status="listening",
            assigned_endpoint_id="endpoint-1",
            hostname="host-1",
            pid=101,
            runtime_metadata={"endpoint_id": "endpoint-1", "desired_revision_id": "rev-1", "warmed_revision_id": "rev-1"},
            now=now,
        )
        await services.register_endpoint_worker(
            runtime_instance_id="runtime-2",
            status="idle",
            hostname="host-2",
            pid=102,
            now=now + timedelta(seconds=1),
        )

        routing_state = await services.ensure_endpoint_ready_for_invocation("endpoint-1")

        assert routing_state == {
            "endpoint_id": "endpoint-1",
            "desired_revision_id": "rev-1",
            "assigned_workers": 1,
            "ready_workers": 1,
            "status_counts": {"listening": 1},
        }

    asyncio.run(scenario())


def test_registry_assignment_remains_control_plane_owned_across_worker_heartbeats():
    async def scenario() -> None:
        services = _make_services()
        now = datetime(2099, 1, 1, tzinfo=timezone.utc)

        async def list_all_bundle_endpoints():
            return [{"id": "endpoint-1", "pinned_worker_count": 1, "created_at": now.isoformat()}]

        async def get_bundle_endpoint(endpoint_id: str):
            return {"id": endpoint_id, "module_import_id": "mod-1", "deployed_revision_id": "rev-1"}

        async def resolve_bundle_revision_execution_state(revision_id: str):
            return {"module_id": "mod-1", "bundle_revision_id": revision_id}

        services.list_all_bundle_endpoints = list_all_bundle_endpoints  # type: ignore[method-assign]
        services.get_bundle_endpoint = get_bundle_endpoint  # type: ignore[method-assign]
        services.resolve_bundle_revision_execution_state = resolve_bundle_revision_execution_state  # type: ignore[method-assign]

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
