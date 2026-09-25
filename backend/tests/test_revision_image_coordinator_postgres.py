from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.revision_image_builder import RevisionImageBuildResult
from app.revision_image_coordinator import (
    PostgresRevisionImageBuildStore,
    RevisionImageBuildClaim,
    _bounded_utf8,
)

BASE_IMAGE_ID = f"sha256:{'a' * 64}"
IMAGE_ID = f"sha256:{'b' * 64}"
SOURCE_DIGEST = f"sha256:{'c' * 64}"


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _RecordingConnection:
    def __init__(
        self,
        *,
        execute_results=None,
        fetch_results=None,
        fetchrow_results=None,
        fetchval_results=None,
    ):
        self.calls = []
        self.execute_results = list(execute_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetchval_results = list(fetchval_results or [])

    @staticmethod
    def is_closed():
        return False

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        if self.execute_results:
            return self.execute_results.pop(0)
        return "UPDATE 0"

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.fetch_results.pop(0) if self.fetch_results else []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.fetchrow_results.pop(0) if self.fetchrow_results else None

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.fetchval_results.pop(0) if self.fetchval_results else None


def _store(connection, *, max_log_bytes=256):
    store = PostgresRevisionImageBuildStore(
        postgres_dsn="postgresql://unused",
        instance_id="instance-a",
        deployment_id="deployment-a",
        base_image_id=BASE_IMAGE_ID,
        image_repository="revision",
        platform_version="test",
        build_log_max_bytes=max_log_bytes,
        leader_timeout_seconds=1,
    )
    store._connection = connection
    return store


def _write_snapshot(root: Path) -> None:
    root.mkdir()
    metadata = (
        f'name = "fixture"{chr(10)}version = "1.0.0"{chr(10) * 2}'
        f'[runtime]{chr(10)}system_dependency_commands = []{chr(10)}'
    )
    (root / "bundle.toml").write_text(metadata, encoding="utf-8")
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")


def _revision_row(snapshot: Path, *, retry_of_build_id=None):
    return {
        "revision_id": "revision-a",
        "module_id": "module-a",
        "source_commit": "commit-a",
        "source_snapshot_path": str(snapshot),
        "source_content_digest": SOURCE_DIGEST,
        "retry_of_build_id": retry_of_build_id,
    }


def _claim():
    return RevisionImageBuildClaim(
        build_id="build-a",
        module_id="module-a",
        revision_id="revision-a",
        generation=1,
        source_commit="commit-a",
        source_snapshot_path=Path("/snapshot/revision-a"),
        source_content_digest=SOURCE_DIGEST,
        local_tag="revision:one",
        base_image_id=BASE_IMAGE_ID,
        attempt=2,
        claim_owner="instance-a",
    )


def _ready_result():
    return RevisionImageBuildResult(
        status="ready",
        local_tag="revision:one",
        build_digest=f"sha256:{'d' * 64}",
        dependency_digest=f"sha256:{'e' * 64}",
        image_id=IMAGE_ID,
        image_digest=None,
        labels={},
        build_log="completed\n",
        failure_reason=None,
    )


def _sql(call):
    return " ".join(call[1].lower().split())


def test_postgres_backfill_uses_exact_eligibility_and_inserts_immutable_queue_row(tmp_path):
    snapshot = tmp_path / "snapshot"
    _write_snapshot(snapshot)
    connection = _RecordingConnection(fetch_results=[[_revision_row(snapshot)]])
    store = _store(connection)

    inserted = asyncio.run(store.reconcile_eligible_revisions())

    assert inserted == 1
    fetch_call = next(call for call in connection.calls if call[0] == "fetch")
    candidate_sql = _sql(fetch_call)
    assert "m.deleted_at is null" in candidate_sql
    assert "m.sync_status = 'synced'" in candidate_sql
    assert "m.current_revision_id = r.id" in candidate_sql
    assert "rb.validation_status = 'passed'" in candidate_sql
    assert "rb.validation_revision_id = r.id" in candidate_sql
    assert "r.source_snapshot_path is not null" in candidate_sql
    assert "r.source_content_digest is not null" in candidate_sql
    assert "not exists ( select 1 from revision_image_builds b where b.revision_id = r.id )" in candidate_sql
    assert "for update of r" in candidate_sql

    execute_calls = [call for call in connection.calls if call[0] == "execute"]
    revoke_sql = _sql(execute_calls[1])
    assert "where b.status = 'queued' and not exists" in revoke_sql
    assert "r.source_snapshot_path = b.source_snapshot_path" in revoke_sql
    assert "r.source_content_digest = b.source_content_digest" in revoke_sql
    insert_sql = _sql(execute_calls[-1])
    assert "insert into revision_image_builds" in insert_sql
    assert "'queued', 0" in insert_sql
    assert execute_calls[-1][2][2] == 1
    assert execute_calls[-1][2][7] == BASE_IMAGE_ID


def test_postgres_claim_is_eligibility_fenced_and_serving_first_fifo():
    connection = _RecordingConnection(fetchrow_results=[None])
    store = _store(connection)

    assert asyncio.run(store.claim_next(claim_timeout_seconds=30)) is None

    claim_call = next(call for call in connection.calls if call[0] == "fetchrow")
    sql = _sql(claim_call)
    assert "m.deleted_at is null" in sql
    assert "m.sync_status = 'synced'" in sql
    assert "m.current_revision_id = b.revision_id" in sql
    assert "rb.validation_status = 'passed'" in sql
    assert "rb.validation_revision_id = b.revision_id" in sql
    assert "r.source_snapshot_path = b.source_snapshot_path" in sql
    assert "r.source_content_digest = b.source_content_digest" in sql
    assert "not exists ( select 1 from revision_image_builds active where active.status = 'building' )" in sql
    priority = sql.index("case when exists")
    available = sql.index("b.available_at asc", priority)
    queued = sql.index("b.queued_at asc", available)
    build_id = sql.index("b.id asc", queued)
    assert priority < available < queued < build_id
    assert "from bundle_endpoints endpoint" in sql
    assert "from endpoint_deployments deployment" in sql
    assert "for update of b skip locked" in sql


def test_postgres_expiry_requeues_claim_with_cap_safe_recovery_log():
    connection = _RecordingConnection(execute_results=["UPDATE 2"])
    store = _store(connection, max_log_bytes=1)

    assert asyncio.run(store.recover_expired_claims()) == 2

    call = connection.calls[0]
    sql = _sql(call)
    assert "set status = 'queued'" in sql
    assert "where status = 'building' and claim_expires_at <= now()" in sql
    assert len(call[2][0].encode("utf-8")) <= 1


def test_postgres_retry_locks_revision_and_creates_next_generation(tmp_path):
    snapshot = tmp_path / "snapshot"
    _write_snapshot(snapshot)
    row = _revision_row(snapshot)
    row["id"] = "build-old"
    connection = _RecordingConnection(
        fetchrow_results=[row],
        fetchval_results=[2],
    )
    store = _store(connection)

    build_id = asyncio.run(store.enqueue_retry("build-old"))

    retry_sql = _sql(next(call for call in connection.calls if call[0] == "fetchrow"))
    assert "m.current_revision_id = b.revision_id" in retry_sql
    assert "rb.validation_revision_id = b.revision_id" in retry_sql
    assert "for update of b, r" in retry_sql
    execute_calls = [call for call in connection.calls if call[0] == "execute"]
    assert "status in ('queued', 'building')" in _sql(execute_calls[0])
    assert "insert into revision_image_builds" in _sql(execute_calls[1])
    assert execute_calls[1][2][0] == build_id
    assert execute_calls[1][2][2] == 2
    assert execute_calls[1][2][-1] == "build-old"


def test_postgres_rebuild_all_locks_each_revision_and_retries_latest_build(tmp_path):
    snapshot = tmp_path / "snapshot"
    _write_snapshot(snapshot)
    row = _revision_row(snapshot, retry_of_build_id="build-latest")
    connection = _RecordingConnection(
        fetch_results=[[row]],
        fetchval_results=[3],
    )
    store = _store(connection)

    build_ids = asyncio.run(store.enqueue_rebuild_all())

    rebuild_sql = _sql(next(call for call in connection.calls if call[0] == "fetch"))
    assert "m.current_revision_id = r.id" in rebuild_sql
    assert "rb.validation_revision_id = r.id" in rebuild_sql
    assert "order by r.created_at asc, r.id asc" in rebuild_sql
    assert "for update of r" in rebuild_sql
    execute_calls = [call for call in connection.calls if call[0] == "execute"]
    assert "status in ('queued', 'building')" in _sql(execute_calls[0])
    assert "insert into revision_image_builds" in _sql(execute_calls[1])
    assert execute_calls[1][2][0] == build_ids[0]
    assert execute_calls[1][2][2] == 3
    assert execute_calls[1][2][-1] == "build-latest"


def test_postgres_completion_atomically_fails_revoked_ready_result_without_publication():
    connection = _RecordingConnection(fetchval_results=["failed"])
    store = _store(connection)

    published = asyncio.run(store.complete_claim(_claim(), _ready_result()))

    assert published is False
    completion_call = next(call for call in connection.calls if call[0] == "fetchval")
    sql = _sql(completion_call)
    assert "with completion_candidate as" in sql
    assert "b.claim_owner = $2 and b.attempt = $3" in sql
    assert "for update of b" in sql
    assert "m.deleted_at is null" in sql
    assert "m.sync_status = 'synced'" in sql
    assert "m.current_revision_id = b.revision_id" in sql
    assert "rb.validation_revision_id = b.revision_id" in sql
    assert "r.source_snapshot_path = b.source_snapshot_path" in sql
    assert "r.source_content_digest = b.source_content_digest" in sql
    assert "when $4 = 'ready' and not candidate.eligible then 'failed'" in sql
    assert "image_id = case when $4 = 'ready' and candidate.eligible then $5 else null end" in sql
    assert not [call for call in connection.calls if call[0] == "execute"]


def test_postgres_completion_publishes_and_supersedes_only_when_eligibility_fence_passes():
    connection = _RecordingConnection(fetchval_results=["ready"])
    store = _store(connection)

    published = asyncio.run(store.complete_claim(_claim(), _ready_result()))

    assert published is True
    execute_calls = [call for call in connection.calls if call[0] == "execute"]
    assert len(execute_calls) == 1
    assert "where revision_id = $1 and id <> $2 and status = 'ready'" in _sql(
        execute_calls[0]
    )


def test_coordinator_log_bound_never_exceeds_small_cap_with_marker():
    marker = b"[earlier build output truncated]\n"
    for max_bytes in range(len(marker) + 3):
        bounded = _bounded_utf8("π" * 100, max_bytes).encode("utf-8")
        assert len(bounded) <= max_bytes
        if max_bytes:
            assert bounded == marker[:max_bytes] or bounded.startswith(marker)
