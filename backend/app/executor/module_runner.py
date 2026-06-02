from __future__ import annotations

import importlib.util
from importlib import import_module
import inspect
import json
from pathlib import Path
import shutil
import tomllib
from types import ModuleType
from typing import Any

import dspy


def _load_module(name: str, file_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normalize_judge_result(raw_result: Any, item_index: int) -> dict[str, Any]:
    if not isinstance(raw_result, dict):
        raise RuntimeError(
            f"judge_metric must return a dict with keys score, rationale, flags, raw_response (item_index={item_index})"
        )

    required_keys = {"score", "rationale", "flags", "raw_response"}
    actual_keys = set(raw_result.keys())
    if actual_keys != required_keys:
        raise RuntimeError(
            "judge_metric returned invalid keys "
            f"(item_index={item_index}, expected={sorted(required_keys)}, actual={sorted(actual_keys)})"
        )

    score = raw_result["score"]
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise RuntimeError(f"judge_metric score must be a number (item_index={item_index})")

    rationale = raw_result["rationale"]
    if not isinstance(rationale, str):
        raise RuntimeError(f"judge_metric rationale must be a string (item_index={item_index})")

    flags = raw_result["flags"]
    if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
        raise RuntimeError(f"judge_metric flags must be a list of strings (item_index={item_index})")

    return {
        "score": float(score),
        "rationale": rationale,
        "flags": flags,
        "raw_response": raw_result["raw_response"],
    }


def _disable_lm_cache(lm: Any) -> None:
    if hasattr(lm, "cache"):
        try:
            setattr(lm, "cache", False)
        except Exception:
            pass


def _run_judge_metric_without_autolog(raw_metric_fn: Any, example: Any, prediction: Any, lm: Any) -> Any:
    if lm is not None:
        with dspy.context(lm=lm, callbacks=[]):
            return raw_metric_fn(example, prediction)
    with dspy.context(callbacks=[]):
        return raw_metric_fn(example, prediction)


def _load_class(class_path: str) -> type[Any]:
    if "." not in class_path:
        raise RuntimeError(f"lm_class_path must be module-qualified (got: {class_path})")
    module_name, class_name = class_path.rsplit(".", 1)
    module = import_module(module_name)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise RuntimeError(f"lm_class_path class not found: {class_path}")
    if not isinstance(cls, type):
        raise RuntimeError(f"lm_class_path does not point to a class: {class_path}")
    return cls


def _build_lm_from_profile(lm_profile: dict[str, Any]) -> Any:
    class_path = str(lm_profile.get("lm_class_path") or "dspy.LM").strip()
    lm_cls = _load_class(class_path)
    default_params = lm_profile.get("default_params")
    params = default_params if isinstance(default_params, dict) else {}

    profile_id = str(lm_profile.get("id") or "").strip()
    virtual_key = str(lm_profile.get("virtual_key") or "").strip()
    model_name = str(lm_profile.get("model") or "").strip()
    api_base = str(lm_profile.get("api_base") or "").strip()
    if profile_id and virtual_key:
        model_name = f"openai/lm-profile:{profile_id}"
        api_base = str(lm_profile.get("proxy_api_base") or "").strip()

    base_kwargs: dict[str, Any] = {
        "model": model_name,
        "api_base": api_base,
        "api_key": virtual_key,
        "model_type": str(lm_profile.get("model_type") or "responses").strip() or "responses",
    }
    kwargs: dict[str, Any] = {**base_kwargs, **params}
    kwargs = {key: value for key, value in kwargs.items() if value not in (None, "")}

    try:
        signature = inspect.signature(lm_cls)
        accepts_var_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        if not accepts_var_kwargs:
            allowed = set(signature.parameters.keys())
            kwargs = {key: value for key, value in kwargs.items() if key in allowed}
    except Exception:
        pass

    return lm_cls(**kwargs)


def _load_bundle(bundle_path: str) -> tuple[Path, float, ModuleType, ModuleType]:
    root = Path(bundle_path).expanduser().resolve()
    pass_threshold = 0.5
    toml_path = root / "bundle.toml"
    if toml_path.exists():
        try:
            payload = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            threshold_value = payload.get("score_pass_threshold")
            if isinstance(threshold_value, (int, float)) and not isinstance(threshold_value, bool):
                pass_threshold = min(1.0, max(0.0, float(threshold_value)))
        except Exception:
            pass

    module_mod = _load_module("user_module", root / "module.py")
    metric_mod = _load_module("user_metric", root / "metric.py")

    if not hasattr(module_mod, "build_program"):
        raise RuntimeError("module.py must define build_program()")
    if not hasattr(metric_mod, "judge_metric"):
        raise RuntimeError("metric.py must define judge_metric(example, prediction)")

    return root, pass_threshold, module_mod, metric_mod


def _build_eval_example(item: dict[str, Any]) -> dspy.Example:
    input_payload = item.get("input", {})
    label_payload = item.get("label", {})
    if not isinstance(input_payload, dict) or not input_payload:
        raise RuntimeError("evaluation items must include a non-empty input payload")
    if not isinstance(label_payload, dict):
        label_payload = {}
    return dspy.Example(**input_payload, label=label_payload).with_inputs(*input_payload.keys())


def _evaluate_program(
    program: Any,
    raw_metric_fn: Any,
    eval_inputs: list[dict[str, Any]],
    pass_threshold: float,
    lm: Any,
) -> dict[str, Any]:
    original_program_lm = None
    had_program_lm_binding = False

    if lm is not None and hasattr(program, "set_lm"):
        try:
            original_program_lm = getattr(program, "lm", None)
            program.set_lm(lm)
            had_program_lm_binding = True
        except Exception:
            pass

    devset = [_build_eval_example(item) for item in eval_inputs]

    predictions: list[Any] = []
    try:
        for example in devset:
            inputs = example.inputs().toDict()
            if lm is not None:
                with dspy.context(lm=lm):
                    prediction = program(**inputs)
            else:
                prediction = program(**inputs)
            predictions.append(prediction)
    finally:
        if had_program_lm_binding:
            try:
                if hasattr(program, "set_lm"):
                    program.set_lm(original_program_lm)
            except Exception:
                pass

    normalized = []
    for idx, (example, prediction) in enumerate(zip(devset, predictions)):
        judge_result = _normalize_judge_result(
            _run_judge_metric_without_autolog(raw_metric_fn, example, prediction, lm),
            idx,
        )
        normalized.append(
            {
                "item_index": idx,
                "score": judge_result["score"],
                "passed": bool(judge_result["score"] >= pass_threshold),
                "input": example.inputs().toDict(),
                "label": example.labels().toDict(),
                "prediction": prediction.toDict() if hasattr(prediction, "toDict") else {"value": str(prediction)},
                "rationale": judge_result["rationale"],
                "flags": judge_result["flags"],
                "raw_response": judge_result["raw_response"],
            }
        )

    avg_score = sum(item["score"] for item in normalized) / float(len(normalized)) if normalized else 0.0
    return {
        "score_pct": avg_score * 100.0,
        "items": normalized,
        "score_pass_threshold": pass_threshold,
    }


def _collect_output_fields(program: Any) -> list[str]:
    output_fields: list[str] = []
    for _, predictor in program.named_predictors():
        signature = getattr(predictor, "signature", None)
        fields = getattr(signature, "output_fields", {}) if signature is not None else {}
        for field_name in fields.keys():
            if field_name not in output_fields:
                output_fields.append(field_name)
    if not output_fields:
        raise RuntimeError("program must define at least one predictor output field for optimization")
    return output_fields


def _normalize_demo_target(
    label_payload: dict[str, Any],
    prediction_payload: dict[str, Any],
    output_fields: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    matched_label = {field_name: label_payload[field_name] for field_name in output_fields if field_name in label_payload}
    if matched_label:
        return matched_label, "label_payload"

    matched_prediction = {field_name: prediction_payload[field_name] for field_name in output_fields if field_name in prediction_payload}
    if matched_prediction:
        return matched_prediction, "prediction_payload"

    if len(output_fields) == 1:
        field_name = output_fields[0]
        if len(label_payload) == 1:
            return {field_name: next(iter(label_payload.values()))}, "label_payload_mapped"
        if len(prediction_payload) == 1:
            return {field_name: next(iter(prediction_payload.values()))}, "prediction_payload_mapped"

    return None, None


def _build_demo_examples(
    records: list[dict[str, Any]],
    output_fields: list[str],
) -> tuple[list[dspy.Example], dict[str, Any]]:
    examples: list[dspy.Example] = []
    skipped_reasons: dict[str, int] = {}
    target_provenance_counts: dict[str, int] = {}

    for record in records:
        input_payload = record.get("input", {})
        label_payload = record.get("label", {})
        prediction_payload = record.get("prediction", {})
        if not isinstance(input_payload, dict) or not input_payload:
            skipped_reasons["missing_input"] = skipped_reasons.get("missing_input", 0) + 1
            continue
        if not isinstance(label_payload, dict):
            label_payload = {}
        if not isinstance(prediction_payload, dict):
            prediction_payload = {}

        target_payload, target_provenance = _normalize_demo_target(label_payload, prediction_payload, output_fields)
        if not target_payload:
            skipped_reasons["unmappable_demo_target"] = skipped_reasons.get("unmappable_demo_target", 0) + 1
            continue

        gold_label = label_payload or target_payload
        example = dspy.Example(**input_payload, **target_payload, label=gold_label).with_inputs(*input_payload.keys())
        examples.append(example)
        if target_provenance is not None:
            target_provenance_counts[target_provenance] = target_provenance_counts.get(target_provenance, 0) + 1

    return examples, {
        "requested_record_count": len(records),
        "usable_record_count": len(examples),
        "skipped_record_count": sum(skipped_reasons.values()),
        "skipped_reasons": skipped_reasons,
        "target_provenance_counts": target_provenance_counts,
    }


def _coerce_feedback_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _build_feedback_examples(
    records: list[dict[str, Any]],
    output_fields: list[str],
) -> tuple[list[dspy.Example], dict[str, Any]]:
    examples: list[dspy.Example] = []
    skipped_reasons: dict[str, int] = {}
    target_provenance_counts: dict[str, int] = {}

    for record in records:
        input_payload = record.get("input", {})
        label_payload = record.get("label", {})
        prediction_payload = record.get("prediction", {})
        if not isinstance(input_payload, dict) or not input_payload:
            skipped_reasons["missing_input"] = skipped_reasons.get("missing_input", 0) + 1
            continue
        if not isinstance(label_payload, dict):
            label_payload = {}
        if not isinstance(prediction_payload, dict):
            prediction_payload = {}

        target_payload, target_provenance = _normalize_demo_target(label_payload, prediction_payload, output_fields)
        if not target_payload:
            skipped_reasons["unmappable_demo_target"] = skipped_reasons.get("unmappable_demo_target", 0) + 1
            continue

        feedback = _coerce_feedback_text(record.get("feedback") or record.get("rationale"))
        example = dspy.Example(
            **input_payload,
            **target_payload,
            optimization_feedback=feedback,
            label=label_payload or target_payload,
        ).with_inputs(*input_payload.keys())
        examples.append(example)
        if target_provenance is not None:
            target_provenance_counts[target_provenance] = target_provenance_counts.get(target_provenance, 0) + 1

    return examples, {
        "requested_record_count": len(records),
        "usable_record_count": len(examples),
        "skipped_record_count": sum(skipped_reasons.values()),
        "skipped_reasons": skipped_reasons,
        "target_provenance_counts": target_provenance_counts,
    }


def _serialize_example(example: Any) -> dict[str, Any]:
    try:
        inputs = example.inputs().toDict() if hasattr(example, "inputs") else {}
    except Exception:
        inputs = {}
    try:
        labels = example.labels().toDict() if hasattr(example, "labels") else {}
    except Exception:
        labels = {}
    if "label" in labels and isinstance(labels["label"], dict):
        label_payload = labels["label"]
    else:
        label_payload = labels
    return {
        "input": inputs,
        "label": label_payload,
        "input_keys": sorted(inputs.keys()),
        "label_keys": sorted(label_payload.keys()),
    }


def _summarize_predictor_demos(program: Any) -> list[dict[str, Any]]:
    predictor_summaries: list[dict[str, Any]] = []
    for name, predictor in program.named_predictors():
        demos = getattr(predictor, "demos", None)
        if not isinstance(demos, list):
            demos = []
        predictor_summaries.append(
            {
                "predictor": name,
                "demo_count": len(demos),
                "demos": [_serialize_example(example) for example in demos],
            }
        )
    return predictor_summaries


def _save_program_state(program: Any, program_state_path: Path) -> None:
    predictor_lms: list[tuple[Any, Any]] = []
    try:
        for _, predictor in program.named_predictors():
            if hasattr(predictor, "lm"):
                predictor_lms.append((predictor, predictor.lm))
                predictor.lm = None

        state = program.dump_state() if hasattr(program, "dump_state") else {}
        program_state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    finally:
        for predictor, lm in predictor_lms:
            predictor.lm = lm


def _normalize_auto_budget(value: Any) -> str:
    normalized = str(value or "medium").strip().lower()
    if normalized not in {"light", "medium", "heavy"}:
        raise RuntimeError("MIPROv2 auto budget must be one of: light, medium, heavy")
    return normalized


def run_bundle_optimization(
    bundle_path: str,
    strategy: str,
    train_records: list[dict[str, Any]],
    val_inputs: list[dict[str, Any]],
    artifact_dir: str,
    num_threads: int = 1,
    execution_lm_profile: dict[str, Any] | None = None,
    helper_lm_profile: dict[str, Any] | None = None,
    dspy_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _, pass_threshold, module_mod, metric_mod = _load_bundle(bundle_path)
    raw_metric_fn = metric_mod.judge_metric
    config = dict(dspy_config or {})

    def _build_execution_lm() -> Any:
        if execution_lm_profile is not None:
            return _build_lm_from_profile(execution_lm_profile)
        if hasattr(module_mod, "build_lm"):
            return module_mod.build_lm()
        return None

    student_program = module_mod.build_program()
    baseline_program = module_mod.build_program()

    execution_lm = _build_execution_lm()
    if execution_lm is not None:
        _disable_lm_cache(execution_lm)
        student_program.set_lm(execution_lm)
        baseline_program.set_lm(execution_lm)

    helper_lm = None
    if helper_lm_profile is not None:
        helper_lm = _build_lm_from_profile(helper_lm_profile)
    elif execution_lm is not None:
        helper_lm = execution_lm
    if helper_lm is not None:
        _disable_lm_cache(helper_lm)

    output_fields = _collect_output_fields(student_program)
    trainset, dataset_summary = _build_demo_examples(train_records, output_fields)
    if not trainset:
        raise RuntimeError("optimization dataset produced no usable demo examples")

    evaluation_inputs = val_inputs or [{"input": record.get("input", {}), "label": record.get("label", {})} for record in train_records]
    compile_valset = [_build_eval_example(item) for item in evaluation_inputs]

    def compile_metric(example: Any, prediction: Any, trace: Any = None) -> float:
        del trace
        result = _normalize_judge_result(
            _run_judge_metric_without_autolog(raw_metric_fn, example, prediction, execution_lm),
            0,
        )
        return float(result["score"])

    normalized_strategy = strategy.strip().lower().replace("-", "_")
    if normalized_strategy == "bootstrapfewshot":
        normalized_strategy = "bootstrap_fewshot"
    auto_budget = _normalize_auto_budget(config.get("auto"))

    if normalized_strategy == "bootstrap_fewshot":
        optimizer = dspy.BootstrapFewShot(
            metric=compile_metric,
            metric_threshold=pass_threshold,
            max_bootstrapped_demos=max(1, int(config.get("max_bootstrapped_demos", 4))),
            max_labeled_demos=max(1, int(config.get("max_labeled_demos", 16))),
        )
        teacher = None
        if helper_lm is not None and helper_lm is not execution_lm:
            teacher = module_mod.build_program()
            teacher.set_lm(helper_lm)
        compile_context = dspy.context(lm=execution_lm) if execution_lm is not None else dspy.context()
        with compile_context:
            compiled_program = optimizer.compile(student_program, teacher=teacher, trainset=trainset)
        strategy_summary = {
            "optimizer_class": "BootstrapFewShot",
            "max_bootstrapped_demos": max(1, int(config.get("max_bootstrapped_demos", 4))),
            "max_labeled_demos": max(1, int(config.get("max_labeled_demos", 16))),
            "teacher_lm_profile_id": config.get("teacher_lm_profile_id"),
        }
    elif normalized_strategy == "miprov2":
        optimizer = dspy.MIPROv2(
            metric=compile_metric,
            prompt_model=helper_lm or execution_lm,
            task_model=execution_lm,
            auto=auto_budget,  # pyright: ignore[reportArgumentType]
            max_bootstrapped_demos=max(1, int(config.get("max_bootstrapped_demos", 4))),
            max_labeled_demos=max(1, int(config.get("max_labeled_demos", 16))),
            num_threads=max(1, int(num_threads)),
            track_stats=True,
            metric_threshold=pass_threshold,
        )
        compile_context = dspy.context(lm=execution_lm) if execution_lm is not None else dspy.context()
        with compile_context:
            compiled_program = optimizer.compile(student_program, trainset=trainset, valset=compile_valset)
        strategy_summary = {
            "optimizer_class": "MIPROv2",
            "auto": auto_budget,
            "max_bootstrapped_demos": max(1, int(config.get("max_bootstrapped_demos", 4))),
            "max_labeled_demos": max(1, int(config.get("max_labeled_demos", 16))),
            "candidate_program_count": len(getattr(compiled_program, "candidate_programs", []) or []),
            "prompt_model_total_calls": getattr(compiled_program, "prompt_model_total_calls", 0),
            "total_calls": getattr(compiled_program, "total_calls", 0),
        }
    elif normalized_strategy == "gepa":
        feedback_trainset, feedback_dataset_summary = _build_feedback_examples(train_records, output_fields)
        if not feedback_trainset:
            raise RuntimeError("optimization dataset produced no usable demo examples")

        trainset = feedback_trainset
        dataset_summary = feedback_dataset_summary

        auto_budget = _normalize_auto_budget(config.get("auto"))
        reflection_lm = helper_lm or execution_lm
        if reflection_lm is not None:
            _disable_lm_cache(reflection_lm)

        def gepa_metric(
            example: Any,
            prediction: Any,
            trace: Any = None,
            pred_name: str | None = None,
            pred_trace: Any = None,
        ) -> dict[str, Any]:
            del trace, pred_name, pred_trace
            result = _normalize_judge_result(
                _run_judge_metric_without_autolog(raw_metric_fn, example, prediction, execution_lm),
                0,
            )
            record_feedback = _coerce_feedback_text(getattr(example, "optimization_feedback", None))
            rationale_feedback = result["rationale"]
            if record_feedback and rationale_feedback:
                combined = f"{record_feedback}\n\nJudge rationale: {rationale_feedback}"
            else:
                combined = record_feedback or rationale_feedback
            return {"score": float(result["score"]), "feedback": combined or f"This trajectory got a score of {result['score']}."}

        optimizer = dspy.GEPA(
            metric=gepa_metric,
            auto=auto_budget,  # pyright: ignore[reportArgumentType]
            track_stats=bool(config.get("track_stats", True)),
            reflection_lm=reflection_lm,
            num_threads=max(1, int(num_threads)),
            log_dir=str(Path(artifact_dir).expanduser().resolve() / "gepa_logs"),
        )

        compile_context = dspy.context(lm=execution_lm) if execution_lm is not None else dspy.context()
        with compile_context:
            compiled_program = optimizer.compile(student_program, trainset=trainset, valset=compile_valset)

        detailed_results = getattr(compiled_program, "detailed_results", None)
        strategy_summary = {
            "optimizer_class": "GEPA",
            "auto": auto_budget,
            "track_stats": bool(config.get("track_stats", True)),
            "candidate_count": len(getattr(detailed_results, "candidates", []) or []),
            "total_metric_calls": getattr(detailed_results, "total_metric_calls", None),
            "num_full_val_evals": getattr(detailed_results, "num_full_val_evals", None),
            "reflection_lm_profile_id": config.get("reflection_lm_profile_id"),
        }
    else:
        raise RuntimeError(f"unsupported optimization strategy: {strategy}")

    baseline_eval_lm = _build_execution_lm()
    if baseline_eval_lm is not None:
        _disable_lm_cache(baseline_eval_lm)
    optimized_eval_lm = _build_execution_lm()
    if optimized_eval_lm is not None:
        _disable_lm_cache(optimized_eval_lm)

    baseline_report = _evaluate_program(baseline_program, raw_metric_fn, evaluation_inputs, pass_threshold, baseline_eval_lm)
    optimized_report = _evaluate_program(compiled_program, raw_metric_fn, evaluation_inputs, pass_threshold, optimized_eval_lm)

    artifact_root = Path(artifact_dir).expanduser().resolve()
    if artifact_root.exists():
        shutil.rmtree(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    program_state_path = artifact_root / "program.json"
    _save_program_state(compiled_program, program_state_path)

    predictor_demos = _summarize_predictor_demos(compiled_program)
    comparison_summary = {
        "baseline_score_pct": baseline_report["score_pct"],
        "optimized_score_pct": optimized_report["score_pct"],
        "score_delta_pct": optimized_report["score_pct"] - baseline_report["score_pct"],
        "baseline_item_count": len(baseline_report["items"]),
        "optimized_item_count": len(optimized_report["items"]),
    }
    telemetry_summary = {
        "strategy": normalized_strategy,
        "score_pass_threshold": pass_threshold,
        "dataset_summary": dataset_summary,
        "evaluation_item_count": len(evaluation_inputs),
        "selected_demos": predictor_demos,
        "strategy_details": strategy_summary,
    }
    artifact_metadata = {
        "artifact_type": "dspy_program_state",
        "artifact_dir": str(artifact_root),
        "program_state_path": str(program_state_path),
        "predictor_count": len(compiled_program.predictors()),
        "selected_demo_count": sum(item["demo_count"] for item in predictor_demos),
    }

    return {
        "artifact_path": str(program_state_path),
        "artifact_metadata": artifact_metadata,
        "telemetry_summary": telemetry_summary,
        "comparison_summary": comparison_summary,
    }


def run_bundle_eval(
    bundle_path: str,
    eval_inputs: list[dict[str, Any]],
    num_threads: int = 1,
    lm_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _, pass_threshold, module_mod, metric_mod = _load_bundle(bundle_path)

    program = module_mod.build_program()
    if hasattr(module_mod, "build_lm"):
        lm = module_mod.build_lm()
    elif lm_profile is not None:
        lm = _build_lm_from_profile(lm_profile)
    else:
        lm = None
    if lm is not None:
        _disable_lm_cache(lm)
    raw_metric_fn = metric_mod.judge_metric
    return _evaluate_program(program, raw_metric_fn, eval_inputs, pass_threshold, lm)
