from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.revision_image_builder import (
    BuildCancellation,
    RevisionImageBuildCancelled,
    RevisionImageBuildResult,
    RevisionImageBuildSpec,
    calculate_revision_image_build_identity,
)
from app.revision_images import MAX_FAILURE_REASON_CHARS

logger = logging.getLogger(__name__)
_BUILD_COORDINATOR_ADVISORY_LOCK = 0x44535059494D4742
_RECOVERY_LOG = "claim expired; requeued for recovery\n"
_SHUTDOWN_LOG = "deployer stopped; requeued for recovery\n"
_RETRY_LOG = "generation queued by operator retry\n"
_REBUILD_ALL_LOG = "generation queued by operator rebuild-all\n"


class RevisionImageEnqueueError(ValueError):
    def __init__(self, code: str, message: str, *, build_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.build_id = build_id


@dataclass(frozen=True)
class RevisionImageBuildClaim:
    build_id: str
    module_id: str
    revision_id: str
    generation: int
    source_commit: str
    source_snapshot_path: Path
    source_content_digest: str
    local_tag: str
    base_image_id: str
    attempt: int
    claim_owner: str

    def build_spec(
        self, *, owner: str, image_repository: str, platform_version: str
    ) -> RevisionImageBuildSpec:
        return RevisionImageBuildSpec(
            owner=owner,
            module_id=self.module_id,
            revision_id=self.revision_id,
            build_id=self.build_id,
            generation=self.generation,
            source_commit=self.source_commit,
            source_snapshot_path=self.source_snapshot_path,
            source_content_digest=self.source_content_digest,
            base_image_id=self.base_image_id,
            image_repository=image_repository,
            platform_version=platform_version,
        )


class RevisionImageBuildStore(Protocol):
    async def try_acquire_leadership(self) -> bool: ...

    async def release_leadership(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def recover_expired_claims(self) -> int: ...

    async def reconcile_eligible_revisions(self) -> int: ...

    async def claim_next(
        self, *, claim_timeout_seconds: float
    ) -> RevisionImageBuildClaim | None: ...

    async def renew_claim(
        self,
        claim: RevisionImageBuildClaim,
        *,
        claim_timeout_seconds: float,
    ) -> bool: ...

    async def claim_cancellation_reason(
        self, claim: RevisionImageBuildClaim
    ) -> str | None: ...

    async def append_claim_log(
        self, claim: RevisionImageBuildClaim, text: str
    ) -> bool: ...

    async def complete_claim(
        self,
        claim: RevisionImageBuildClaim,
        result: RevisionImageBuildResult,
    ) -> bool: ...

    async def fail_claim(
        self, claim: RevisionImageBuildClaim, reason: str, build_log: str = ""
    ) -> bool: ...

    async def release_claim(self, claim: RevisionImageBuildClaim) -> bool: ...

    async def enqueue_retry(self, build_id: str) -> str: ...

    async def enqueue_rebuild_all(self) -> list[str]: ...


class RevisionImageBuilderAdapter(Protocol):
    def build(
        self,
        spec: RevisionImageBuildSpec,
        cancellation: BuildCancellation | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> RevisionImageBuildResult: ...

    def cancel(self, cancellation: BuildCancellation) -> None: ...


class _PendingLog:
    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._data = bytearray()

    def append(self, value: str) -> None:
        encoded = str(value).encode("utf-8", errors="replace")
        with self._lock:
            self._data.extend(encoded)
            if len(self._data) > self._max_bytes:
                self._data = self._data[-self._max_bytes :]

    def drain(self) -> str:
        with self._lock:
            data = bytes(self._data)
            self._data.clear()
        return data.decode("utf-8", errors="ignore")


class RevisionImageBuildCoordinator:
    def __init__(
        self,
        *,
        store: RevisionImageBuildStore,
        builder: RevisionImageBuilderAdapter,
        deployment_id: str,
        image_repository: str,
        platform_version: str,
        claim_timeout_seconds: float,
        poll_interval_seconds: float,
        build_log_max_bytes: int,
    ) -> None:
        self._store = store
        self._builder = builder
        self._deployment_id = deployment_id
        self._image_repository = image_repository
        self._platform_version = platform_version
        self._claim_timeout_seconds = claim_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._build_log_max_bytes = build_log_max_bytes
        self._stopping = asyncio.Event()
        self._leader = False
        self._active_claim: RevisionImageBuildClaim | None = None
        self._active_cancellation: BuildCancellation | None = None

    @property
    def is_leader(self) -> bool:
        return self._leader

    @property
    def active_build_id(self) -> str | None:
        return self._active_claim.build_id if self._active_claim is not None else None

    def request_stop(self) -> None:
        self._stopping.set()
        if self._active_cancellation is not None:
            self._builder.cancel(self._active_cancellation)

    async def run(self) -> None:
        try:
            while not self._stopping.is_set():
                try:
                    did_work = await self.run_cycle()
                except asyncio.CancelledError:
                    self.request_stop()
                    raise
                except Exception:
                    logger.exception("revision image deployer cycle failed")
                    self._leader = False
                    await self._store.disconnect()
                    did_work = False
                if not did_work:
                    await self._wait_for_stop(self._poll_interval_seconds)
        finally:
            self.request_stop()
            if self._leader:
                with suppress(Exception):
                    await self._store.release_leadership()
            self._leader = False
            await self._store.disconnect()

    async def run_cycle(self) -> bool:
        if self._stopping.is_set():
            return False
        if not self._leader:
            self._leader = await self._store.try_acquire_leadership()
            if not self._leader:
                return False
            logger.info(
                "revision image deployer became leader deployment_id=%s",
                self._deployment_id,
            )

        recovered = await self._store.recover_expired_claims()
        enqueued = await self._store.reconcile_eligible_revisions()
        claim = await self._store.claim_next(
            claim_timeout_seconds=self._claim_timeout_seconds
        )
        if claim is None:
            return bool(recovered or enqueued)
        await self._execute_claim(claim)
        return True

    async def _execute_claim(self, claim: RevisionImageBuildClaim) -> None:
        cancellation = BuildCancellation()
        pending_log = _PendingLog(self._build_log_max_bytes)
        self._active_claim = claim
        self._active_cancellation = cancellation
        spec = claim.build_spec(
            owner=self._deployment_id,
            image_repository=self._image_repository,
            platform_version=self._platform_version,
        )
        build_task = asyncio.create_task(
            asyncio.to_thread(
                self._builder.build, spec, cancellation, pending_log.append
            )
        )
        cancellation_reason: str | None = None
        shutdown = False
        monitor_interval = min(
            self._poll_interval_seconds, self._claim_timeout_seconds / 3
        )
        try:
            while not build_task.done():
                done, _ = await asyncio.wait({build_task}, timeout=monitor_interval)
                chunk = pending_log.drain()
                if chunk:
                    await self._store.append_claim_log(claim, chunk)
                if done:
                    break
                if self._stopping.is_set():
                    shutdown = True
                    cancellation_reason = "deployer shutting down"
                else:
                    cancellation_reason = await self._store.claim_cancellation_reason(
                        claim
                    )
                    if cancellation_reason is None:
                        renewed = await self._store.renew_claim(
                            claim,
                            claim_timeout_seconds=self._claim_timeout_seconds,
                        )
                        if not renewed:
                            cancellation_reason = "claim ownership lost"
                if cancellation_reason is not None:
                    self._builder.cancel(cancellation)
                    break

            try:
                result = await build_task
            except RevisionImageBuildCancelled:
                result = None
            except Exception as exc:  # noqa: BLE001
                result = None
                cancellation_reason = cancellation_reason or f"builder crashed: {exc}"
            if self._stopping.is_set():
                shutdown = True
                cancellation_reason = "deployer shutting down"

            chunk = pending_log.drain()
            if chunk:
                await self._store.append_claim_log(claim, chunk)

            if shutdown:
                await self._store.release_claim(claim)
                return
            if cancellation_reason is not None:
                await self._store.fail_claim(claim, cancellation_reason)
                return
            if result is None:
                await self._store.fail_claim(claim, "builder exited without a result")
                return
            if result.local_tag is not None and result.local_tag != claim.local_tag:
                await self._store.fail_claim(
                    claim,
                    "builder returned an image tag that does not match the immutable claim",
                )
                return
            published = await self._store.complete_claim(claim, result)
            if not published:
                logger.warning(
                    "discarded stale revision image build result build_id=%s attempt=%s",
                    claim.build_id,
                    claim.attempt,
                )
        finally:
            if not build_task.done():
                self._builder.cancel(cancellation)
                with suppress(RevisionImageBuildCancelled, asyncio.CancelledError):
                    await build_task
            self._active_claim = None
            self._active_cancellation = None

    async def _wait_for_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except TimeoutError:
            return


class PostgresRevisionImageBuildStore:
    """Session-scoped leader lock and durable queue operations for the deployer."""

    def __init__(
        self,
        *,
        postgres_dsn: str,
        instance_id: str,
        deployment_id: str,
        base_image_id: str,
        image_repository: str,
        platform_version: str,
        build_log_max_bytes: int,
        leader_timeout_seconds: float,
    ) -> None:
        self._postgres_dsn = postgres_dsn
        self._instance_id = instance_id
        self._deployment_id = deployment_id
        self._base_image_id = base_image_id
        self._image_repository = image_repository
        self._platform_version = platform_version
        self._build_log_max_bytes = build_log_max_bytes
        self._leader_timeout_seconds = leader_timeout_seconds
        self._connection: Any | None = None
        self._leader = False

    async def _conn(self) -> Any:
        if self._connection is not None and self._connection.is_closed():
            self._connection = None
            if self._leader:
                self._leader = False
                raise ConnectionError(
                    "Postgres session closed while holding revision image coordinator leadership"
                )
        if self._connection is None:
            import asyncpg

            self._connection = await asyncpg.connect(
                self._postgres_dsn,
                command_timeout=self._leader_timeout_seconds,
                server_settings={
                    "application_name": f"dspy-trainer-deployer:{self._instance_id}"
                },
            )
            self._leader = False
        return self._connection

    async def try_acquire_leadership(self) -> bool:
        conn = await self._conn()
        acquired = bool(
            await conn.fetchval(
                "select pg_try_advisory_lock($1)", _BUILD_COORDINATOR_ADVISORY_LOCK
            )
        )
        self._leader = acquired
        return acquired

    async def release_leadership(self) -> None:
        if self._connection is None or self._connection.is_closed() or not self._leader:
            self._leader = False
            return
        await self._connection.fetchval(
            "select pg_advisory_unlock($1)", _BUILD_COORDINATOR_ADVISORY_LOCK
        )
        self._leader = False

    async def disconnect(self) -> None:
        if self._connection is not None and not self._connection.is_closed():
            await self._connection.close()
        self._connection = None
        self._leader = False

    async def recover_expired_claims(self) -> int:
        conn = await self._conn()
        result = await conn.execute(
            """
            update revision_image_builds
            set status = 'queued',
                available_at = now(),
                claim_owner = null,
                claim_expires_at = null,
                build_log = case
                  when octet_length(build_log) + octet_length($1) <= $2 then build_log || $1
                  else $1
                end,
                failure_reason = null,
                updated_at = now()
            where status = 'building' and claim_expires_at <= now()
            """,
            _bounded_utf8(_RECOVERY_LOG, self._build_log_max_bytes),
            self._build_log_max_bytes,
        )
        return _affected_rows(result)

    async def reconcile_eligible_revisions(self) -> int:
        conn = await self._conn()
        inserted = 0
        async with conn.transaction():
            await conn.execute("""
                with eligible as (
                  select r.id as revision_id, r.module_import_id
                  from bundle_revisions r
                  join module_imports m on m.id = r.module_import_id
                  join runtime_bundles rb on rb.module_import_id = m.id
                  where m.deleted_at is null
                    and m.sync_status = 'synced'
                    and m.current_revision_id = r.id
                    and rb.validation_status = 'passed'
                    and rb.validation_revision_id = r.id
                    and r.source_snapshot_path is not null
                    and r.source_content_digest is not null
                )
                update revision_image_builds b
                set status = 'failed',
                    claim_owner = null,
                    claim_expires_at = null,
                    failure_reason = 'superseded by a newer eligible revision',
                    finished_at = now(),
                    updated_at = now()
                from bundle_revisions br, eligible e
                where b.revision_id = br.id
                  and br.module_import_id = e.module_import_id
                  and b.revision_id <> e.revision_id
                  and b.status = 'queued'
                """)
            await conn.execute("""
                update revision_image_builds b
                set status = 'failed',
                    failure_reason = 'revision is no longer eligible for an image build',
                    finished_at = now(),
                    updated_at = now()
                where b.status = 'queued'
                  and not exists (
                    select 1
                    from bundle_revisions r
                    join module_imports m on m.id = r.module_import_id
                    join runtime_bundles rb on rb.module_import_id = m.id
                    where r.id = b.revision_id
                      and m.deleted_at is null
                      and m.sync_status = 'synced'
                      and m.current_revision_id = b.revision_id
                      and rb.validation_status = 'passed'
                      and rb.validation_revision_id = b.revision_id
                      and r.source_snapshot_path is not null
                      and r.source_content_digest is not null
                      and r.source_snapshot_path = b.source_snapshot_path
                      and r.source_content_digest = b.source_content_digest
                      and coalesce(r.commit_sha, 'unversioned') = coalesce(b.source_commit, 'unversioned')
                  )
                """)
            candidates = await conn.fetch("""
                select r.id as revision_id, r.module_import_id as module_id, r.commit_sha as source_commit,
                       r.source_snapshot_path, r.source_content_digest
                from bundle_revisions r
                join module_imports m on m.id = r.module_import_id
                join runtime_bundles rb on rb.module_import_id = m.id
                where m.deleted_at is null
                  and m.sync_status = 'synced'
                  and m.current_revision_id = r.id
                  and rb.validation_status = 'passed'
                  and rb.validation_revision_id = r.id
                  and r.source_snapshot_path is not null
                  and r.source_content_digest is not null
                  and not exists (
                    select 1 from revision_image_builds b where b.revision_id = r.id
                  )
                order by r.created_at asc, r.id asc
                for update of r
                """)
            for candidate in candidates:
                await self._insert_build(
                    conn, candidate, generation=1, retry_of_build_id=None
                )
                inserted += 1
        return inserted

    async def claim_next(
        self, *, claim_timeout_seconds: float
    ) -> RevisionImageBuildClaim | None:
        conn = await self._conn()
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                with candidate as (
                  select b.id
                  from revision_image_builds b
                  join bundle_revisions r on r.id = b.revision_id
                  join module_imports m on m.id = r.module_import_id
                  join runtime_bundles rb on rb.module_import_id = m.id
                  where b.status = 'queued'
                    and b.available_at <= now()
                    and m.deleted_at is null
                    and m.sync_status = 'synced'
                    and m.current_revision_id = b.revision_id
                    and rb.validation_status = 'passed'
                    and rb.validation_revision_id = b.revision_id
                    and r.source_snapshot_path is not null
                    and r.source_content_digest is not null
                    and r.source_snapshot_path = b.source_snapshot_path
                    and r.source_content_digest = b.source_content_digest
                    and coalesce(r.commit_sha, 'unversioned') = coalesce(b.source_commit, 'unversioned')
                    and not exists (
                      select 1 from revision_image_builds active where active.status = 'building'
                    )
                  order by
                    case when exists (
                      select 1
                      from bundle_endpoints endpoint
                      where endpoint.module_import_id = r.module_import_id
                    ) or exists (
                      select 1
                      from endpoint_deployments deployment
                      where deployment.active_revision_id = b.revision_id
                         or deployment.target_revision_id = b.revision_id
                    ) then 0 else 1 end asc,
                    b.available_at asc,
                    b.queued_at asc,
                    b.id asc
                  for update of b skip locked
                  limit 1
                )
                update revision_image_builds b
                set status = 'building',
                    attempt = b.attempt + 1,
                    claim_owner = $1,
                    claim_expires_at = now() + ($2 * interval '1 second'),
                    started_at = coalesce(b.started_at, now()),
                    failure_reason = null,
                    updated_at = now()
                from candidate
                where b.id = candidate.id
                returning b.id, b.revision_id, b.generation, b.source_commit,
                          b.source_snapshot_path, b.source_content_digest, b.local_tag,
                          b.base_image_id, b.attempt, b.claim_owner,
                          (select r.module_import_id from bundle_revisions r where r.id = b.revision_id) as module_id
                """,
                self._instance_id,
                claim_timeout_seconds,
            )
        return _claim_from_row(row) if row is not None else None

    async def renew_claim(
        self,
        claim: RevisionImageBuildClaim,
        *,
        claim_timeout_seconds: float,
    ) -> bool:
        conn = await self._conn()
        result = await conn.execute(
            """
            update revision_image_builds
            set claim_expires_at = now() + ($4 * interval '1 second'), updated_at = now()
            where id = $1 and status = 'building' and claim_owner = $2 and attempt = $3
            """,
            claim.build_id,
            claim.claim_owner,
            claim.attempt,
            claim_timeout_seconds,
        )
        return _affected_rows(result) == 1

    async def claim_cancellation_reason(
        self, claim: RevisionImageBuildClaim
    ) -> str | None:
        conn = await self._conn()
        row = await conn.fetchrow(
            """
            select b.status, b.claim_owner, b.attempt,
                   m.deleted_at,
                   m.sync_status,
                   m.current_revision_id,
                   rb.validation_status,
                   rb.validation_revision_id
            from revision_image_builds b
            join bundle_revisions r on r.id = b.revision_id
            join module_imports m on m.id = r.module_import_id
            left join runtime_bundles rb on rb.module_import_id = m.id
            where b.id = $1
            """,
            claim.build_id,
        )
        if row is None:
            return "claim was deleted"
        if (
            row["status"] != "building"
            or row["claim_owner"] != claim.claim_owner
            or row["attempt"] != claim.attempt
        ):
            return "claim ownership lost"
        if (
            row["deleted_at"] is not None
            or row["sync_status"] != "synced"
            or row["current_revision_id"] != claim.revision_id
            or row["validation_status"] != "passed"
            or row["validation_revision_id"] != claim.revision_id
        ):
            return "build superseded because the revision is no longer eligible"
        return None

    async def append_claim_log(self, claim: RevisionImageBuildClaim, text: str) -> bool:
        if not text:
            return True
        conn = await self._conn()
        async with conn.transaction():
            existing = await conn.fetchval(
                """
                select build_log from revision_image_builds
                where id = $1 and status = 'building' and claim_owner = $2 and attempt = $3
                for update
                """,
                claim.build_id,
                claim.claim_owner,
                claim.attempt,
            )
            if existing is None:
                return False
            bounded = _bounded_utf8(f"{existing}{text}", self._build_log_max_bytes)
            result = await conn.execute(
                """
                update revision_image_builds
                set build_log = $4, updated_at = now()
                where id = $1 and status = 'building' and claim_owner = $2 and attempt = $3
                """,
                claim.build_id,
                claim.claim_owner,
                claim.attempt,
                bounded,
            )
        return _affected_rows(result) == 1

    async def complete_claim(
        self,
        claim: RevisionImageBuildClaim,
        result: RevisionImageBuildResult,
    ) -> bool:
        if result.status not in {"ready", "failed"}:
            raise ValueError(f"builder returned unsupported status: {result.status}")
        conn = await self._conn()
        async with conn.transaction():
            final_status = await conn.fetchval(
                """
                with completion_candidate as (
                  select b.id,
                         exists (
                           select 1
                           from bundle_revisions r
                           join module_imports m on m.id = r.module_import_id
                           join runtime_bundles rb on rb.module_import_id = m.id
                           where r.id = b.revision_id
                             and m.deleted_at is null
                             and m.sync_status = 'synced'
                             and m.current_revision_id = b.revision_id
                             and rb.validation_status = 'passed'
                             and rb.validation_revision_id = b.revision_id
                             and r.source_snapshot_path is not null
                             and r.source_content_digest is not null
                             and r.source_snapshot_path = b.source_snapshot_path
                             and r.source_content_digest = b.source_content_digest
                             and coalesce(r.commit_sha, 'unversioned') = coalesce(b.source_commit, 'unversioned')
                         ) as eligible
                  from revision_image_builds b
                  where b.id = $1 and b.status = 'building'
                    and b.claim_owner = $2 and b.attempt = $3
                  for update of b
                )
                update revision_image_builds b
                set status = case
                      when $4 = 'ready' and not candidate.eligible then 'failed'
                      else $4
                    end,
                    image_id = case when $4 = 'ready' and candidate.eligible then $5 else null end,
                    image_digest = case when $4 = 'ready' and candidate.eligible then $6 else null end,
                    build_log = $7,
                    failure_reason = case
                      when $4 = 'ready' and not candidate.eligible
                        then 'revision eligibility was revoked before image publication'
                      else $8
                    end,
                    claim_owner = null,
                    claim_expires_at = null,
                    finished_at = now(),
                    updated_at = now()
                from completion_candidate candidate
                where b.id = candidate.id
                returning b.status
                """,
                claim.build_id,
                claim.claim_owner,
                claim.attempt,
                result.status,
                result.image_id,
                result.image_digest,
                _bounded_utf8(result.build_log, self._build_log_max_bytes),
                _bounded_reason(result.failure_reason),
            )
            if final_status is None:
                return False
            if final_status == "ready":
                await conn.execute(
                    """
                    update revision_image_builds
                    set status = 'superseded', updated_at = now()
                    where revision_id = $1 and id <> $2 and status = 'ready'
                    """,
                    claim.revision_id,
                    claim.build_id,
                )
        return final_status == result.status

    async def fail_claim(
        self, claim: RevisionImageBuildClaim, reason: str, build_log: str = ""
    ) -> bool:
        conn = await self._conn()
        result = await conn.execute(
            """
            update revision_image_builds
            set status = 'failed',
                build_log = case when $5 = '' then build_log else $5 end,
                failure_reason = $4,
                claim_owner = null,
                claim_expires_at = null,
                finished_at = now(),
                updated_at = now()
            where id = $1 and status = 'building' and claim_owner = $2 and attempt = $3
            """,
            claim.build_id,
            claim.claim_owner,
            claim.attempt,
            _bounded_reason(reason),
            _bounded_utf8(build_log, self._build_log_max_bytes),
        )
        return _affected_rows(result) == 1

    async def release_claim(self, claim: RevisionImageBuildClaim) -> bool:
        conn = await self._conn()
        result = await conn.execute(
            """
            update revision_image_builds
            set status = 'queued',
                available_at = now(),
                claim_owner = null,
                claim_expires_at = null,
                build_log = case
                  when octet_length(build_log) + octet_length($4) <= $5 then build_log || $4
                  else $4
                end,
                failure_reason = null,
                updated_at = now()
            where id = $1 and status = 'building' and claim_owner = $2 and attempt = $3
            """,
            claim.build_id,
            claim.claim_owner,
            claim.attempt,
            _bounded_utf8(_SHUTDOWN_LOG, self._build_log_max_bytes),
            self._build_log_max_bytes,
        )
        return _affected_rows(result) == 1

    async def enqueue_retry(self, build_id: str) -> str:
        conn = await self._conn()
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                select b.id, b.revision_id, r.module_import_id as module_id,
                       b.source_commit, b.source_snapshot_path, b.source_content_digest,
                       b.status,
                       (m.deleted_at is null
                        and m.sync_status = 'synced'
                        and m.current_revision_id = b.revision_id
                        and rb.validation_status = 'passed'
                        and rb.validation_revision_id = b.revision_id
                        and r.source_snapshot_path is not null
                        and r.source_content_digest is not null
                        and r.source_snapshot_path = b.source_snapshot_path
                        and r.source_content_digest = b.source_content_digest
                        and coalesce(r.commit_sha, 'unversioned') = coalesce(b.source_commit, 'unversioned')) as eligible
                from revision_image_builds b
                join bundle_revisions r on r.id = b.revision_id
                join module_imports m on m.id = r.module_import_id
                left join runtime_bundles rb on rb.module_import_id = m.id
                where b.id = $1
                for update of b, r
                """,
                build_id,
            )
            if row is None:
                raise RevisionImageEnqueueError(
                    "build_not_found", "revision image build was not found"
                )
            if row["status"] not in {"failed", "ready"}:
                raise RevisionImageEnqueueError(
                    "build_conflict", "only failed or ready builds can be retried"
                )
            if not row["eligible"]:
                raise RevisionImageEnqueueError(
                    "not_eligible", "build revision is not currently eligible"
                )
            duplicate = await conn.fetchval(
                """
                select id from revision_image_builds
                where retry_of_build_id = $1 and base_image_id = $2
                order by generation desc limit 1
                """,
                build_id,
                self._base_image_id,
            )
            if duplicate is not None:
                raise RevisionImageEnqueueError(
                    "build_conflict",
                    "a retry generation already exists for this build and base image",
                    build_id=str(duplicate),
                )
            await self._supersede_active_revision_build(
                conn, row["revision_id"], "superseded by manual retry"
            )
            generation = int(
                await conn.fetchval(
                    "select coalesce(max(generation), 0) + 1 from revision_image_builds where revision_id = $1",
                    row["revision_id"],
                )
            )
            return await self._insert_build(
                conn,
                row,
                generation=generation,
                retry_of_build_id=build_id,
                initial_log=_RETRY_LOG,
            )

    async def enqueue_rebuild_all(self) -> list[str]:
        conn = await self._conn()
        build_ids: list[str] = []
        async with conn.transaction():
            rows = await conn.fetch(
                """
                select r.id as revision_id, r.module_import_id as module_id, r.commit_sha as source_commit,
                       r.source_snapshot_path, r.source_content_digest,
                       (select b.id from revision_image_builds b where b.revision_id = r.id
                        order by b.generation desc limit 1) as retry_of_build_id,
                       (select b.id from revision_image_builds b where b.revision_id = r.id
                        and b.status in ('queued', 'building') order by b.generation desc limit 1) as active_build_id,
                       (select b.build_log from revision_image_builds b where b.revision_id = r.id
                        and b.status in ('queued', 'building') order by b.generation desc limit 1) as active_build_log
                from bundle_revisions r
                join module_imports m on m.id = r.module_import_id
                join runtime_bundles rb on rb.module_import_id = m.id
                where m.deleted_at is null
                  and m.sync_status = 'synced'
                  and m.current_revision_id = r.id
                  and rb.validation_status = 'passed'
                  and rb.validation_revision_id = r.id
                  and r.source_snapshot_path is not null
                  and r.source_content_digest is not null
                order by r.created_at asc, r.id asc
                for update of r
                """
            )
            duplicate = next(
                (
                    row["active_build_id"]
                    for row in rows
                    if row["active_build_log"] == _REBUILD_ALL_LOG
                ),
                None,
            )
            if duplicate is not None:
                raise RevisionImageEnqueueError(
                    "build_conflict",
                    "a rebuild-all generation is already queued for the current revisions",
                    build_id=str(duplicate),
                )
            for row in rows:
                await self._supersede_active_revision_build(
                    conn, row["revision_id"], "superseded by rebuild-all"
                )
                generation = int(
                    await conn.fetchval(
                        "select coalesce(max(generation), 0) + 1 from revision_image_builds where revision_id = $1",
                        row["revision_id"],
                    )
                )
                build_ids.append(
                    await self._insert_build(
                        conn,
                        row,
                        generation=generation,
                        retry_of_build_id=row["retry_of_build_id"],
                        initial_log=_REBUILD_ALL_LOG,
                    )
                )
        return build_ids

    async def _supersede_active_revision_build(
        self, conn: Any, revision_id: str, reason: str
    ) -> None:
        await conn.execute(
            """
            update revision_image_builds
            set status = 'failed',
                claim_owner = null,
                claim_expires_at = null,
                failure_reason = $2,
                finished_at = now(),
                updated_at = now()
            where revision_id = $1 and status in ('queued', 'building')
            """,
            revision_id,
            _bounded_reason(reason),
        )

    async def _insert_build(
        self,
        conn: Any,
        row: Any,
        *,
        generation: int,
        retry_of_build_id: str | None,
        initial_log: str = "",
    ) -> str:
        build_id = f"build-{uuid4().hex}"
        spec = RevisionImageBuildSpec(
            owner=self._deployment_id,
            module_id=str(row["module_id"]),
            revision_id=str(row["revision_id"]),
            build_id=build_id,
            generation=generation,
            source_commit=str(row["source_commit"] or "unversioned"),
            source_snapshot_path=Path(str(row["source_snapshot_path"])),
            source_content_digest=str(row["source_content_digest"]),
            base_image_id=self._base_image_id,
            image_repository=self._image_repository,
            platform_version=self._platform_version,
        )
        identity = calculate_revision_image_build_identity(spec)
        await conn.execute(
            """
            insert into revision_image_builds (
              id, revision_id, generation, source_commit, source_snapshot_path,
              source_content_digest, local_tag, base_image_id, status, attempt,
              available_at, build_log, failure_reason, retry_of_build_id,
              queued_at, created_at, updated_at
            ) values (
              $1, $2, $3, $4, $5, $6, $7, $8, 'queued', 0,
              now(), $10, null, $9, now(), now(), now()
            )
            """,
            build_id,
            spec.revision_id,
            generation,
            spec.source_commit,
            str(spec.source_snapshot_path),
            spec.source_content_digest,
            identity.local_tag,
            spec.base_image_id,
            retry_of_build_id,
            _bounded_utf8(initial_log, self._build_log_max_bytes),
        )
        return build_id

def _claim_from_row(row: Any) -> RevisionImageBuildClaim:
    return RevisionImageBuildClaim(
        build_id=str(row["id"]),
        module_id=str(row["module_id"]),
        revision_id=str(row["revision_id"]),
        generation=int(row["generation"]),
        source_commit=str(row["source_commit"] or "unversioned"),
        source_snapshot_path=Path(str(row["source_snapshot_path"])),
        source_content_digest=str(row["source_content_digest"]),
        local_tag=str(row["local_tag"]),
        base_image_id=str(row["base_image_id"]),
        attempt=int(row["attempt"]),
        claim_owner=str(row["claim_owner"]),
    )


def _affected_rows(command_status: str) -> int:
    try:
        return int(str(command_status).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


def _bounded_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = str(value or "").encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8")
    marker = b"[earlier build output truncated]\n"
    if max_bytes <= len(marker):
        return marker[:max_bytes].decode("ascii")
    tail = encoded[-(max_bytes - len(marker)) :]
    valid_tail = tail.decode("utf-8", errors="ignore").encode("utf-8")
    return (marker + valid_tail).decode("utf-8")


def _bounded_reason(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value)[:MAX_FAILURE_REASON_CHARS]
