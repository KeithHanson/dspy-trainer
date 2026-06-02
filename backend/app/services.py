from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import tomllib
import traceback
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import redis.asyncio as redis

from app.config import Settings


def _random_eval_name() -> str:
    adjectives = ["brisk", "bright", "calm", "clever", "daring", "eager", "gentle", "lucky", "steady", "swift"]
    colors = ["amber", "azure", "copper", "gold", "green", "indigo", "silver", "teal", "white", "yellow"]
    nouns = ["atlas", "comet", "falcon", "harbor", "meadow", "orbit", "river", "sunrise", "thunder", "voyager"]
    return f"{random.choice(adjectives)}-{random.choice(colors)}-{random.choice(nouns)}"


@dataclass
class ReadinessStatus:
    postgres: bool
    redis: bool
    mlflow: bool
    litellm: bool

    @property
    def ok(self) -> bool:
        return self.postgres and self.redis and self.mlflow and self.litellm


def _load_score_threshold(bundle_path: str | None) -> float:
    if not bundle_path:
        return 0.5
    toml_path = Path(bundle_path).expanduser().resolve() / "bundle.toml"
    if not toml_path.exists():
        return 0.5
    try:
        payload = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception:
        return 0.5
    threshold_value = payload.get("score_pass_threshold")
    if isinstance(threshold_value, bool) or not isinstance(threshold_value, (int, float)):
        return 0.5
    return min(1.0, max(0.0, float(threshold_value)))


def _normalize_optimization_strategy(strategy: str | None) -> str:
    raw = (strategy or "").strip().lower().replace("-", "_")
    aliases = {
        "": "bootstrap_fewshot",
        "bootstrapfewshot": "bootstrap_fewshot",
        "bootstrap_fewshot": "bootstrap_fewshot",
        "bootstrap": "bootstrap_fewshot",
        "gepa": "gepa",
        "mipro": "miprov2",
        "mipro_v2": "miprov2",
        "miprov2": "miprov2",
    }
    normalized = aliases.get(raw)
    if normalized is None:
        raise ValueError("strategy must be one of: bootstrap_fewshot, gepa, miprov2")
    return normalized


def _normalize_budget(value: Any, *, default: str = "medium") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
    else:
        normalized = str(value).strip().lower()
    if normalized in {"light", "medium", "heavy"}:
        return normalized
    raise ValueError("budget must be one of: light, medium, heavy")


class AppServices:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis: redis.Redis | None = None
        self.postgres_pool: asyncpg.Pool | None = None
        self.http_client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self.redis = redis.Redis.from_url(self.settings.redis_url, decode_responses=True)
        self.postgres_pool = await asyncpg.create_pool(dsn=self.settings.postgres_dsn, min_size=1, max_size=3)
        self.http_client = httpx.AsyncClient(timeout=5.0)
        await self.init_db()

    async def disconnect(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()
        if self.postgres_pool is not None:
            await self.postgres_pool.close()
        if self.redis is not None:
            await self.redis.aclose()

    async def readiness(self) -> ReadinessStatus:
        postgres_ok = False
        redis_ok = False
        mlflow_ok = False
        litellm_ok = False

        if self.postgres_pool is not None:
            try:
                async with self.postgres_pool.acquire() as conn:
                    await conn.execute("select 1")
                postgres_ok = True
            except Exception:
                postgres_ok = False

        if self.redis is not None:
            try:
                redis_ok = bool(self.redis.ping())
            except Exception:
                redis_ok = False

        if self.http_client is not None:
            try:
                response = await self.http_client.get(self.settings.mlflow_tracking_uri)
                mlflow_ok = response.status_code < 500
            except Exception:
                mlflow_ok = False

            try:
                headers = {}
                if self.settings.litellm_api_key.strip():
                    headers["Authorization"] = f"Bearer {self.settings.litellm_api_key}"
                response = await self.http_client.get(
                    f"{self.settings.litellm_base_url.rstrip('/')}/health/liveness",
                    headers=headers,
                )
                litellm_ok = response.status_code < 500
            except Exception:
                litellm_ok = False

        return ReadinessStatus(
            postgres=postgres_ok,
            redis=redis_ok,
            mlflow=mlflow_ok,
            litellm=litellm_ok,
        )

    async def list_workers(self) -> list[dict[str, Any]]:
        if self.redis is None:
            return []
        prefix = f"{self.settings.worker_registry_prefix}:"
        keys = await self.redis.keys(f"{prefix}*")
        workers: list[dict[str, Any]] = []
        for key in keys:
            raw = await self.redis.get(key)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            worker_id = payload.get("worker_id") or key.replace(prefix, "", 1)
            workers.append(
                {
                    "worker_id": str(worker_id),
                    "status": str(payload.get("status") or "unknown"),
                    "task_id": payload.get("task_id"),
                    "last_seen": payload.get("last_seen"),
                }
            )
        workers.sort(key=lambda item: item["worker_id"])
        return workers

    async def init_db(self) -> None:
        if self.postgres_pool is None:
            return
        async with self.postgres_pool.acquire() as conn:
            await conn.execute(
                """
                create table if not exists module_imports (
                  id text primary key,
                  source text not null,
                  source_ref text,
                  version_hash text,
                  bundle_name text,
                  bundle_version text,
                  status text not null,
                  deleted_at timestamptz,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table module_imports add column if not exists bundle_name text;")
            await conn.execute("alter table module_imports add column if not exists bundle_version text;")
            await conn.execute("alter table module_imports add column if not exists deleted_at timestamptz;")
            await conn.execute(
                """
                create table if not exists runtime_bundles (
                  module_import_id text primary key references module_imports(id) on delete cascade,
                  validation_status text not null,
                  smoke_status text not null,
                  diagnostics jsonb not null default '[]'::jsonb,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute(
                """
                create table if not exists eval_jobs (
                  id text primary key,
                  status text not null,
                  eval_name text not null,
                  project_id text not null,
                  module_import_id text not null references module_imports(id) on delete restrict,
                  scenario_id text not null,
                  dataset_version text not null,
                  bundle_path text not null,
                  repeat_count int not null default 1,
                  num_threads int not null default 1,
                  eval_inputs jsonb not null default '[]'::jsonb,
                  mlflow_experiment_id text,
                  mlflow_parent_run_id text,
                  failure_reason text,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table eval_jobs add column if not exists repeat_count int not null default 1;")
            await conn.execute("alter table eval_jobs add column if not exists num_threads int not null default 1;")
            await conn.execute("alter table eval_jobs add column if not exists eval_inputs jsonb not null default '[]'::jsonb;")
            await conn.execute("alter table eval_jobs add column if not exists eval_name text;")
            await conn.execute("update eval_jobs set eval_name = coalesce(eval_name, 'steady-bright-orbit') where eval_name is null;")
            await conn.execute("alter table eval_jobs alter column eval_name set not null;")
            await conn.execute("alter table eval_jobs add column if not exists bundle_path text;")
            await conn.execute("alter table eval_jobs add column if not exists failure_reason text;")
            await conn.execute("alter table eval_jobs add column if not exists evaluation_plan_id text;")
            await conn.execute(
                """
                create table if not exists eval_run_items (
                  id text primary key,
                  eval_job_id text not null references eval_jobs(id) on delete cascade,
                  status text not null,
                  project_id text not null,
                  module_import_id text not null,
                  scenario_id text not null,
                  dataset_version text not null,
                  mlflow_experiment_id text,
                  mlflow_parent_run_id text,
                  mlflow_item_run_id text,
                  mlflow_trace_id text,
                  repeat_index int,
                  item_index int,
                  score double precision,
                  input_payload jsonb,
                  prediction_payload jsonb,
                  label_payload jsonb,
                  rationale text,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table eval_run_items add column if not exists repeat_index int;")
            await conn.execute("alter table eval_run_items add column if not exists item_index int;")
            await conn.execute("alter table eval_run_items add column if not exists score double precision;")
            await conn.execute("alter table eval_run_items add column if not exists input_payload jsonb;")
            await conn.execute("alter table eval_run_items add column if not exists prediction_payload jsonb;")
            await conn.execute("alter table eval_run_items add column if not exists label_payload jsonb;")
            await conn.execute("alter table eval_run_items add column if not exists rationale text;")
            await conn.execute("alter table eval_run_items add column if not exists mlflow_item_run_id text;")
            await conn.execute("alter table eval_run_items add column if not exists mlflow_trace_id text;")
            await conn.execute(
                """
                create table if not exists optimization_datasets (
                  id text primary key,
                  project_id text not null,
                  module_import_id text not null references module_imports(id) on delete restrict,
                  name text not null,
                  dataset_kind text not null,
                  source_type text not null,
                  source_eval_job_ids jsonb not null default '[]'::jsonb,
                  source_filters jsonb not null default '{}'::jsonb,
                  records jsonb not null default '[]'::jsonb,
                  record_count int not null default 0,
                  input_keys jsonb not null default '[]'::jsonb,
                  label_keys jsonb not null default '[]'::jsonb,
                  optimizer_contract text not null default 'dspy_example_v1',
                  provenance_summary jsonb not null default '{}'::jsonb,
                  notes text,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table optimization_datasets add column if not exists dataset_kind text;")
            await conn.execute("alter table optimization_datasets add column if not exists source_type text;")
            await conn.execute("alter table optimization_datasets add column if not exists source_eval_job_ids jsonb not null default '[]'::jsonb;")
            await conn.execute("alter table optimization_datasets add column if not exists source_filters jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table optimization_datasets add column if not exists records jsonb not null default '[]'::jsonb;")
            await conn.execute("alter table optimization_datasets add column if not exists record_count int not null default 0;")
            await conn.execute("alter table optimization_datasets add column if not exists input_keys jsonb not null default '[]'::jsonb;")
            await conn.execute("alter table optimization_datasets add column if not exists label_keys jsonb not null default '[]'::jsonb;")
            await conn.execute("alter table optimization_datasets add column if not exists optimizer_contract text not null default 'dspy_example_v1';")
            await conn.execute("alter table optimization_datasets add column if not exists provenance_summary jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table optimization_datasets add column if not exists notes text;")
            await conn.execute(
                """
                create table if not exists optimization_jobs (
                  id text primary key,
                  status text not null,
                  project_id text not null,
                  module_import_id text not null references module_imports(id) on delete restrict,
                  bundle_path text not null,
                  strategy text not null default 'bootstrap_fewshot',
                  objective text not null default 'optimize_demo_quality',
                  dataset_id text references optimization_datasets(id) on delete set null,
                  validation_dataset_id text references optimization_datasets(id) on delete set null,
                  execution_lm_profile_id text,
                  helper_lm_profile_id text,
                  request_config jsonb not null default '{}'::jsonb,
                  normalized_config jsonb not null default '{}'::jsonb,
                  train_inputs jsonb not null default '[]'::jsonb,
                  val_inputs jsonb not null default '[]'::jsonb,
                  num_threads int not null default 1,
                  source_eval_job_id text,
                  artifact_path text,
                  artifact_metadata jsonb not null default '{}'::jsonb,
                  telemetry_summary jsonb not null default '{}'::jsonb,
                  comparison_summary jsonb not null default '{}'::jsonb,
                  failure_reason text,
                  run_started_at timestamptz,
                  finished_at timestamptz,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table optimization_jobs add column if not exists strategy text not null default 'bootstrap_fewshot';")
            await conn.execute("alter table optimization_jobs add column if not exists objective text not null default 'optimize_demo_quality';")
            await conn.execute("alter table optimization_jobs add column if not exists dataset_id text references optimization_datasets(id) on delete set null;")
            await conn.execute("alter table optimization_jobs add column if not exists validation_dataset_id text references optimization_datasets(id) on delete set null;")
            await conn.execute("alter table optimization_jobs add column if not exists execution_lm_profile_id text;")
            await conn.execute("alter table optimization_jobs add column if not exists helper_lm_profile_id text;")
            await conn.execute("alter table optimization_jobs add column if not exists request_config jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table optimization_jobs add column if not exists normalized_config jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table optimization_jobs add column if not exists artifact_metadata jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table optimization_jobs add column if not exists telemetry_summary jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table optimization_jobs add column if not exists comparison_summary jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table optimization_jobs add column if not exists run_started_at timestamptz;")
            await conn.execute("alter table optimization_jobs add column if not exists finished_at timestamptz;")
            await conn.execute(
                """
                create table if not exists lm_profiles (
                  id text primary key,
                  name text not null,
                  model text not null,
                  api_base text not null,
                  model_type text not null default 'responses',
                  default_params jsonb not null default '{}'::jsonb,
                  lm_class_path text,
                  archived_at timestamptz,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table lm_profiles add column if not exists model_type text not null default 'responses';")
            await conn.execute("alter table lm_profiles add column if not exists default_params jsonb not null default '{}'::jsonb;")
            await conn.execute("alter table lm_profiles add column if not exists lm_class_path text;")
            await conn.execute("alter table lm_profiles add column if not exists archived_at timestamptz;")
            await conn.execute("alter table lm_profiles add column if not exists virtual_key text;")
            await conn.execute(
                """
                create table if not exists evaluation_plans (
                  id text primary key,
                  project_id text not null,
                  scenario_id text not null,
                  dataset_version text not null,
                  name text not null default 'Untitled plan',
                  runs_per_question int not null default 1,
                  max_workers int not null default 1,
                  module_import_id text,
                  eval_inputs jsonb not null default '[]'::jsonb,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table evaluation_plans add column if not exists name text not null default 'Untitled plan';")
            await conn.execute("alter table evaluation_plans add column if not exists runs_per_question int not null default 1;")
            await conn.execute("alter table evaluation_plans add column if not exists max_workers int not null default 1;")
            await conn.execute("alter table evaluation_plans add column if not exists module_import_id text;")
            await conn.execute("alter table evaluation_plans add column if not exists lm_profile_id text references lm_profiles(id) on delete set null;")
            await conn.execute(
                """
                create table if not exists agent_run_plans (
                  id text primary key,
                  status text not null,
                  project_id text not null,
                  module_import_id text not null references module_imports(id) on delete restrict,
                  scenario_id text not null,
                  dataset_version text not null,
                  plan_name text not null default 'RunPlan',
                  lm_profile_id text references lm_profiles(id) on delete set null,
                  bundle_path text not null,
                  eval_inputs jsonb not null default '[]'::jsonb,
                  mlflow_experiment_id text,
                  mlflow_parent_run_id text,
                  runs_per_question int not null default 1,
                  max_workers int not null default 1,
                  total_tasks int not null default 0,
                  completed_tasks int not null default 0,
                  failed_tasks int not null default 0,
                  failure_reason text,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table agent_run_plans add column if not exists plan_name text not null default 'RunPlan';")
            await conn.execute("alter table agent_run_plans add column if not exists lm_profile_id text references lm_profiles(id) on delete set null;")
            await conn.execute("alter table agent_run_plans add column if not exists mlflow_experiment_id text;")
            await conn.execute("alter table agent_run_plans add column if not exists mlflow_parent_run_id text;")
            await conn.execute(
                """
                create table if not exists agent_run_tasks (
                  id text primary key,
                  plan_id text not null references agent_run_plans(id) on delete cascade,
                  status text not null,
                  question_index int not null,
                  attempt_index int not null,
                  input_payload jsonb not null,
                  label_payload jsonb not null,
                  prediction_payload jsonb,
                  score double precision,
                  eval_pass boolean,
                  rationale text,
                  error text,
                  worker_log text,
                  worker_id text,
                  created_at timestamptz not null,
                  updated_at timestamptz not null
                );
                """
            )
            await conn.execute("alter table agent_run_tasks add column if not exists worker_log text;")
            await conn.execute("alter table agent_run_tasks add column if not exists eval_pass boolean;")

    async def create_module_import(self, source: str, source_ref: str | None, version_hash: str | None) -> dict[str, Any]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        module_id = str(uuid4())
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            await conn.execute(
                """
                insert into module_imports (id, source, source_ref, version_hash, status, created_at, updated_at)
                values ($1, $2, $3, $4, $5, $6, $7)
                """,
                module_id,
                source,
                source_ref,
                version_hash,
                "imported",
                now,
                now,
            )
            await conn.execute(
                """
                insert into runtime_bundles (module_import_id, validation_status, smoke_status, diagnostics, updated_at)
                values ($1, $2, $3, $4::jsonb, $5)
                """,
                module_id,
                "pending",
                "pending",
                "[]",
                now,
            )
        return {"id": module_id, "status": "imported"}

    async def set_validation_status(self, module_id: str, status: str, diagnostics: list[dict[str, Any]]) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute(
                """
                update runtime_bundles
                set validation_status = $2, diagnostics = $3::jsonb, updated_at = $4
                where module_import_id = $1
                """,
                module_id,
                status,
                __import__("json").dumps(diagnostics),
                now,
            )
            await conn.execute(
                """
                update module_imports
                set status = $2, updated_at = $3
                where id = $1
                """,
                module_id,
                "validated" if status == "passed" else "validation_failed",
                now,
            )
        return result.endswith("1")

    async def set_smoke_status(self, module_id: str, status: str, diagnostics: list[dict[str, Any]]) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute(
                """
                update runtime_bundles
                set smoke_status = $2, diagnostics = $3::jsonb, updated_at = $4
                where module_import_id = $1
                """,
                module_id,
                status,
                __import__("json").dumps(diagnostics),
                now,
            )
            await conn.execute(
                """
                update module_imports
                set status = $2, updated_at = $3
                where id = $1
                """,
                module_id,
                (
                    "smoke_testing"
                    if status == "running"
                    else ("runnable" if status == "passed" else "smoke_failed")
                ),
                now,
            )
        return result.endswith("1")

    async def get_diagnostics(self, module_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select m.id, m.status, r.validation_status, r.smoke_status, r.diagnostics
                from module_imports m
                join runtime_bundles r on r.module_import_id = m.id
                where m.id = $1
                """,
                module_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "validation_status": row["validation_status"],
            "smoke_status": row["smoke_status"],
            "diagnostics": row["diagnostics"],
        }

    async def list_modules(self) -> list[dict[str, Any]]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select m.id, m.source, m.source_ref, m.version_hash, m.bundle_name, m.bundle_version, m.status, m.created_at,
                       r.validation_status, r.smoke_status, r.diagnostics
                from module_imports m
                join runtime_bundles r on r.module_import_id = m.id
                where m.deleted_at is null
                order by m.created_at desc
                """
            )
        return [
            {
                "id": row["id"],
                "source": row["source"],
                "source_ref": row["source_ref"],
                "version_hash": row["version_hash"],
                "bundle_name": row["bundle_name"],
                "bundle_version": row["bundle_version"],
                "status": row["status"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "validation_status": row["validation_status"],
                "smoke_status": row["smoke_status"],
                "diagnostics": row["diagnostics"],
            }
            for row in rows
        ]

    async def get_module(self, module_id: str) -> dict[str, Any] | None:
        modules = await self.list_modules()
        for item in modules:
            if item["id"] == module_id:
                return item
        return None

    async def get_module_files(self, module_id: str) -> dict[str, str] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select source_ref
                from module_imports
                where id = $1 and deleted_at is null
                """,
                module_id,
            )
        if row is None:
            return None
        source_ref = str(row["source_ref"] or "").strip()
        if not source_ref:
            return {}
        root = Path(source_ref)
        if not root.exists() or not root.is_dir():
            return {}
        files: dict[str, str] = {}
        for file_name in ("module.py", "metric.py", "bundle.toml"):
            file_path = root / file_name
            if not file_path.exists() or not file_path.is_file():
                continue
            try:
                files[file_name] = file_path.read_text(encoding="utf-8")
            except Exception:
                continue
        return files

    async def delete_module(self, module_id: str) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute(
                """
                update module_imports
                set deleted_at = now(),
                    updated_at = now(),
                    status = 'deleted'
                where id = $1 and deleted_at is null
                """,
                module_id,
            )
        return result.endswith("1")

    async def set_module_bundle_metadata(self, module_id: str, bundle_name: str | None, bundle_version: str | None) -> None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            await conn.execute(
                """
                update module_imports
                set bundle_name = coalesce($2, bundle_name),
                    bundle_version = coalesce($3, bundle_version),
                    updated_at = now()
                where id = $1
                """,
                module_id,
                bundle_name,
                bundle_version,
            )

    async def set_module_source_ref(self, module_id: str, source_ref: str) -> None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            await conn.execute(
                """
                update module_imports
                set source_ref = $2,
                    updated_at = now()
                where id = $1
                """,
                module_id,
                source_ref,
            )

    async def create_eval_job(
        self,
        project_id: str,
        module_import_id: str,
        scenario_id: str,
        dataset_version: str,
        bundle_path: str,
        repeat_count: int,
        num_threads: int,
        eval_inputs: list[dict[str, Any]],
        evaluation_plan_id: str | None,
        mlflow_experiment_id: str | None,
        mlflow_parent_run_id: str | None,
    ) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        eval_job_id = str(uuid4())
        eval_name = _random_eval_name()
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            module_exists = await conn.fetchval("select 1 from module_imports where id = $1", module_import_id)
            if module_exists is None:
                return None
            effective_eval_inputs = eval_inputs
            if evaluation_plan_id:
                plan_inputs = await conn.fetchval("select eval_inputs from evaluation_plans where id = $1", evaluation_plan_id)
                if plan_inputs is None:
                    return None
                effective_eval_inputs = self._json_list(plan_inputs)
            await conn.execute(
                """
                insert into eval_jobs (
                  id, status, eval_name, project_id, module_import_id, scenario_id,
                  dataset_version, bundle_path, repeat_count, num_threads, eval_inputs,
                  evaluation_plan_id, mlflow_experiment_id, mlflow_parent_run_id, created_at, updated_at
                )
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $14, $15, $16)
                """,
                eval_job_id,
                "queued",
                eval_name,
                project_id,
                module_import_id,
                scenario_id,
                dataset_version,
                bundle_path,
                max(1, repeat_count),
                max(1, num_threads),
                __import__("json").dumps(effective_eval_inputs),
                evaluation_plan_id,
                mlflow_experiment_id,
                mlflow_parent_run_id,
                now,
                now,
            )
        return await self.get_eval_job(eval_job_id)

    async def get_eval_job(self, eval_job_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select id, status, project_id, module_import_id, scenario_id,
                       eval_name,
                       dataset_version, bundle_path, repeat_count, num_threads, eval_inputs,
                       evaluation_plan_id, mlflow_experiment_id, mlflow_parent_run_id, failure_reason,
                       created_at, updated_at
                from eval_jobs
                where id = $1
                """,
                eval_job_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "eval_name": row["eval_name"],
            "project_id": row["project_id"],
            "module_import_id": row["module_import_id"],
            "scenario_id": row["scenario_id"],
            "dataset_version": row["dataset_version"],
            "bundle_path": row["bundle_path"],
            "repeat_count": row["repeat_count"],
            "num_threads": row["num_threads"],
            "eval_inputs": row["eval_inputs"],
            "evaluation_plan_id": row["evaluation_plan_id"],
            "mlflow_experiment_id": row["mlflow_experiment_id"],
            "mlflow_parent_run_id": row["mlflow_parent_run_id"],
            "failure_reason": row["failure_reason"],
            "eval_job_id": row["id"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def cancel_eval_job(self, eval_job_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                update eval_jobs
                set status = 'canceled', failure_reason = null, updated_at = $2
                where id = $1 and status in ('queued', 'running')
                returning id, status, project_id, module_import_id, scenario_id, dataset_version,
                          eval_name,
                          bundle_path, repeat_count, num_threads, eval_inputs,
                          evaluation_plan_id, mlflow_experiment_id, mlflow_parent_run_id, failure_reason,
                          created_at, updated_at
                """,
                eval_job_id,
                now,
            )
            if row is None:
                row = await conn.fetchrow(
                    """
                    select id, status, project_id, module_import_id, scenario_id, dataset_version,
                           eval_name,
                           bundle_path, repeat_count, num_threads, eval_inputs,
                           evaluation_plan_id, mlflow_experiment_id, mlflow_parent_run_id, failure_reason,
                           created_at, updated_at
                    from eval_jobs
                    where id = $1
                    """,
                    eval_job_id,
                )
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "eval_name": row["eval_name"],
            "project_id": row["project_id"],
            "module_import_id": row["module_import_id"],
            "scenario_id": row["scenario_id"],
            "dataset_version": row["dataset_version"],
            "bundle_path": row["bundle_path"],
            "repeat_count": row["repeat_count"],
            "num_threads": row["num_threads"],
            "eval_inputs": row["eval_inputs"],
            "evaluation_plan_id": row["evaluation_plan_id"],
            "mlflow_experiment_id": row["mlflow_experiment_id"],
            "mlflow_parent_run_id": row["mlflow_parent_run_id"],
            "failure_reason": row["failure_reason"],
            "eval_job_id": row["id"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def seed_eval_run_items(
        self,
        eval_job_id: str,
        count: int,
        initial_status: str = "queued",
    ) -> list[dict[str, Any]]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        created_items: list[dict[str, Any]] = []
        async with self.postgres_pool.acquire() as conn:
            job = await conn.fetchrow(
                """
                select id, project_id, module_import_id, scenario_id,
                       dataset_version, mlflow_experiment_id, mlflow_parent_run_id
                from eval_jobs
                where id = $1
                """,
                eval_job_id,
            )
            if job is None:
                raise ValueError("eval job not found")
            for _ in range(count):
                item_id = str(uuid4())
                await conn.execute(
                    """
                    insert into eval_run_items (
                      id, eval_job_id, status, project_id, module_import_id, scenario_id,
                      dataset_version, mlflow_experiment_id, mlflow_parent_run_id, mlflow_trace_id, created_at, updated_at
                    )
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, null, $10, $11)
                    """,
                    item_id,
                    eval_job_id,
                    initial_status,
                    job["project_id"],
                    job["module_import_id"],
                    job["scenario_id"],
                    job["dataset_version"],
                    job["mlflow_experiment_id"],
                    job["mlflow_parent_run_id"],
                    now,
                    now,
                )
                created_items.append(
                    {
                        "id": item_id,
                        "eval_run_item_id": item_id,
                        "eval_job_id": eval_job_id,
                        "status": initial_status,
                        "project_id": job["project_id"],
                        "module_import_id": job["module_import_id"],
                        "scenario_id": job["scenario_id"],
                        "dataset_version": job["dataset_version"],
                        "mlflow_experiment_id": job["mlflow_experiment_id"],
                        "mlflow_parent_run_id": job["mlflow_parent_run_id"],
                        "mlflow_trace_id": None,
                    }
                )
        return created_items

    async def list_eval_run_items(self, eval_job_id: str, limit: int, offset: int) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            job_exists = await conn.fetchval("select 1 from eval_jobs where id = $1", eval_job_id)
            if job_exists is None:
                return None
            rows = await conn.fetch(
                """
                select id, eval_job_id, status, project_id, module_import_id, scenario_id,
                       dataset_version, mlflow_experiment_id, mlflow_parent_run_id, mlflow_item_run_id, mlflow_trace_id,
                       repeat_index, item_index, score, input_payload, prediction_payload, label_payload, rationale,
                       created_at, updated_at
                from eval_run_items
                where eval_job_id = $1
                order by created_at asc, id asc
                limit $2 offset $3
                """,
                eval_job_id,
                limit,
                offset,
            )
            total = await conn.fetchval("select count(*) from eval_run_items where eval_job_id = $1", eval_job_id)
        items = [
            {
                "id": row["id"],
                "eval_run_item_id": row["id"],
                "eval_job_id": row["eval_job_id"],
                "status": row["status"],
                "project_id": row["project_id"],
                "module_import_id": row["module_import_id"],
                "scenario_id": row["scenario_id"],
                "dataset_version": row["dataset_version"],
                "mlflow_experiment_id": row["mlflow_experiment_id"],
                "mlflow_parent_run_id": row["mlflow_parent_run_id"],
                "mlflow_item_run_id": row["mlflow_item_run_id"],
                "mlflow_trace_id": row["mlflow_trace_id"],
                "repeat_index": row["repeat_index"],
                "item_index": row["item_index"],
                "score": row["score"],
                "input_payload": row["input_payload"],
                "prediction_payload": row["prediction_payload"],
                "label_payload": row["label_payload"],
                "rationale": row["rationale"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "count": len(items),
            "total": int(total),
        }

    async def set_eval_job_status(self, eval_job_id: str, status: str, failure_reason: str | None = None) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                update eval_jobs
                set status = $2, failure_reason = $3, updated_at = $4
                where id = $1
                returning id
                """,
                eval_job_id,
                status,
                failure_reason,
                now,
            )
        if row is None:
            return None
        return await self.get_eval_job(eval_job_id)

    async def create_eval_run_item(
        self,
        eval_job_id: str,
        status: str,
        repeat_index: int,
        item_index: int,
        score: float,
        input_payload: dict[str, Any],
        prediction_payload: dict[str, Any],
        label_payload: dict[str, Any],
        rationale: str | None,
        mlflow_item_run_id: str | None,
        mlflow_trace_id: str | None,
    ) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        item_id = str(uuid4())
        async with self.postgres_pool.acquire() as conn:
            job = await conn.fetchrow(
                """
                select project_id, module_import_id, scenario_id, dataset_version,
                       mlflow_experiment_id, mlflow_parent_run_id
                from eval_jobs where id = $1
                """,
                eval_job_id,
            )
            if job is None:
                return None
            await conn.execute(
                """
                    insert into eval_run_items (
                      id, eval_job_id, status, project_id, module_import_id, scenario_id,
                      dataset_version, mlflow_experiment_id, mlflow_parent_run_id, mlflow_item_run_id, mlflow_trace_id,
                      repeat_index, item_index, score, input_payload, prediction_payload, label_payload, rationale,
                      created_at, updated_at
                    )
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb, $16::jsonb, $17::jsonb, $18, $19, $20)
                """,
                item_id,
                eval_job_id,
                status,
                job["project_id"],
                job["module_import_id"],
                job["scenario_id"],
                job["dataset_version"],
                job["mlflow_experiment_id"],
                job["mlflow_parent_run_id"],
                mlflow_item_run_id,
                mlflow_trace_id,
                repeat_index,
                item_index,
                score,
                __import__("json").dumps(input_payload),
                __import__("json").dumps(prediction_payload),
                __import__("json").dumps(label_payload),
                rationale,
                now,
                now,
            )
        return {"id": item_id, "eval_run_item_id": item_id, "eval_job_id": eval_job_id, "status": status}

    async def set_eval_job_mlflow(self, eval_job_id: str, mlflow_experiment_id: str, mlflow_parent_run_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                update eval_jobs
                set mlflow_experiment_id = $2, mlflow_parent_run_id = $3, updated_at = $4
                where id = $1
                returning id
                """,
                eval_job_id,
                mlflow_experiment_id,
                mlflow_parent_run_id,
                now,
            )
        if row is None:
            return None
        return await self.get_eval_job(eval_job_id)

    async def set_eval_run_item_trace_id(self, eval_run_item_id: str, mlflow_trace_id: str) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute(
                """
                update eval_run_items
                set mlflow_trace_id = $2, updated_at = $3
                where id = $1
                """,
                eval_run_item_id,
                mlflow_trace_id,
                now,
            )
        return result.endswith("1")

    async def set_eval_run_item_mlflow_run_id(self, eval_run_item_id: str, mlflow_item_run_id: str) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute(
                """
                update eval_run_items
                set mlflow_item_run_id = $2, updated_at = $3
                where id = $1
                """,
                eval_run_item_id,
                mlflow_item_run_id,
                now,
            )
        return result.endswith("1")

    async def _mlflow_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.http_client is None:
            raise RuntimeError("http client not initialized")
        url = f"{self.settings.mlflow_tracking_uri.rstrip('/')}{path}"
        response = await self.http_client.request(method, url, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"MLflow {method} {path} failed ({response.status_code}): {response.text}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"MLflow {method} {path} returned invalid payload")
        return data

    async def _litellm_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.http_client is None:
            raise RuntimeError("http client not initialized")
        headers = {}
        if self.settings.litellm_api_key.strip():
            headers["Authorization"] = f"Bearer {self.settings.litellm_api_key}"
        url = f"{self.settings.litellm_base_url.rstrip('/')}{path}"
        response = await self.http_client.request(method, url, json=payload, params=query, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"LiteLLM {method} {path} failed ({response.status_code}): {response.text}")
        data = response.json()
        if isinstance(data, dict):
            return data
        return {"data": data}

    async def _litellm_openai_request(self, path: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
        if self.http_client is None:
            raise RuntimeError("http client not initialized")
        headers = {"Authorization": f"Bearer {api_key}"}
        url = f"{self.settings.litellm_base_url.rstrip('/')}{path}"
        response = await self.http_client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"LiteLLM POST {path} failed ({response.status_code}): {response.text}")
        data = response.json()
        if isinstance(data, dict):
            return data
        return {"data": data}

    async def list_litellm_keys(self) -> dict[str, Any]:
        try:
            return await self._litellm_request("GET", "/key/list")
        except Exception:
            return await self._litellm_request("GET", "/v1/key/list")

    async def create_litellm_key(
        self,
        models: list[str],
        aliases: dict[str, str],
        metadata: dict[str, Any],
        duration: str | None,
        key_alias: str | None,
        team_id: str | None,
        user_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "models": models,
            "aliases": aliases,
            "metadata": metadata,
        }
        if duration:
            payload["duration"] = duration
        if key_alias:
            payload["key_alias"] = key_alias
        if team_id:
            payload["team_id"] = team_id
        if user_id:
            payload["user_id"] = user_id
        return await self._litellm_request("POST", "/key/generate", payload=payload)

    async def get_litellm_key_info(self, key: str) -> dict[str, Any]:
        return await self._litellm_request("GET", "/key/info", query={"key": key})

    async def update_litellm_key(
        self,
        key: str,
        models: list[str] | None,
        aliases: dict[str, str] | None,
        metadata: dict[str, Any] | None,
        duration: str | None,
        max_budget: float | None,
        rpm_limit: int | None,
        tpm_limit: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": key}
        if models is not None:
            payload["models"] = models
        if aliases is not None:
            payload["aliases"] = aliases
        if metadata is not None:
            payload["metadata"] = metadata
        if duration is not None:
            payload["duration"] = duration
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if rpm_limit is not None:
            payload["rpm_limit"] = rpm_limit
        if tpm_limit is not None:
            payload["tpm_limit"] = tpm_limit
        return await self._litellm_request("POST", "/key/update", payload=payload)

    async def revoke_litellm_key(self, key: str) -> dict[str, Any]:
        return await self._litellm_request("POST", "/key/block", payload={"key": key})

    async def restore_litellm_key(self, key: str) -> dict[str, Any]:
        return await self._litellm_request("POST", "/key/unblock", payload={"key": key})

    async def ensure_mlflow_experiment(self, project_id: str, experiment_name: str | None = None) -> str:
        if not experiment_name:
            experiment_name = f"project:{project_id}"
        if self.http_client is None:
            raise RuntimeError("http client not initialized")
        url = (
            f"{self.settings.mlflow_tracking_uri.rstrip('/')}/api/2.0/mlflow/experiments/get-by-name"
            f"?experiment_name={experiment_name}"
        )
        response = await self.http_client.get(url)
        if response.status_code >= 500:
            raise RuntimeError(f"MLflow get-by-name failed ({response.status_code}): {response.text}")
        data = response.json() if response.status_code < 400 else {}
        experiment = data.get("experiment") if isinstance(data, dict) else None
        if isinstance(experiment, dict) and experiment.get("experiment_id"):
            return str(experiment["experiment_id"])
        create_data = await self._mlflow_request(
            "POST",
            "/api/2.0/mlflow/experiments/create",
            {"name": experiment_name},
        )
        experiment_id = create_data.get("experiment_id")
        if experiment_id is None:
            raise RuntimeError("MLflow experiment create missing experiment_id")
        return str(experiment_id)

    async def create_mlflow_run(
        self,
        experiment_id: str,
        run_name: str,
        tags: dict[str, str],
    ) -> str:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        payload = {
            "experiment_id": str(experiment_id),
            "run_name": run_name,
            "start_time": now_ms,
            "tags": [{"key": key, "value": value} for key, value in tags.items()],
        }
        data = await self._mlflow_request("POST", "/api/2.0/mlflow/runs/create", payload)
        run = data.get("run") if isinstance(data, dict) else None
        info = run.get("info") if isinstance(run, dict) else None
        run_id = info.get("run_id") if isinstance(info, dict) else None
        if run_id is None:
            raise RuntimeError("MLflow run create missing run_id")
        return str(run_id)

    async def set_mlflow_run_tag(self, run_id: str, key: str, value: str) -> None:
        await self._mlflow_request(
            "POST",
            "/api/2.0/mlflow/runs/set-tag",
            {"run_id": run_id, "key": key, "value": value},
        )

    async def finalize_mlflow_run(self, run_id: str, status: str = "FINISHED") -> None:
        await self._mlflow_request(
            "POST",
            "/api/2.0/mlflow/runs/update",
            {"run_id": run_id, "status": status},
        )

    def prepare_optimization_job_payload(
        self,
        *,
        strategy: str,
        objective: str,
        dataset_id: str | None,
        validation_dataset_id: str | None,
        execution_lm_profile_id: str | None,
        helper_lm_profile_id: str | None,
        request_config: dict[str, Any] | None,
        client_normalized_config: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_strategy = _normalize_optimization_strategy(strategy)
        clean_objective = objective.strip() or "optimize_demo_quality"
        clean_request_config = dict(request_config or {})
        execution_role = (execution_lm_profile_id or "").strip() or None
        helper_role = (helper_lm_profile_id or "").strip() or None
        if execution_role is None:
            raise ValueError("execution_lm_profile_id is required for optimization jobs")

        if normalized_strategy == "bootstrap_fewshot":
            dataset_kind = "demo"
            dspy_config = {
                "teacher_lm_profile_id": helper_role or execution_role,
                "max_bootstrapped_demos": max(1, int(clean_request_config.get("max_bootstrapped_demos", 4))),
                "max_labeled_demos": max(1, int(clean_request_config.get("max_labeled_demos", 16))),
            }
            optimizer_class = "BootstrapFewShot"
        elif normalized_strategy == "miprov2":
            budget = _normalize_budget(clean_request_config.get("budget"), default="medium")
            dataset_kind = "demo"
            dspy_config = {
                "task_model_lm_profile_id": execution_role,
                "prompt_model_lm_profile_id": helper_role or execution_role,
                "auto": budget,
                "max_bootstrapped_demos": max(1, int(clean_request_config.get("max_bootstrapped_demos", 4))),
                "max_labeled_demos": max(1, int(clean_request_config.get("max_labeled_demos", 16))),
            }
            optimizer_class = "MIPROv2"
        else:
            budget = _normalize_budget(clean_request_config.get("budget"), default="medium")
            dataset_kind = "feedback"
            dspy_config = {
                "reflection_lm_profile_id": helper_role or execution_role,
                "auto": budget,
                "track_stats": True,
            }
            optimizer_class = "GEPA"

        persisted_request_config = {
            **clean_request_config,
            "_audit": {
                "strategy": normalized_strategy,
                "objective": clean_objective,
                "dataset_id": dataset_id,
                "validation_dataset_id": validation_dataset_id,
                "execution_lm_profile_id": execution_role,
                "helper_lm_profile_id": helper_role,
                "client_normalized_config": dict(client_normalized_config or {}),
            },
        }
        normalized_config = {
            "strategy": normalized_strategy,
            "optimizer_family": normalized_strategy,
            "optimizer_class": optimizer_class,
            "objective": clean_objective,
            "compile_mode": "offline",
            "dataset_requirements": {
                "dataset_kind": dataset_kind,
                "dataset_id": dataset_id,
                "validation_dataset_id": validation_dataset_id,
            },
            "lm_roles": {
                "execution_lm_profile_id": execution_role,
                "helper_lm_profile_id": helper_role,
            },
            "dspy_config": dspy_config,
        }
        return persisted_request_config, normalized_config

    async def create_optimization_job(
        self,
        project_id: str,
        module_import_id: str,
        bundle_path: str,
        strategy: str,
        objective: str,
        dataset_id: str | None,
        validation_dataset_id: str | None,
        execution_lm_profile_id: str | None,
        helper_lm_profile_id: str | None,
        request_config: dict[str, Any],
        normalized_config: dict[str, Any],
        train_inputs: list[dict[str, Any]],
        val_inputs: list[dict[str, Any]],
        num_threads: int,
        source_eval_job_id: str | None,
    ) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        job_id = str(uuid4())
        async with self.postgres_pool.acquire() as conn:
            module_exists = await conn.fetchval("select 1 from module_imports where id = $1", module_import_id)
            if module_exists is None:
                return None
            if execution_lm_profile_id is not None:
                execution_profile_exists = await conn.fetchval(
                    "select 1 from lm_profiles where id = $1 and archived_at is null",
                    execution_lm_profile_id,
                )
                if execution_profile_exists is None:
                    return None
            if helper_lm_profile_id is not None:
                helper_profile_exists = await conn.fetchval(
                    "select 1 from lm_profiles where id = $1 and archived_at is null",
                    helper_lm_profile_id,
                )
                if helper_profile_exists is None:
                    return None
            if dataset_id is not None:
                dataset_exists = await conn.fetchval("select 1 from optimization_datasets where id = $1", dataset_id)
                if dataset_exists is None:
                    return None
            if validation_dataset_id is not None:
                val_dataset_exists = await conn.fetchval("select 1 from optimization_datasets where id = $1", validation_dataset_id)
                if val_dataset_exists is None:
                    return None
            await conn.execute(
                """
                insert into optimization_jobs (
                  id, status, project_id, module_import_id, bundle_path, strategy, objective, dataset_id,
                  validation_dataset_id, execution_lm_profile_id, helper_lm_profile_id, request_config,
                  normalized_config, train_inputs, val_inputs, num_threads, source_eval_job_id, artifact_path,
                  artifact_metadata, telemetry_summary, comparison_summary, failure_reason, run_started_at,
                  finished_at, created_at, updated_at
                )
                values (
                  $1, 'queued', $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb,
                  $13::jsonb, $14::jsonb, $15, $16, null, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
                  null, null, null, $17, $18
                )
                """,
                job_id,
                project_id,
                module_import_id,
                bundle_path,
                strategy.strip() or "bootstrap_fewshot",
                objective.strip() or "optimize_demo_quality",
                dataset_id,
                validation_dataset_id,
                execution_lm_profile_id,
                helper_lm_profile_id,
                json.dumps(request_config or {}),
                json.dumps(normalized_config or {}),
                __import__("json").dumps(train_inputs),
                __import__("json").dumps(val_inputs),
                max(1, num_threads),
                source_eval_job_id,
                now,
                now,
            )
        return await self.get_optimization_job(job_id)

    async def get_optimization_job(self, optimization_job_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select id, status, project_id, module_import_id, bundle_path, strategy, objective, dataset_id,
                       validation_dataset_id, execution_lm_profile_id, helper_lm_profile_id, request_config,
                       normalized_config, train_inputs, val_inputs, num_threads, source_eval_job_id,
                       artifact_path, artifact_metadata, telemetry_summary, comparison_summary,
                       failure_reason, run_started_at, finished_at, created_at, updated_at
                from optimization_jobs where id = $1
                """,
                optimization_job_id,
            )
        if row is None:
            return None
        request_config = self._json_dict(row["request_config"])
        normalized_config = self._json_dict(row["normalized_config"])
        train_inputs = self._json_list(row["train_inputs"])
        val_inputs = self._json_list(row["val_inputs"])
        return {
            "id": row["id"],
            "status": row["status"],
            "project_id": row["project_id"],
            "module_import_id": row["module_import_id"],
            "bundle_path": row["bundle_path"],
            "strategy": row["strategy"],
            "objective": row["objective"],
            "dataset_id": row["dataset_id"],
            "validation_dataset_id": row["validation_dataset_id"],
            "execution_lm_profile_id": row["execution_lm_profile_id"],
            "helper_lm_profile_id": row["helper_lm_profile_id"],
            "request_config": request_config,
            "normalized_config": normalized_config,
            "train_inputs": train_inputs,
            "val_inputs": val_inputs,
            "num_threads": row["num_threads"],
            "source_eval_job_id": row["source_eval_job_id"],
            "artifact_path": row["artifact_path"],
            "artifact_metadata": self._json_dict(row["artifact_metadata"]),
            "telemetry_summary": self._json_dict(row["telemetry_summary"]),
            "comparison_summary": self._json_dict(row["comparison_summary"]),
            "failure_reason": row["failure_reason"],
            "run_started_at": row["run_started_at"].isoformat() if row["run_started_at"] is not None else None,
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] is not None else None,
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def list_optimization_jobs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id from optimization_jobs
                order by created_at desc
                limit $1 offset $2
                """,
                limit,
                offset,
            )
        jobs: list[dict[str, Any]] = []
        for row in rows:
            job = await self.get_optimization_job(str(row["id"]))
            if job is not None:
                jobs.append(job)
        return jobs

    async def create_optimization_dataset(
        self,
        project_id: str,
        module_import_id: str,
        name: str,
        dataset_kind: str,
        source_type: str,
        source_eval_job_ids: list[str],
        source_filters: dict[str, Any],
        records: list[dict[str, Any]],
        input_keys: list[str],
        label_keys: list[str],
        optimizer_contract: str,
        provenance_summary: dict[str, Any],
        notes: str | None,
    ) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        dataset_id = str(uuid4())
        async with self.postgres_pool.acquire() as conn:
            module_exists = await conn.fetchval("select 1 from module_imports where id = $1", module_import_id)
            if module_exists is None:
                return None
            await conn.execute(
                """
                insert into optimization_datasets (
                  id, project_id, module_import_id, name, dataset_kind, source_type, source_eval_job_ids,
                  source_filters, records, record_count, input_keys, label_keys, optimizer_contract,
                  provenance_summary, notes, created_at, updated_at
                )
                values (
                  $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10, $11::jsonb, $12::jsonb,
                  $13, $14::jsonb, $15, $16, $17
                )
                """,
                dataset_id,
                project_id,
                module_import_id,
                name.strip() or "Unnamed optimization dataset",
                dataset_kind.strip(),
                source_type.strip(),
                json.dumps(source_eval_job_ids or []),
                json.dumps(source_filters or {}),
                json.dumps(records or []),
                len(records or []),
                json.dumps(input_keys or []),
                json.dumps(label_keys or []),
                optimizer_contract.strip() or "dspy_example_v1",
                json.dumps(provenance_summary or {}),
                notes,
                now,
                now,
            )
        return await self.get_optimization_dataset(dataset_id)

    async def _list_all_eval_run_items(self, eval_job_id: str) -> list[dict[str, Any]]:
        all_items: list[dict[str, Any]] = []
        offset = 0
        limit = 500
        while True:
            page = await self.list_eval_run_items(eval_job_id, limit=limit, offset=offset)
            if page is None:
                return []
            items = page.get("items") if isinstance(page, dict) else None
            if not isinstance(items, list) or not items:
                break
            all_items.extend(items)
            if len(items) < limit:
                break
            offset += limit
        return all_items

    @staticmethod
    def _derive_demo_record(item: dict[str, Any], score_threshold: float) -> tuple[dict[str, Any] | None, str | None]:
        input_payload = AppServices._json_dict(item.get("input_payload"))
        if not input_payload:
            return None, "missing_input_payload"

        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None, "missing_score"
        if float(score) < score_threshold:
            return None, "score_below_threshold"

        label_payload = AppServices._json_dict(item.get("label_payload"))
        prediction_payload = AppServices._json_dict(item.get("prediction_payload"))
        if label_payload:
            label = label_payload
            label_provenance = "label_payload"
        elif prediction_payload:
            label = prediction_payload
            label_provenance = "accepted_run_output"
        else:
            return None, "missing_demo_target"

        return {
            "input": input_payload,
            "label": label,
            "input_keys": sorted(input_payload.keys()),
            "label_keys": sorted(label.keys()),
            "source_eval_job_id": item.get("eval_job_id"),
            "source_eval_item_id": item.get("id") or item.get("eval_run_item_id"),
            "score": float(score),
            "rationale": str(item.get("rationale") or ""),
            "label_provenance": label_provenance,
            # Target contract for later execution is dspy.Example(...).with_inputs(...).
            "optimizer_contract": "dspy_example_v1",
        }, None

    @staticmethod
    def _derive_feedback_record(item: dict[str, Any], score_threshold: float) -> tuple[dict[str, Any] | None, str | None]:
        input_payload = AppServices._json_dict(item.get("input_payload"))
        if not input_payload:
            return None, "missing_input_payload"

        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None, "missing_score"

        label_payload = AppServices._json_dict(item.get("label_payload"))
        prediction_payload = AppServices._json_dict(item.get("prediction_payload"))
        return {
            "input": input_payload,
            "label": label_payload,
            "prediction": prediction_payload,
            "input_keys": sorted(input_payload.keys()),
            "label_keys": sorted(label_payload.keys()),
            "source_eval_job_id": item.get("eval_job_id"),
            "source_eval_item_id": item.get("id") or item.get("eval_run_item_id"),
            "score": float(score),
            "passed": bool(float(score) >= score_threshold),
            "rationale": str(item.get("rationale") or ""),
            # GEPA uses score + feedback/rationale across both passing and failing rows.
            "feedback": str(item.get("rationale") or ""),
            "optimizer_contract": "dspy_example_v1",
        }, None

    async def derive_optimization_dataset(
        self,
        project_id: str,
        module_import_id: str,
        name: str,
        dataset_kind: str,
        source_type: str,
        source_eval_job_ids: list[str],
        source_filters: dict[str, Any],
        notes: str | None = None,
        persist: bool = False,
    ) -> dict[str, Any] | None:
        if not source_eval_job_ids:
            return None

        jobs: list[dict[str, Any]] = []
        all_items: list[dict[str, Any]] = []
        for eval_job_id in source_eval_job_ids:
            job = await self.get_eval_job(eval_job_id)
            if job is None:
                return None
            if str(job.get("project_id")) != project_id or str(job.get("module_import_id")) != module_import_id:
                return None
            jobs.append(job)
            all_items.extend(await self._list_all_eval_run_items(eval_job_id))

        requested_threshold = source_filters.get("score_threshold") if isinstance(source_filters, dict) else None
        fallback_threshold = max((_load_score_threshold(str(job.get("bundle_path") or "")) for job in jobs), default=0.5)
        if isinstance(requested_threshold, bool) or not isinstance(requested_threshold, (int, float)):
            score_threshold = fallback_threshold
        else:
            score_threshold = min(1.0, max(0.0, float(requested_threshold)))

        records: list[dict[str, Any]] = []
        excluded_reasons: dict[str, int] = {}
        input_keys: set[str] = set()
        label_keys: set[str] = set()
        label_provenance_counts: dict[str, int] = {}
        passed_count = 0
        failed_count = 0

        derive_record = self._derive_demo_record if dataset_kind == "demo" else self._derive_feedback_record
        for item in all_items:
            record, reason = derive_record(item, score_threshold)
            if record is None:
                if reason is not None:
                    excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1
                continue
            records.append(record)
            input_keys.update(record.get("input_keys") or [])
            label_keys.update(record.get("label_keys") or [])
            provenance = str(record.get("label_provenance") or "")
            if provenance:
                label_provenance_counts[provenance] = label_provenance_counts.get(provenance, 0) + 1
            if bool(record.get("passed", float(record.get("score") or 0.0) >= score_threshold)):
                passed_count += 1
            else:
                failed_count += 1

        provenance_summary = {
            "included_records": len(records),
            "excluded_records": sum(excluded_reasons.values()),
            "excluded_reasons": excluded_reasons,
            "label_provenance_counts": label_provenance_counts,
            "score_threshold": score_threshold,
            "source_eval_job_count": len(jobs),
            "passing_records": passed_count,
            "failing_records": failed_count,
        }
        dataset_payload = {
            "project_id": project_id,
            "module_import_id": module_import_id,
            "name": name.strip() or "Derived optimization dataset",
            "dataset_kind": dataset_kind,
            "source_type": source_type,
            "source_eval_job_ids": source_eval_job_ids,
            "source_filters": {**(source_filters or {}), "score_threshold": score_threshold},
            "records": records,
            "record_count": len(records),
            "input_keys": sorted(input_keys),
            "label_keys": sorted(label_keys),
            "optimizer_contract": "dspy_example_v1",
            "provenance_summary": provenance_summary,
            "notes": notes,
            "preview": not persist,
        }

        if not persist:
            return dataset_payload

        return await self.create_optimization_dataset(
            project_id=project_id,
            module_import_id=module_import_id,
            name=dataset_payload["name"],
            dataset_kind=dataset_kind,
            source_type=source_type,
            source_eval_job_ids=source_eval_job_ids,
            source_filters=dataset_payload["source_filters"],
            records=records,
            input_keys=dataset_payload["input_keys"],
            label_keys=dataset_payload["label_keys"],
            optimizer_contract="dspy_example_v1",
            provenance_summary=provenance_summary,
            notes=notes,
        )

    async def get_optimization_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select id, project_id, module_import_id, name, dataset_kind, source_type, source_eval_job_ids,
                       source_filters, records, record_count, input_keys, label_keys, optimizer_contract,
                       provenance_summary, notes, created_at, updated_at
                from optimization_datasets
                where id = $1
                """,
                dataset_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "module_import_id": row["module_import_id"],
            "name": row["name"],
            "dataset_kind": row["dataset_kind"],
            "source_type": row["source_type"],
            "source_eval_job_ids": row["source_eval_job_ids"],
            "source_filters": row["source_filters"],
            "records": row["records"],
            "record_count": row["record_count"],
            "input_keys": row["input_keys"],
            "label_keys": row["label_keys"],
            "optimizer_contract": row["optimizer_contract"],
            "provenance_summary": row["provenance_summary"],
            "notes": row["notes"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def list_optimization_datasets(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id from optimization_datasets
                order by created_at desc
                limit $1 offset $2
                """,
                limit,
                offset,
            )
        datasets: list[dict[str, Any]] = []
        for row in rows:
            dataset = await self.get_optimization_dataset(str(row["id"]))
            if dataset is not None:
                datasets.append(dataset)
        return datasets

    async def cancel_optimization_job(self, optimization_job_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                update optimization_jobs set status='canceled', updated_at=$2
                where id=$1 and status in ('queued','running')
                returning id
                """,
                optimization_job_id,
                now,
            )
        if row is None:
            return await self.get_optimization_job(optimization_job_id)
        return await self.get_optimization_job(optimization_job_id)

    async def run_optimization_job(self, optimization_job_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        job = await self.get_optimization_job(optimization_job_id)
        if job is None:
            return None
        if job["status"] == "canceled":
            return job
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            await conn.execute(
                "update optimization_jobs set status='running', run_started_at=$2, updated_at=$2 where id=$1",
                optimization_job_id,
                now,
            )

        try:
            strategy = _normalize_optimization_strategy(str(job.get("strategy") or "bootstrap_fewshot"))
            if strategy in {"bootstrap_fewshot", "miprov2", "gepa"}:
                from app.executor.module_runner import run_bundle_optimization

                train_records: list[dict[str, Any]] = []
                if job.get("dataset_id"):
                    dataset = await self.get_optimization_dataset(str(job["dataset_id"]))
                    if dataset is None:
                        raise RuntimeError("optimization dataset not found")
                    train_records = self._json_list(dataset.get("records"))
                else:
                    train_records = self._json_list(job.get("train_inputs"))

                validation_inputs = self._json_list(job.get("val_inputs"))
                if not validation_inputs and job.get("validation_dataset_id"):
                    validation_dataset = await self.get_optimization_dataset(str(job["validation_dataset_id"]))
                    if validation_dataset is None:
                        raise RuntimeError("validation optimization dataset not found")
                    validation_inputs = [
                        {
                            "input": self._json_dict(record.get("input")),
                            "label": self._json_dict(record.get("label")),
                        }
                        for record in self._json_list(validation_dataset.get("records"))
                    ]

                execution_lm_profile = None
                if job.get("execution_lm_profile_id"):
                    execution_lm_profile = await self.get_lm_profile(str(job["execution_lm_profile_id"]))
                    if execution_lm_profile is None:
                        raise RuntimeError("execution lm profile not found")

                helper_lm_profile = None
                if job.get("helper_lm_profile_id"):
                    helper_lm_profile = await self.get_lm_profile(str(job["helper_lm_profile_id"]))
                    if helper_lm_profile is None:
                        raise RuntimeError("helper lm profile not found")

                artifact_root = Path("/tmp/dspy-trainer/optimization_artifacts") / optimization_job_id
                optimization_result = run_bundle_optimization(
                    bundle_path=str(job["bundle_path"]),
                    strategy=str(job["strategy"]),
                    train_records=train_records,
                    val_inputs=validation_inputs,
                    artifact_dir=str(artifact_root),
                    num_threads=int(job["num_threads"]),
                    execution_lm_profile=execution_lm_profile,
                    helper_lm_profile=helper_lm_profile,
                    dspy_config=self._json_dict(job.get("normalized_config")).get("dspy_config", {}),
                )
            else:
                from app.executor.module_runner import run_bundle_eval

                evaluation_result = run_bundle_eval(
                    bundle_path=job["bundle_path"],
                    eval_inputs=job["val_inputs"] or job["train_inputs"],
                    num_threads=job["num_threads"],
                )
                optimization_result = {
                    "artifact_path": f"optimization://{optimization_job_id}/score-{evaluation_result['score_pct']}",
                    "artifact_metadata": {
                        "artifact_type": "optimization_placeholder",
                        "evaluation_item_count": len(evaluation_result["items"]),
                    },
                    "telemetry_summary": {
                        "strategy": strategy,
                        "score_pass_threshold": evaluation_result.get("score_pass_threshold"),
                    },
                    "comparison_summary": {
                        "baseline_score_pct": evaluation_result["score_pct"],
                        "optimized_score_pct": evaluation_result["score_pct"],
                        "score_delta_pct": 0.0,
                        "baseline_item_count": len(evaluation_result["items"]),
                        "optimized_item_count": len(evaluation_result["items"]),
                    },
                }

            now2 = datetime.now(timezone.utc)
            async with self.postgres_pool.acquire() as conn:
                await conn.execute(
                    """
                    update optimization_jobs
                    set status='succeeded',
                        artifact_path=$2,
                        artifact_metadata=$3::jsonb,
                        telemetry_summary=$4::jsonb,
                        comparison_summary=$5::jsonb,
                        failure_reason=null,
                        finished_at=$6,
                        updated_at=$6
                    where id=$1
                    """,
                    optimization_job_id,
                    optimization_result["artifact_path"],
                    json.dumps(optimization_result.get("artifact_metadata") or {}),
                    json.dumps(optimization_result.get("telemetry_summary") or {}),
                    json.dumps(optimization_result.get("comparison_summary") or {}),
                    now2,
                )
        except Exception as exc:
            now3 = datetime.now(timezone.utc)
            async with self.postgres_pool.acquire() as conn:
                await conn.execute(
                    "update optimization_jobs set status='failed', failure_reason=$2, finished_at=$3, updated_at=$3 where id=$1",
                    optimization_job_id,
                    str(exc),
                    now3,
                )
        return await self.get_optimization_job(optimization_job_id)

    async def create_lm_profile(
        self,
        name: str,
        model: str,
        api_base: str,
        model_type: str,
        default_params: dict[str, Any],
        lm_class_path: str | None,
        upstream_api_key: str | None,
    ) -> dict[str, Any]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        profile_id = str(uuid4())
        clean_name = name.strip() or "Unnamed profile"
        clean_model = model.strip()
        clean_api_base = api_base.strip()
        clean_model_type = model_type.strip() or "responses"
        clean_lm_class_path = lm_class_path.strip() if isinstance(lm_class_path, str) and lm_class_path.strip() else None
        await self._provision_litellm_model(
            profile_ref=profile_id,
            profile_name=clean_name,
            model=clean_model,
            api_base=clean_api_base,
            model_type=clean_model_type,
            upstream_api_key=upstream_api_key,
        )
        virtual_key = await self._generate_lm_profile_virtual_key(profile_id=profile_id, model=clean_model)
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            await conn.execute(
                """
                insert into lm_profiles (
                  id, name, model, api_base, model_type, default_params, lm_class_path, virtual_key, archived_at, created_at, updated_at
                )
                values ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, null, $9, $10)
                """,
                profile_id,
                clean_name,
                clean_model,
                clean_api_base,
                clean_model_type,
                __import__("json").dumps(default_params if isinstance(default_params, dict) else {}),
                clean_lm_class_path,
                virtual_key,
                now,
                now,
            )
        result = await self.get_lm_profile(profile_id)
        if result is None:
            raise RuntimeError("failed to load lm profile")
        return result

    async def list_lm_profiles(self) -> list[dict[str, Any]]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id, name, model, api_base, model_type, default_params, lm_class_path, virtual_key, archived_at, created_at, updated_at
                from lm_profiles
                where archived_at is null
                order by created_at desc
                """
            )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "model": row["model"],
                "api_base": row["api_base"],
                "model_type": row["model_type"],
                "default_params": self._json_dict(row["default_params"]),
                "lm_class_path": row["lm_class_path"],
                "virtual_key": row["virtual_key"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]

    async def get_lm_profile(self, lm_profile_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select id, name, model, api_base, model_type, default_params, lm_class_path, virtual_key, archived_at, created_at, updated_at
                from lm_profiles
                where id = $1 and archived_at is null
                """,
                lm_profile_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "model": row["model"],
            "api_base": row["api_base"],
            "model_type": row["model_type"],
            "default_params": self._json_dict(row["default_params"]),
            "lm_class_path": row["lm_class_path"],
            "virtual_key": row["virtual_key"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def update_lm_profile(
        self,
        lm_profile_id: str,
        name: str | None,
        model: str | None,
        api_base: str | None,
        model_type: str | None,
        default_params: dict[str, Any] | None,
        lm_class_path: str | None,
        upstream_api_key: str | None,
    ) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            existing = await conn.fetchrow(
                """
                select id, name, model, api_base, model_type, default_params, lm_class_path
                from lm_profiles
                where id = $1 and archived_at is null
                """,
                lm_profile_id,
            )
            if existing is None:
                return None
            next_name = name.strip() if isinstance(name, str) and name.strip() else existing["name"]
            next_model = model.strip() if isinstance(model, str) and model.strip() else existing["model"]
            next_api_base = api_base.strip() if isinstance(api_base, str) and api_base.strip() else existing["api_base"]
            next_model_type = model_type.strip() if isinstance(model_type, str) and model_type.strip() else existing["model_type"]
            next_default_params = default_params if isinstance(default_params, dict) else self._json_dict(existing["default_params"])
            next_lm_class_path = lm_class_path.strip() if isinstance(lm_class_path, str) and lm_class_path.strip() else None
            model_changed = next_model != existing["model"]
            api_base_changed = next_api_base != existing["api_base"]
            await self._sync_litellm_model_update(
                profile_ref=lm_profile_id,
                profile_name=next_name,
                model=next_model,
                api_base=next_api_base,
                model_type=next_model_type,
                upstream_api_key=upstream_api_key,
                include_litellm_params=(model_changed or api_base_changed),
            )
            await conn.execute(
                """
                update lm_profiles
                set name = $2,
                    model = $3,
                    api_base = $4,
                    model_type = $5,
                    default_params = $6::jsonb,
                    lm_class_path = $7,
                    updated_at = $8
                where id = $1
                """,
                lm_profile_id,
                next_name,
                next_model,
                next_api_base,
                next_model_type,
                __import__("json").dumps(next_default_params),
                next_lm_class_path,
                now,
            )
        return await self.get_lm_profile(lm_profile_id)

    async def rotate_lm_profile_virtual_key(self, lm_profile_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            existing = await conn.fetchrow(
                """
                select id, name, model, api_base, model_type, virtual_key
                from lm_profiles
                where id = $1 and archived_at is null
                """,
                lm_profile_id,
            )
            if existing is None:
                return None
            await self._provision_litellm_model(
                profile_ref=lm_profile_id,
                profile_name=existing["name"],
                model=existing["model"],
                api_base=existing["api_base"],
                model_type=existing["model_type"],
                upstream_api_key=None,
            )
            prior_key = existing["virtual_key"]
            if isinstance(prior_key, str) and prior_key.strip():
                try:
                    await self.revoke_litellm_key(prior_key)
                except Exception:
                    # Continue rotation even if previous key is already invalid/missing in proxy.
                    pass
            new_key = await self._generate_lm_profile_virtual_key(profile_id=lm_profile_id, model=existing["model"])
            now = datetime.now(timezone.utc)
            await conn.execute(
                """
                update lm_profiles
                set virtual_key = $2,
                    updated_at = $3
                where id = $1
                """,
                lm_profile_id,
                new_key,
                now,
            )
        result = await self.get_lm_profile(lm_profile_id)
        return result

    async def test_lm_profile_connection(self, lm_profile_id: str) -> dict[str, Any] | None:
        profile = await self.get_lm_profile(lm_profile_id)
        if profile is None:
            return None
        virtual_key = profile.get("virtual_key")
        if not isinstance(virtual_key, str) or not virtual_key.strip():
            raise RuntimeError("lm profile has no virtual key")
        payload = {
            "model": f"lm-profile:{lm_profile_id}",
            "messages": [{"role": "user", "content": "Reply with: connection-ok"}],
            "temperature": 0,
            "max_tokens": 24,
        }
        try:
            result = await self._litellm_openai_request("/chat/completions", payload=payload, api_key=virtual_key)
        except Exception:
            result = await self._litellm_openai_request("/v1/chat/completions", payload=payload, api_key=virtual_key)
        text = ""
        try:
            choices = result.get("choices") if isinstance(result, dict) else None
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(message, dict):
                    text = str(message.get("content") or "")
        except Exception:
            text = ""
        return {
            "ok": True,
            "model": payload["model"],
            "reply": text,
            "raw": result,
        }

    async def _generate_lm_profile_virtual_key(self, profile_id: str, model: str) -> str:
        profile_model_name = f"lm-profile:{profile_id}"
        unique_alias = f"lm-profile:{profile_id}:{str(uuid4())[:8]}"
        payload = await self.create_litellm_key(
            models=[profile_model_name, model],
            aliases={"default": profile_model_name},
            metadata={"lm_profile_id": profile_id},
            duration=None,
            key_alias=unique_alias,
            team_id=None,
            user_id=None,
        )
        key = payload.get("key")
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError("LiteLLM key generation returned no key")
        return key

    async def _provision_litellm_model(
        self,
        profile_ref: str,
        profile_name: str,
        model: str,
        api_base: str,
        model_type: str,
        upstream_api_key: str | None,
    ) -> None:
        clean_key = upstream_api_key.strip() if isinstance(upstream_api_key, str) else ""
        if not clean_key:
            return
        payload = {
            "model_name": f"lm-profile:{profile_ref}",
            "litellm_params": {
                "model": model,
                "api_base": api_base,
                "api_key": clean_key,
            },
            "model_info": {
                "id": profile_ref,
                "mode": model_type,
                "metadata": {"lm_profile_name": profile_name},
            },
        }
        try:
            await self._litellm_request("POST", "/model/new", payload=payload)
        except Exception as exc:
            message = str(exc)
            if "Unique constraint failed" not in message and "Failed to add model to db" not in message:
                raise
            await self._litellm_request("PATCH", f"/model/{profile_ref}/update", payload=payload)

    async def _sync_litellm_model_update(
        self,
        profile_ref: str,
        profile_name: str,
        model: str,
        api_base: str,
        model_type: str,
        upstream_api_key: str | None,
        include_litellm_params: bool,
    ) -> None:
        payload: dict[str, Any] = {
            "model_name": f"lm-profile:{profile_ref}",
            "model_info": {
                "id": profile_ref,
                "mode": model_type,
                "metadata": {"lm_profile_name": profile_name},
            },
        }
        if include_litellm_params:
            clean_key = upstream_api_key.strip() if isinstance(upstream_api_key, str) else ""
            if not clean_key:
                raise RuntimeError("upstream_api_key is required when model or api_base changes")
            payload["litellm_params"] = {
                "model": model,
                "api_base": api_base,
                "api_key": clean_key,
            }
        await self._litellm_request("PATCH", f"/model/{profile_ref}/update", payload=payload)

    async def delete_lm_profile(self, lm_profile_id: str) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            existing = await conn.fetchrow(
                """
                select id, virtual_key
                from lm_profiles
                where id = $1 and archived_at is null
                """,
                lm_profile_id,
            )
        if existing is None:
            return False
        virtual_key = existing["virtual_key"]
        if isinstance(virtual_key, str) and virtual_key.strip():
            await self._litellm_request("POST", "/key/delete", payload={"keys": [virtual_key]})
        await self._litellm_request("POST", "/model/delete", payload={"id": lm_profile_id})
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute(
                """
                update lm_profiles
                set archived_at = $2,
                    updated_at = $2
                where id = $1 and archived_at is null
                """,
                lm_profile_id,
                now,
            )
        return str(result).startswith("UPDATE 1")

    async def create_agent_run_plan(
        self,
        project_id: str,
        module_import_id: str,
        scenario_id: str,
        dataset_version: str,
        bundle_path: str,
        eval_inputs: list[dict[str, Any]],
        evaluation_plan_id: str | None,
        lm_profile_id: str | None,
        runs_per_question: int,
        max_workers: int,
    ) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        plan_id = str(uuid4())
        plan_name_value = "RunPlan"
        effective_lm_profile_id = lm_profile_id
        async with self.postgres_pool.acquire() as conn:
            module_exists = await conn.fetchval("select 1 from module_imports where id = $1", module_import_id)
            if module_exists is None:
                return None
            effective_eval_inputs = eval_inputs
            if evaluation_plan_id:
                plan_row = await conn.fetchrow(
                    "select name, eval_inputs, lm_profile_id from evaluation_plans where id = $1",
                    evaluation_plan_id,
                )
                if plan_row is None:
                    return None
                plan_name_value = str(plan_row["name"] or "RunPlan").strip() or "RunPlan"
                effective_eval_inputs = self._json_list(plan_row["eval_inputs"])
                if effective_lm_profile_id is None:
                    effective_lm_profile_id = plan_row["lm_profile_id"]
            if effective_lm_profile_id:
                profile_exists = await conn.fetchval("select 1 from lm_profiles where id = $1 and archived_at is null", effective_lm_profile_id)
                if profile_exists is None:
                    return None
            await conn.execute(
                """
                insert into agent_run_plans (
                  id, status, project_id, module_import_id, scenario_id, dataset_version,
                  plan_name, lm_profile_id, bundle_path, eval_inputs, mlflow_experiment_id, mlflow_parent_run_id, runs_per_question, max_workers,
                  total_tasks, completed_tasks, failed_tasks, failure_reason,
                  created_at, updated_at
                )
                values ($1, 'draft', $2, $3, $4, $5, $6, $7, $8, $9::jsonb, null, null, $10, $11, 0, 0, 0, null, $12, $13)
                """,
                plan_id,
                project_id,
                module_import_id,
                scenario_id,
                dataset_version,
                plan_name_value,
                effective_lm_profile_id,
                bundle_path,
                __import__("json").dumps(effective_eval_inputs),
                max(1, runs_per_question),
                max(1, max_workers),
                now,
                now,
            )
        return await self.get_agent_run_plan(plan_id)

    async def create_evaluation_plan(
        self,
        project_id: str,
        scenario_id: str,
        dataset_version: str,
        name: str,
        runs_per_question: int,
        max_workers: int,
        module_import_id: str | None,
        lm_profile_id: str | None,
        eval_inputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        plan_id = str(uuid4())
        async with self.postgres_pool.acquire() as conn:
            if lm_profile_id:
                profile_exists = await conn.fetchval("select 1 from lm_profiles where id = $1 and archived_at is null", lm_profile_id)
                if profile_exists is None:
                    raise ValueError("lm profile not found")
            await conn.execute(
                """
                insert into evaluation_plans (
                  id, project_id, scenario_id, dataset_version, name, runs_per_question, max_workers, module_import_id, lm_profile_id, eval_inputs, created_at, updated_at
                )
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)
                """,
                plan_id,
                project_id,
                scenario_id,
                dataset_version,
                name.strip() if name.strip() else "Untitled plan",
                max(1, runs_per_question),
                max(1, max_workers),
                module_import_id,
                lm_profile_id,
                __import__("json").dumps(eval_inputs),
                now,
                now,
            )
        result = await self.get_evaluation_plan(plan_id)
        if result is None:
            raise RuntimeError("failed to load evaluation plan")
        return result

    async def get_evaluation_plan(self, evaluation_plan_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select id, project_id, scenario_id, dataset_version, name, runs_per_question, max_workers, module_import_id, lm_profile_id, eval_inputs, created_at, updated_at
                from evaluation_plans
                where id = $1
                """,
                evaluation_plan_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "scenario_id": row["scenario_id"],
            "dataset_version": row["dataset_version"],
            "name": row["name"],
            "runs_per_question": row["runs_per_question"],
            "max_workers": row["max_workers"],
            "module_import_id": row["module_import_id"],
            "lm_profile_id": row["lm_profile_id"],
            "eval_inputs": self._json_list(row["eval_inputs"]),
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def list_evaluation_plans(self) -> list[dict[str, Any]]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id, project_id, scenario_id, dataset_version, name, runs_per_question, max_workers, module_import_id, lm_profile_id, eval_inputs, created_at, updated_at
                from evaluation_plans
                order by created_at desc
                """
            )
        return [
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "scenario_id": row["scenario_id"],
                "dataset_version": row["dataset_version"],
                "name": row["name"],
                "runs_per_question": row["runs_per_question"],
                "max_workers": row["max_workers"],
                "module_import_id": row["module_import_id"],
                "lm_profile_id": row["lm_profile_id"],
                "eval_inputs": self._json_list(row["eval_inputs"]),
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]

    async def delete_evaluation_plan(self, evaluation_plan_id: str) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute("delete from evaluation_plans where id = $1", evaluation_plan_id)
        return result.endswith("1")

    async def update_evaluation_plan(
        self,
        evaluation_plan_id: str,
        project_id: str,
        scenario_id: str,
        dataset_version: str,
        name: str,
        runs_per_question: int,
        max_workers: int,
        module_import_id: str | None,
        lm_profile_id: str | None,
        eval_inputs: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            exists = await conn.fetchval("select 1 from evaluation_plans where id = $1", evaluation_plan_id)
            if exists is None:
                return None
            if lm_profile_id:
                profile_exists = await conn.fetchval("select 1 from lm_profiles where id = $1 and archived_at is null", lm_profile_id)
                if profile_exists is None:
                    raise ValueError("lm profile not found")
            await conn.execute(
                """
                update evaluation_plans
                set project_id = $2,
                    scenario_id = $3,
                    dataset_version = $4,
                    name = $5,
                    runs_per_question = $6,
                    max_workers = $7,
                    module_import_id = $8,
                    lm_profile_id = $9,
                    eval_inputs = $10::jsonb,
                    updated_at = $11
                where id = $1
                """,
                evaluation_plan_id,
                project_id,
                scenario_id,
                dataset_version,
                name.strip() if name.strip() else "Untitled plan",
                max(1, runs_per_question),
                max(1, max_workers),
                module_import_id,
                lm_profile_id,
                __import__("json").dumps(eval_inputs),
                now,
            )
        return await self.get_evaluation_plan(evaluation_plan_id)

    @staticmethod
    def _json_list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return []
            return loaded if isinstance(loaded, list) else []
        return []

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return loaded if isinstance(loaded, dict) else {}
        return {}

    async def get_agent_run_plan(self, plan_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                select id, status, project_id, module_import_id, scenario_id, dataset_version,
                       plan_name, lm_profile_id, bundle_path, eval_inputs, mlflow_experiment_id, mlflow_parent_run_id, runs_per_question, max_workers,
                       (select count(*) from agent_run_tasks t where t.plan_id = agent_run_plans.id and t.status = 'running') as running_tasks,
                       total_tasks, completed_tasks, failed_tasks, failure_reason,
                       created_at, updated_at
                from agent_run_plans
                where id = $1
                """,
                plan_id,
            )
        if row is None:
            return None
        eval_inputs = self._json_list(row["eval_inputs"])
        return {
            "id": row["id"],
            "status": row["status"],
            "project_id": row["project_id"],
            "module_import_id": row["module_import_id"],
            "scenario_id": row["scenario_id"],
            "dataset_version": row["dataset_version"],
            "plan_name": row["plan_name"],
            "lm_profile_id": row["lm_profile_id"],
            "bundle_path": row["bundle_path"],
            "eval_inputs": eval_inputs,
            "mlflow_experiment_id": row["mlflow_experiment_id"],
            "mlflow_parent_run_id": row["mlflow_parent_run_id"],
            "runs_per_question": row["runs_per_question"],
            "max_workers": row["max_workers"],
            "total_tasks": row["total_tasks"],
            "completed_tasks": row["completed_tasks"],
            "failed_tasks": row["failed_tasks"],
            "failure_reason": row["failure_reason"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def list_agent_run_plans(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        async with self.postgres_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                select id, status, project_id, module_import_id, scenario_id, dataset_version,
                       plan_name, lm_profile_id, bundle_path, eval_inputs, mlflow_experiment_id, mlflow_parent_run_id, runs_per_question, max_workers,
                       (
                         select avg(t.score)
                         from agent_run_tasks t
                         where t.plan_id = agent_run_plans.id and t.score is not null
                       ) as average_score,
                       (
                         select count(*)
                         from agent_run_tasks t
                         where t.plan_id = agent_run_plans.id and t.eval_pass is true
                       ) as eval_pass_count,
                       (
                         select count(*)
                         from agent_run_tasks t
                         where t.plan_id = agent_run_plans.id and t.eval_pass is false
                       ) as eval_fail_count,
                       total_tasks, completed_tasks, failed_tasks, failure_reason,
                       created_at, updated_at
                from agent_run_plans
                order by created_at desc
                limit $1 offset $2
                """,
                safe_limit,
                safe_offset,
            )
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "project_id": row["project_id"],
                "module_import_id": row["module_import_id"],
                "scenario_id": row["scenario_id"],
                "dataset_version": row["dataset_version"],
                "plan_name": row["plan_name"],
                "lm_profile_id": row["lm_profile_id"],
                "bundle_path": row["bundle_path"],
                "eval_inputs": self._json_list(row["eval_inputs"]),
                "mlflow_experiment_id": row["mlflow_experiment_id"],
                "mlflow_parent_run_id": row["mlflow_parent_run_id"],
                "runs_per_question": row["runs_per_question"],
                "max_workers": row["max_workers"],
                "running_tasks": int((row["running_tasks"] if "running_tasks" in row else 0) or 0),
                "average_score": float(row["average_score"]) if row["average_score"] is not None else None,
                "eval_pass_count": int(row["eval_pass_count"] or 0),
                "eval_fail_count": int(row["eval_fail_count"] or 0),
                "total_tasks": row["total_tasks"],
                "completed_tasks": row["completed_tasks"],
                "failed_tasks": row["failed_tasks"],
                "failure_reason": row["failure_reason"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]

    async def delete_agent_run_plan(self, plan_id: str) -> bool:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            result = await conn.execute("delete from agent_run_plans where id = $1", plan_id)
        return str(result).startswith("DELETE 1")

    async def enqueue_agent_run_plan(self, plan_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        if self.redis is None:
            raise RuntimeError("queue not initialized")
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            plan = await conn.fetchrow(
                """
                select id, project_id, module_import_id, scenario_id, dataset_version, plan_name, eval_inputs, runs_per_question, max_workers,
                       mlflow_experiment_id, mlflow_parent_run_id
                from agent_run_plans where id = $1
                """,
                plan_id,
            )
            if plan is None:
                return None
            if not plan["mlflow_experiment_id"] or not plan["mlflow_parent_run_id"]:
                module_meta = await conn.fetchrow(
                    "select bundle_name, bundle_version from module_imports where id = $1",
                    str(plan["module_import_id"]),
                )
                bundle_name = str(module_meta["bundle_name"] or "").strip() if module_meta else ""
                bundle_version = str(module_meta["bundle_version"] or "").strip() if module_meta else ""
                experiment_name = f"{bundle_name}_v{bundle_version}" if bundle_name and bundle_version else f"project:{plan['project_id']}"
                experiment_id = await self.ensure_mlflow_experiment(str(plan["project_id"]), experiment_name=experiment_name)
                parent_tags = {
                    "type": "agent_run_plan",
                    "project_id": str(plan["project_id"]),
                    "plan_id": str(plan_id),
                    "scenario_id": str(plan["scenario_id"]),
                    "dataset_version": str(plan["dataset_version"]),
                }
                plan_name = str(plan["plan_name"] or "RunPlan").strip() or "RunPlan"
                parent_run_id = await self.create_mlflow_run(
                    experiment_id=str(experiment_id),
                    run_name=f"{plan_name}_{plan_id[:8]}",
                    tags=parent_tags,
                )
                await conn.execute(
                    """
                    update agent_run_plans
                    set mlflow_experiment_id = $2,
                        mlflow_parent_run_id = $3,
                        updated_at = $4
                    where id = $1
                    """,
                    plan_id,
                    str(experiment_id),
                    str(parent_run_id),
                    now,
                )
            existing = await conn.fetchval("select count(*) from agent_run_tasks where plan_id = $1", plan_id)
            if int(existing) == 0:
                eval_inputs = self._json_list(plan["eval_inputs"])
                for question_index, item in enumerate(eval_inputs):
                    for attempt_index in range(max(1, int(plan["runs_per_question"]))):
                        task_id = str(uuid4())
                        await conn.execute(
                            """
                            insert into agent_run_tasks (
                              id, plan_id, status, question_index, attempt_index,
                              input_payload, label_payload, prediction_payload, score, rationale, error, worker_id,
                              created_at, updated_at
                            )
                            values ($1, $2, 'pending', $3, $4, $5::jsonb, $6::jsonb, null, null, null, null, null, $7, $8)
                            """,
                            task_id,
                            plan_id,
                            question_index,
                            attempt_index,
                            __import__("json").dumps(item.get("input", {})),
                            __import__("json").dumps(item.get("label", {})),
                            now,
                            now,
                        )
            total_tasks = await conn.fetchval("select count(*) from agent_run_tasks where plan_id = $1", plan_id)
            queued_or_running = await conn.fetchval(
                "select count(*) from agent_run_tasks where plan_id = $1 and status in ('queued','running')",
                plan_id,
            )
            slots = max(0, int(plan["max_workers"]) - int(queued_or_running))
            tasks_to_queue = []
            if slots > 0:
                tasks_to_queue = await conn.fetch(
                    """
                    select id from agent_run_tasks
                    where plan_id = $1 and status = 'pending'
                    order by question_index asc, attempt_index asc
                    limit $2
                    """,
                    plan_id,
                    slots,
                )
                for row in tasks_to_queue:
                    await conn.execute(
                        "update agent_run_tasks set status='queued', updated_at=$2 where id=$1",
                        row["id"],
                        now,
                    )
            await conn.execute(
                """
                update agent_run_plans
                set status='queued', total_tasks=$2, updated_at=$3
                where id=$1
                """,
                plan_id,
                int(total_tasks),
                now,
            )
        for row in tasks_to_queue:
            await self.redis.execute_command(
                "LPUSH",
                self.settings.queue_name,
                __import__("json").dumps({"type": "agent_run_task", "task_id": row["id"]}),
            )
        return await self.get_agent_run_plan(plan_id)

    async def list_agent_run_tasks(self, plan_id: str, limit: int, offset: int) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        async with self.postgres_pool.acquire() as conn:
            plan_exists = await conn.fetchval("select 1 from agent_run_plans where id = $1", plan_id)
            if plan_exists is None:
                return None
            rows = await conn.fetch(
                """
                select id, plan_id, status, question_index, attempt_index,
                       input_payload, label_payload, prediction_payload,
                       score, eval_pass, rationale, error, worker_log, worker_id, created_at, updated_at
                from agent_run_tasks
                where plan_id = $1
                order by question_index asc, attempt_index asc, created_at asc
                limit $2 offset $3
                """,
                plan_id,
                limit,
                offset,
            )
            total = await conn.fetchval("select count(*) from agent_run_tasks where plan_id = $1", plan_id)
        return {
            "items": [
                {
                    "id": row["id"],
                    "plan_id": row["plan_id"],
                    "status": row["status"],
                    "question_index": row["question_index"],
                    "attempt_index": row["attempt_index"],
                    "input_payload": self._json_dict(row["input_payload"]),
                    "label_payload": self._json_dict(row["label_payload"]),
                    "prediction_payload": self._json_dict(row["prediction_payload"]) if row["prediction_payload"] is not None else None,
                    "score": row["score"],
                    "eval_pass": row["eval_pass"],
                    "rationale": row["rationale"],
                    "error": row["error"],
                    "worker_log": row["worker_log"],
                    "worker_id": row["worker_id"],
                    "created_at": row["created_at"].isoformat(),
                    "updated_at": row["updated_at"].isoformat(),
                }
                for row in rows
            ],
            "limit": limit,
            "offset": offset,
            "count": len(rows),
            "total": int(total),
        }

    async def run_agent_run_task(self, task_id: str, worker_id: str) -> dict[str, Any] | None:
        if self.postgres_pool is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(timezone.utc)
        log_lines = [f"worker={worker_id}", f"task={task_id}", "status=starting"]
        async with self.postgres_pool.acquire() as conn:
            task = await conn.fetchrow(
                """
                select t.id, t.plan_id, t.status, t.input_payload, t.label_payload,
                       p.bundle_path, p.max_workers, p.mlflow_experiment_id, p.mlflow_parent_run_id, p.project_id,
                       p.lm_profile_id,
                       lp.model as lm_model,
                       lp.api_base as lm_api_base,
                       lp.model_type as lm_model_type,
                       lp.default_params as lm_default_params,
                       lp.lm_class_path as lm_class_path,
                       lp.virtual_key as lm_virtual_key
                from agent_run_tasks t
                join agent_run_plans p on p.id = t.plan_id
                left join lm_profiles lp on lp.id = p.lm_profile_id and lp.archived_at is null
                where t.id = $1
                """,
                task_id,
            )
            if task is None:
                return None
            if task["status"] not in {"queued", "pending"}:
                return await conn.fetchrow("select id, status from agent_run_tasks where id = $1", task_id)
            running_count = await conn.fetchval(
                "select count(*) from agent_run_tasks where plan_id = $1 and status = 'running'",
                task["plan_id"],
            )
            if int(running_count) >= int(task["max_workers"]):
                return {"id": task_id, "status": "throttled"}
            log_lines.append("status=running")
            await conn.execute(
                "update agent_run_tasks set status='running', worker_id=$2, worker_log=$3, updated_at=$4 where id=$1",
                task_id,
                worker_id,
                "\n".join(log_lines),
                now,
            )
            await conn.execute(
                "update agent_run_plans set status='running', updated_at=$2 where id=$1 and status in ('queued','draft')",
                task["plan_id"],
                now,
            )
        try:
            eval_inputs = [
                {
                    "input": self._json_dict(task["input_payload"]),
                    "label": self._json_dict(task["label_payload"]),
                }
            ]
            parent_run_id = str(task["mlflow_parent_run_id"] or "")
            experiment_id = str(task["mlflow_experiment_id"] or "")
            tracking_uri = str(getattr(self.settings, "mlflow_tracking_uri", "") or "")
            trace_ids_before: set[str] = set()
            lm_profile: dict[str, Any] | None = None
            if task["lm_profile_id"]:
                lm_profile = {
                    "id": str(task["lm_profile_id"]),
                    "model": task["lm_model"],
                    "api_base": task["lm_api_base"],
                    "proxy_api_base": self.settings.litellm_base_url,
                    "model_type": task["lm_model_type"],
                    "default_params": self._json_dict(task["lm_default_params"]),
                    "lm_class_path": task["lm_class_path"],
                    "virtual_key": task["lm_virtual_key"],
                }
            if parent_run_id:
                from app.executor.eval import _configure_dspy_mlflow_autolog
                from app.executor.eval import _link_traces_to_parent_run
                from app.executor.eval import _recent_trace_ids
                from app.executor.eval import _run_bundle_eval_with_mlflow_parent

                if tracking_uri and experiment_id:
                    trace_ids_before = _recent_trace_ids(tracking_uri, experiment_id)
                _configure_dspy_mlflow_autolog(self, str(task["project_id"]))
                result = _run_bundle_eval_with_mlflow_parent(
                    bundle_path=str(task["bundle_path"]),
                    eval_inputs=eval_inputs,
                    num_threads=1,
                    parent_run_id=parent_run_id,
                    lm_profile=lm_profile,
                )
                if tracking_uri and experiment_id:
                    trace_ids_after = _recent_trace_ids(tracking_uri, experiment_id)
                    _link_traces_to_parent_run(
                        tracking_uri=tracking_uri,
                        parent_run_id=parent_run_id,
                        trace_ids=trace_ids_after - trace_ids_before,
                    )
            else:
                from app.executor.module_runner import run_bundle_eval

                result = run_bundle_eval(
                    bundle_path=str(task["bundle_path"]),
                    eval_inputs=eval_inputs,
                    num_threads=1,
                    lm_profile=lm_profile,
                )
            item = result["items"][0]
            score = float(item["score"])
            log_lines.append("status=succeeded")
            log_lines.append(f"score={score:.4f}")

            if parent_run_id and experiment_id and tracking_uri:
                try:
                    from app.executor.eval import _list_parent_run_traces
                    from app.executor.eval import _match_trace_id_for_item

                    parent_traces = _list_parent_run_traces(
                        tracking_uri=tracking_uri,
                        experiment_id=experiment_id,
                        parent_run_id=parent_run_id,
                    )
                    used_trace_ids: set[str] = set()
                    if trace_ids_before:
                        used_trace_ids = {str(tid) for tid in trace_ids_before}
                    _match_trace_id_for_item(
                        item_input=self._json_dict(task["input_payload"]),
                        traces=parent_traces,
                        used=used_trace_ids,
                    )
                except Exception:
                    pass

            async with self.postgres_pool.acquire() as conn:
                await conn.execute(
                    """
                    update agent_run_tasks
                    set status='succeeded', prediction_payload=$2::jsonb, score=$3, eval_pass=$4, rationale=$5, error=null, worker_log=$6, updated_at=$7
                    where id=$1
                    """,
                    task_id,
                    __import__("json").dumps(item["prediction"]),
                    score,
                    bool(item.get("passed", False)),
                    str(item["rationale"]),
                    "\n".join(log_lines),
                    datetime.now(timezone.utc),
                )
        except Exception as exc:
            log_lines.append("status=failed")
            log_lines.append("traceback:")
            log_lines.append(traceback.format_exc())
            async with self.postgres_pool.acquire() as conn:
                await conn.execute(
                    "update agent_run_tasks set status='failed', error=$2, worker_log=$3, updated_at=$4 where id=$1",
                    task_id,
                    str(exc),
                    "\n".join(log_lines),
                    datetime.now(timezone.utc),
                )

        await self._reconcile_agent_run_plan(str(task["plan_id"]))
        await self._queue_more_agent_run_tasks(str(task["plan_id"]))
        async with self.postgres_pool.acquire() as conn:
            return await conn.fetchrow("select id, status from agent_run_tasks where id = $1", task_id)

    async def _reconcile_agent_run_plan(self, plan_id: str) -> None:
        if self.postgres_pool is None:
            return
        now = datetime.now(timezone.utc)
        async with self.postgres_pool.acquire() as conn:
            total = int(await conn.fetchval("select count(*) from agent_run_tasks where plan_id = $1", plan_id) or 0)
            completed = int(await conn.fetchval("select count(*) from agent_run_tasks where plan_id = $1 and status = 'succeeded'", plan_id) or 0)
            failed = int(await conn.fetchval("select count(*) from agent_run_tasks where plan_id = $1 and status = 'failed'", plan_id) or 0)
            pending_or_active = int(
                await conn.fetchval(
                    "select count(*) from agent_run_tasks where plan_id = $1 and status in ('pending','queued','running')",
                    plan_id,
                )
                or 0
            )
            status = "running"
            if pending_or_active == 0 and total > 0:
                status = "failed" if failed > 0 else "succeeded"
            plan_row = await conn.fetchrow("select mlflow_parent_run_id, status from agent_run_plans where id = $1", plan_id)
            await conn.execute(
                """
                update agent_run_plans
                set status=$2, total_tasks=$3, completed_tasks=$4, failed_tasks=$5, updated_at=$6
                where id=$1
                """,
                plan_id,
                status,
                total,
                completed,
                failed,
                now,
            )
        if plan_row and plan_row["mlflow_parent_run_id"] and status in {"succeeded", "failed"}:
            mlflow_status = "FINISHED" if status == "succeeded" else "FAILED"
            try:
                await self.finalize_mlflow_run(str(plan_row["mlflow_parent_run_id"]), status=mlflow_status)
            except Exception:
                pass

    async def _queue_more_agent_run_tasks(self, plan_id: str) -> None:
        if self.postgres_pool is None or self.redis is None:
            return
        now = datetime.now(timezone.utc)
        tasks_to_queue: list[str] = []
        async with self.postgres_pool.acquire() as conn:
            plan = await conn.fetchrow("select max_workers from agent_run_plans where id = $1", plan_id)
            if plan is None:
                return
            active = int(
                await conn.fetchval(
                    "select count(*) from agent_run_tasks where plan_id = $1 and status in ('queued','running')",
                    plan_id,
                )
                or 0
            )
            slots = max(0, int(plan["max_workers"]) - active)
            if slots == 0:
                return
            rows = await conn.fetch(
                """
                select id from agent_run_tasks
                where plan_id = $1 and status = 'pending'
                order by question_index asc, attempt_index asc
                limit $2
                """,
                plan_id,
                slots,
            )
            for row in rows:
                tasks_to_queue.append(str(row["id"]))
                await conn.execute(
                    "update agent_run_tasks set status='queued', updated_at=$2 where id=$1",
                    str(row["id"]),
                    now,
                )
        for task_id in tasks_to_queue:
            await self.redis.execute_command(
                "LPUSH",
                self.settings.queue_name,
                __import__("json").dumps({"type": "agent_run_task", "task_id": task_id}),
            )
