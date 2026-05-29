from __future__ import annotations

import json
import os
import time
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
) -> dict[str, Any]:
    if not parent_run_id:
        return run_bundle_eval(bundle_path=bundle_path, eval_inputs=eval_inputs, num_threads=num_threads)
    try:
        import mlflow
    except ImportError:
        return run_bundle_eval(bundle_path=bundle_path, eval_inputs=eval_inputs, num_threads=num_threads)
    try:
        with mlflow.start_run(run_id=parent_run_id):
            return run_bundle_eval(bundle_path=bundle_path, eval_inputs=eval_inputs, num_threads=num_threads)
    except Exception:
        return run_bundle_eval(bundle_path=bundle_path, eval_inputs=eval_inputs, num_threads=num_threads)


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
    client.link_traces_to_run(sorted(trace_ids), parent_run_id)


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


def _log_trace_feedback(
    tracking_uri: str,
    trace_id: str,
    parent_run_id: str,
    score: float,
    judge_name: str = "judge_metric",
) -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    try:
        import mlflow
        from mlflow.entities.assessment_source import AssessmentSource
    except ImportError:
        return
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.log_feedback(
        trace_id=trace_id,
        name=judge_name,
        value=float(score),
        source=AssessmentSource(source_type="CODE", source_id="dspy-trainer"),
        metadata={"mlflow.assessment.sourceRunId": parent_run_id},
    )


def _cleanup_duplicate_judge_assessments(tracking_uri: str, trace_id: str, parent_run_id: str, judge_name: str = "judge_metric") -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    try:
        import mlflow
    except ImportError:
        return
    mlflow.set_tracking_uri(tracking_uri)
    try:
        trace = mlflow.get_trace(trace_id)
    except Exception:
        return
    assessments = getattr(getattr(trace, "info", None), "assessments", None) or []
    for assessment in assessments:
        assessment_name = getattr(assessment, "name", None) or getattr(assessment, "assessment_name", "")
        if assessment_name != judge_name:
            continue
        metadata = getattr(assessment, "metadata", None) or {}
        if metadata.get("mlflow.assessment.sourceRunId") == parent_run_id:
            continue
        assessment_id = getattr(assessment, "assessment_id", None)
        if not assessment_id:
            continue
        try:
            mlflow.delete_assessment(trace_id=trace_id, assessment_id=assessment_id)
        except Exception:
            continue


def _normalize_judge_assessments_for_run(
    tracking_uri: str,
    experiment_id: str,
    parent_run_id: str,
    judge_name: str = "judge_metric",
) -> None:
    traces = _list_parent_run_traces(tracking_uri, experiment_id, parent_run_id)
    for trace in traces:
        trace_id = getattr(getattr(trace, "info", None), "trace_id", None)
        if not trace_id:
            continue
        _cleanup_duplicate_judge_assessments(
            tracking_uri=tracking_uri,
            trace_id=str(trace_id),
            parent_run_id=parent_run_id,
            judge_name=judge_name,
        )


async def run_eval_job(services: Any, eval_job_id: str) -> dict[str, Any] | None:
    job = await services.get_eval_job(eval_job_id)
    if job is None:
        return None

    if job["status"] == "canceled":
        return job

    await services.set_eval_job_status(eval_job_id, "running")
    job = await services.get_eval_job(eval_job_id)
    if job is None:
        return None

    try:
        _configure_dspy_mlflow_autolog(services, str(job["project_id"]))
        experiment_id = job.get("mlflow_experiment_id")
        parent_run_id = job.get("mlflow_parent_run_id")
        if not experiment_id:
            experiment_id = await services.ensure_mlflow_experiment(job["project_id"])
        if not parent_run_id:
            eval_name = str(job.get("eval_name") or eval_job_id)
            parent_tags = {
                "project_id": str(job["project_id"]),
                "module_import_id": str(job["module_import_id"]),
                "eval_job_id": str(eval_job_id),
                "eval_name": eval_name,
                "scenario_id": str(job["scenario_id"]),
                "dataset_version": str(job["dataset_version"]),
                "mlflow_experiment_id": str(experiment_id),
            }
            parent_run_id = await services.create_mlflow_run(
                experiment_id=str(experiment_id),
                run_name=eval_name,
                tags=parent_tags,
            )
        await services.set_eval_job_mlflow(
            eval_job_id=eval_job_id,
            mlflow_experiment_id=str(experiment_id),
            mlflow_parent_run_id=str(parent_run_id),
        )
        job = await services.get_eval_job(eval_job_id)
        if job is None:
            return None
    except Exception as exc:
        await services.set_eval_job_status(eval_job_id, "failed", failure_reason=f"mlflow_setup_failed: {exc}")
        return await services.get_eval_job(eval_job_id)

    eval_inputs = job.get("eval_inputs") or []
    if isinstance(eval_inputs, str):
        try:
            eval_inputs = json.loads(eval_inputs)
        except json.JSONDecodeError as exc:
            await services.set_eval_job_status(
                eval_job_id,
                "failed",
                failure_reason=f"invalid_eval_inputs_json: {exc}",
            )
            return await services.get_eval_job(eval_job_id)
    if not isinstance(eval_inputs, list):
        await services.set_eval_job_status(
            eval_job_id,
            "failed",
            failure_reason="eval_inputs must be a JSON array",
        )
        return await services.get_eval_job(eval_job_id)
    repeats = max(1, int(job.get("repeat_count", 1)))
    num_threads = max(1, int(job.get("num_threads", 1)))
    bundle_path = job.get("bundle_path")
    if not bundle_path:
        await services.set_eval_job_status(eval_job_id, "failed", failure_reason="bundle_path is required")
        return await services.get_eval_job(eval_job_id)

    try:
        tracking_uri = str(getattr(services.settings, "mlflow_tracking_uri", ""))
        experiment_id_for_trace = str(job.get("mlflow_experiment_id") or "")
        trace_ids_before = _recent_trace_ids(tracking_uri, experiment_id_for_trace) if tracking_uri and experiment_id_for_trace else set()
        created_items: list[dict[str, Any]] = []
        for repeat_index in range(repeats):
            result = _run_bundle_eval_with_mlflow_parent(
                bundle_path=bundle_path,
                eval_inputs=eval_inputs,
                num_threads=num_threads,
                parent_run_id=str(job.get("mlflow_parent_run_id") or ""),
            )

            for item in result["items"]:
                item_index = int(item["item_index"])
                item_result = await services.create_eval_run_item(
                    eval_job_id=eval_job_id,
                    status="succeeded",
                    repeat_index=repeat_index,
                    item_index=item_index,
                    score=float(item["score"]),
                    input_payload=item["input"],
                    prediction_payload=item["prediction"],
                    label_payload=item["label"],
                    rationale=item["rationale"],
                    mlflow_item_run_id=None,
                    mlflow_trace_id=None,
                )

                item_id = str(item_result["id"])
                created_items.append({"item_id": item_id, "input": item["input"], "score": float(item["score"])})
                created_items[-1]["prediction"] = item["prediction"]
                created_items[-1]["label"] = item["label"]
                # DSPy + MLflow autologging emits the canonical traces.
                # We intentionally avoid emitting additional synthetic traces here.
        if tracking_uri and str(job.get("mlflow_parent_run_id") or "") and experiment_id_for_trace:
            trace_ids_after: set[str] = set()
            for _ in range(10):
                trace_ids_after = _recent_trace_ids(tracking_uri, experiment_id_for_trace)
                if trace_ids_after - trace_ids_before:
                    break
                time.sleep(0.5)
            _link_traces_to_parent_run(
                tracking_uri=tracking_uri,
                parent_run_id=str(job["mlflow_parent_run_id"]),
                trace_ids=trace_ids_after - trace_ids_before,
            )
            parent_traces = _list_parent_run_traces(
                tracking_uri=tracking_uri,
                experiment_id=experiment_id_for_trace,
                parent_run_id=str(job["mlflow_parent_run_id"]),
            )
            used_trace_ids: set[str] = set()
            for item in created_items:
                trace_id = _match_trace_id_for_item(item["input"], parent_traces, used_trace_ids)
                if not trace_id:
                    continue
                await services.set_eval_run_item_trace_id(str(item["item_id"]), trace_id)
                _log_trace_feedback(
                    tracking_uri=tracking_uri,
                    trace_id=trace_id,
                    parent_run_id=str(job["mlflow_parent_run_id"]),
                    score=float(item["score"]),
                )
                _cleanup_duplicate_judge_assessments(
                    tracking_uri=tracking_uri,
                    trace_id=trace_id,
                    parent_run_id=str(job["mlflow_parent_run_id"]),
                )
            _normalize_judge_assessments_for_run(
                tracking_uri=tracking_uri,
                experiment_id=experiment_id_for_trace,
                parent_run_id=str(job["mlflow_parent_run_id"]),
            )
    except Exception as exc:
        if job.get("mlflow_parent_run_id"):
            try:
                await services.finalize_mlflow_run(str(job["mlflow_parent_run_id"]), status="FAILED")
            except Exception:
                pass
        await services.set_eval_job_status(eval_job_id, "failed", failure_reason=f"eval_run_failed: {exc}")
        return await services.get_eval_job(eval_job_id)

    if job.get("mlflow_parent_run_id"):
        await services.finalize_mlflow_run(str(job["mlflow_parent_run_id"]), status="FINISHED")
    await services.set_eval_job_status(eval_job_id, "succeeded")
    return await services.get_eval_job(eval_job_id)
