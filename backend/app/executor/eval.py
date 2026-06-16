from __future__ import annotations

import os
from typing import Any

from app.executor.module_runner import run_bundle_eval


def _configure_dspy_mlflow_autolog(services: Any, project_id: str) -> None:
    settings = getattr(services, "settings", None)
    tracking_uri = getattr(settings, "mlflow_tracking_uri", "") if settings is not None else ""
    if not tracking_uri:
        return

    try:
        import mlflow
    except ImportError:
        return

    dspy_mlflow = getattr(mlflow, "dspy", None)
    if dspy_mlflow is None or not hasattr(dspy_mlflow, "autolog"):
        return

    try:
        mlflow.set_tracking_uri(tracking_uri)
        dspy_mlflow.autolog(log_compiles=True, log_evals=True, log_traces_from_compile=True)
    except Exception:
        return


def _run_bundle_eval_with_mlflow_parent(
    bundle_path: str,
    eval_inputs: list[dict[str, Any]],
    num_threads: int,
    parent_run_id: str | None,
    lm_profile: dict[str, Any] | None = None,
    runtime_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        if lm_profile is None:
            return run_bundle_eval(bundle_path=bundle_path, eval_inputs=eval_inputs, num_threads=num_threads, runtime_env=runtime_env)
        return run_bundle_eval(bundle_path=bundle_path, eval_inputs=eval_inputs, num_threads=num_threads, lm_profile=lm_profile, runtime_env=runtime_env)

    if not parent_run_id:
        return _run()
    try:
        import mlflow
    except ImportError:
        return _run()
    try:
        with mlflow.start_run(run_id=parent_run_id):
            return _run()
    except Exception:
        return _run()


def _recent_trace_ids(tracking_uri: str, experiment_id: str, max_results: int = 200) -> set[str]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return set()
    try:
        from mlflow.tracking import MlflowClient
    except ImportError:
        return set()
    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        traces = client.search_traces(experiment_ids=[experiment_id], max_results=max_results)
    except Exception:
        return set()
    return {t.info.trace_id for t in traces if getattr(t, "info", None) is not None and t.info.trace_id}


def _link_traces_to_parent_run(tracking_uri: str, parent_run_id: str, trace_ids: set[str]) -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    if not trace_ids:
        return
    try:
        from mlflow.tracking import MlflowClient
    except ImportError:
        return
    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        client.link_traces_to_run(sorted(trace_ids), parent_run_id)
    except Exception as exc:
        message = str(exc)
        if "entity_associations" in message and "UNIQUE constraint failed" in message:
            return
        raise


def _list_parent_run_traces(tracking_uri: str, experiment_id: str, parent_run_id: str) -> list[Any]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return []
    try:
        from mlflow.tracking import MlflowClient
    except ImportError:
        return []
    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        traces = client.search_traces(
            experiment_ids=[experiment_id],
            filter_string=f"run_id = '{parent_run_id}'",
            max_results=500,
        )
    except Exception:
        return []
    return list(traces)


def _request_preview(trace: Any) -> str:
    info = getattr(trace, "info", None)
    return str(getattr(info, "request_preview", "") or "")


def _match_trace_id_for_item(item_input: dict[str, Any], traces: list[Any], used: set[str]) -> str | None:
    question = str(item_input.get("question", "")).strip()
    if question:
        for trace in traces:
            trace_id = getattr(getattr(trace, "info", None), "trace_id", None)
            if not trace_id or trace_id in used:
                continue
            if question in _request_preview(trace):
                used.add(trace_id)
                return str(trace_id)
    for trace in traces:
        trace_id = getattr(getattr(trace, "info", None), "trace_id", None)
        if trace_id and trace_id not in used:
            used.add(trace_id)
            return str(trace_id)
    return None
