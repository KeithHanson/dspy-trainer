from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.util
from importlib import import_module
import io
import inspect
import json
import logging
from pathlib import Path
import shutil
import sys
import tomllib
from types import ModuleType
from typing import Any, Callable

import dspy


DEFAULT_PROXY_API_BASE = "http://litellm-proxy:4000"
AZURE_RESPONSES_COMPAT_CLASS_PATH = "app.lm.AzureResponsesCompatLM"


class _TeeWriter:
    def __init__(self, primary: Any, mirror: io.StringIO, label: str, log_event: Callable[[str], None] | None):
        self.primary = primary
        self.mirror = mirror
        self.label = label
        self.log_event = log_event
        self._line_buffer = ""
        self._started = False

    def _start(self) -> None:
        if not self._started and self.log_event is not None:
            self.log_event(f"raw_{self.label}_begin")
            self._started = True

    def _emit_complete_lines(self) -> None:
        if self.log_event is None:
            return
        while True:
            newline_index = self._line_buffer.find("\n")
            carriage_index = self._line_buffer.find("\r")
            indexes = [index for index in (newline_index, carriage_index) if index != -1]
            if not indexes:
                break
            split_index = min(indexes)
            line = self._line_buffer[:split_index]
            self._line_buffer = self._line_buffer[split_index + 1 :]
            self._start()
            self.log_event(line)

    def finish(self) -> None:
        if self.log_event is not None and self._line_buffer:
            self._start()
            self.log_event(self._line_buffer)
            self._line_buffer = ""
        if self._started and self.log_event is not None:
            self.log_event(f"raw_{self.label}_end")

    def write(self, data: str) -> int:
        written = self.primary.write(data)
        self.primary.flush()
        self.mirror.write(data)
        self._line_buffer += data
        self._emit_complete_lines()
        return written

    def flush(self) -> None:
        self.primary.flush()
        self.mirror.flush()


@contextmanager
def _capture_process_output(log_event: Callable[[str], None] | None):
    if log_event is None:
        yield
        return

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    stdout_writer = _TeeWriter(sys.stdout, stdout_buffer, "stdout", log_event)
    stderr_writer = _TeeWriter(sys.stderr, stderr_buffer, "stderr", log_event)

    class _ProcessLogHandler(logging.Handler):
        def __init__(self, callback: Callable[[str], None] | None):
            super().__init__(level=logging.NOTSET)
            self.callback = callback
            self.started = False

        def emit(self, record: logging.LogRecord) -> None:
            if self.callback is None:
                return
            message = self.format(record)
            if message.startswith("[optimization:"):
                return
            if not self.started:
                self.callback("raw_logging_begin")
                self.started = True
            self.callback(message)

        def finish(self) -> None:
            if self.started and self.callback is not None:
                self.callback("raw_logging_end")

    handler = _ProcessLogHandler(log_event)
    handler.setLevel(logging.NOTSET)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        with redirect_stdout(stdout_writer), redirect_stderr(stderr_writer):
            yield
    finally:
        root_logger.removeHandler(handler)
        handler.flush()
        stdout_writer.finish()
        stderr_writer.finish()
        handler.finish()


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
    model_name = str(lm_profile.get("model") or "").strip()
    model_type = str(lm_profile.get("model_type") or "responses").strip() or "responses"
    if class_path == "dspy.LM" and model_type == "responses" and model_name.lower().startswith("azure/"):
        class_path = AZURE_RESPONSES_COMPAT_CLASS_PATH
    lm_cls = _load_class(class_path)
    default_params = lm_profile.get("default_params")
    params = default_params if isinstance(default_params, dict) else {}

    profile_id = str(lm_profile.get("id") or "").strip()
    virtual_key = str(lm_profile.get("virtual_key") or "").strip()
    api_base = str(lm_profile.get("api_base") or "").strip()
    if profile_id and virtual_key:
        model_name = f"openai/lm-profile:{profile_id}"
        api_base = str(lm_profile.get("proxy_api_base") or DEFAULT_PROXY_API_BASE).strip()

    base_kwargs: dict[str, Any] = {
        "model": model_name,
        "api_base": api_base,
        "api_key": virtual_key,
        "model_type": model_type,
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
    phase_name: str = "evaluation",
    log_event: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def _log(message: str) -> None:
        if log_event is not None:
            log_event(message)

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

    _log(f"phase={phase_name}:start")
    predictions: list[Any] = []
    try:
        for idx, example in enumerate(devset):
            inputs = example.inputs().toDict()
            _log(f"phase={phase_name}:program_call_start:item={idx}")
            try:
                if lm is not None:
                    with dspy.context(lm=lm):
                        prediction = program(**inputs)
                else:
                    prediction = program(**inputs)
            except Exception as exc:
                _log(f"phase={phase_name}:program_call_failed:item={idx}:error={exc}")
                raise
            _log(f"phase={phase_name}:program_call_done:item={idx}")
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
        _log(f"phase={phase_name}:judge_call_start:item={idx}")
        try:
            judge_result = _normalize_judge_result(
                _run_judge_metric_without_autolog(raw_metric_fn, example, prediction, lm),
                idx,
            )
        except Exception as exc:
            _log(f"phase={phase_name}:judge_call_failed:item={idx}:error={exc}")
            raise
        _log(f"phase={phase_name}:judge_call_done:item={idx}:score={judge_result['score']}")
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
    _log(f"phase={phase_name}:done:score_pct={avg_score * 100.0}")
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


def _resolve_demo_output_fields(records: list[dict[str, Any]], fallback_output_fields: list[str]) -> tuple[list[str], str]:
    prediction_fields: list[str] = []
    for record in records:
        prediction_payload = record.get("prediction", {})
        if not isinstance(prediction_payload, dict):
            continue
        for field_name in prediction_payload.keys():
            if field_name not in prediction_fields:
                prediction_fields.append(field_name)
    if prediction_fields:
        return prediction_fields, "top_level_prediction"

    label_fields: list[str] = []
    for record in records:
        label_payload = record.get("label", {})
        if not isinstance(label_payload, dict):
            continue
        for field_name in label_payload.keys():
            if field_name not in label_fields:
                label_fields.append(field_name)
    if label_fields:
        return label_fields, "label_payload"

    return fallback_output_fields, "predictor_outputs"


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
    skipped_preview: list[dict[str, Any]] = []

    for record in records:
        input_payload = record.get("input", {})
        label_payload = record.get("label", {})
        prediction_payload = record.get("prediction", {})
        if not isinstance(input_payload, dict) or not input_payload:
            skipped_reasons["missing_input"] = skipped_reasons.get("missing_input", 0) + 1
            if len(skipped_preview) < 3:
                skipped_preview.append({"reason": "missing_input"})
            continue
        if not isinstance(label_payload, dict):
            label_payload = {}
        if not isinstance(prediction_payload, dict):
            prediction_payload = {}

        target_payload, target_provenance = _normalize_demo_target(label_payload, prediction_payload, output_fields)
        if not target_payload:
            skipped_reasons["unmappable_demo_target"] = skipped_reasons.get("unmappable_demo_target", 0) + 1
            if len(skipped_preview) < 3:
                skipped_preview.append(
                    {
                        "reason": "unmappable_demo_target",
                        "input_keys": sorted(input_payload.keys()),
                        "label_keys": sorted(label_payload.keys()),
                        "prediction_keys": sorted(prediction_payload.keys()),
                        "expected_output_fields": output_fields,
                    }
                )
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
        "skipped_preview": skipped_preview,
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
    baseline_summary: dict[str, Any] | None = None,
    log_event: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def _log(message: str) -> None:
        if log_event is not None:
            log_event(message)

    _, pass_threshold, module_mod, metric_mod = _load_bundle(bundle_path)
    raw_metric_fn = metric_mod.judge_metric
    config = dict(dspy_config or {})
    _log(f"bundle_path={bundle_path}")
    _log(f"pass_threshold={pass_threshold}")

    def _build_execution_lm() -> Any:
        if execution_lm_profile is not None:
            return _build_lm_from_profile(execution_lm_profile)
        if hasattr(module_mod, "build_lm"):
            return module_mod.build_lm()
        return None

    student_program = module_mod.build_program()
    baseline_program = module_mod.build_program() if baseline_summary is None else None

    execution_lm = _build_execution_lm()
    if execution_lm is not None:
        _disable_lm_cache(execution_lm)
        student_program.set_lm(execution_lm)
        if baseline_program is not None:
            baseline_program.set_lm(execution_lm)

    helper_lm = None
    if helper_lm_profile is not None:
        helper_lm = _build_lm_from_profile(helper_lm_profile)
    elif execution_lm is not None:
        helper_lm = execution_lm
    if helper_lm is not None:
        _disable_lm_cache(helper_lm)

    with _capture_process_output(log_event):
        predictor_output_fields = _collect_output_fields(student_program)
        output_fields, output_field_source = _resolve_demo_output_fields(train_records, predictor_output_fields)
        _log(f"predictor_output_fields={','.join(predictor_output_fields) if predictor_output_fields else '<none>'}")
        _log(f"output_fields={','.join(output_fields) if output_fields else '<none>'}")
        _log(f"output_field_source={output_field_source}")
        trainset, dataset_summary = _build_demo_examples(train_records, output_fields)
        _log(f"usable_train_examples={len(trainset)}")
        _log(f"demo_dataset_summary={json.dumps(dataset_summary, sort_keys=True)}")
        if not trainset:
            raise RuntimeError("optimization dataset produced no usable demo examples")

        evaluation_inputs = val_inputs or [{"input": record.get("input", {}), "label": record.get("label", {})} for record in train_records]
        compile_valset = [_build_eval_example(item) for item in evaluation_inputs]
        _log(f"compile_valset_size={len(compile_valset)}")

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
            _log("optimizer=BootstrapFewShot")
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
            _log("compile_complete=true")
            strategy_summary = {
                "optimizer_class": "BootstrapFewShot",
                "max_bootstrapped_demos": max(1, int(config.get("max_bootstrapped_demos", 4))),
                "max_labeled_demos": max(1, int(config.get("max_labeled_demos", 16))),
                "teacher_lm_profile_id": config.get("teacher_lm_profile_id"),
            }
        elif normalized_strategy == "miprov2":
            _log("optimizer=MIPROv2")
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
            _log("compile_complete=true")
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
            _log("optimizer=GEPA")
            feedback_trainset, feedback_dataset_summary = _build_feedback_examples(train_records, output_fields)
            _log(f"usable_feedback_examples={len(feedback_trainset)}")
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
            ) -> dspy.Prediction:
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
                return dspy.Prediction(
                    score=float(result["score"]),
                    feedback=combined or f"This trajectory got a score of {result['score']}.",
                )

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
            _log("compile_complete=true")

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

        optimized_eval_lm = _build_execution_lm()
        if optimized_eval_lm is not None:
            _disable_lm_cache(optimized_eval_lm)

        if baseline_summary is None:
            baseline_eval_lm = _build_execution_lm()
            if baseline_eval_lm is not None:
                _disable_lm_cache(baseline_eval_lm)
            baseline_report = _evaluate_program(
                baseline_program,
                raw_metric_fn,
                evaluation_inputs,
                pass_threshold,
                baseline_eval_lm,
                phase_name="baseline_eval",
                log_event=_log,
            )
        else:
            baseline_report = {
                "score_pct": float(baseline_summary.get("score_pct") or 0.0),
                "items": [{}] * max(0, int(baseline_summary.get("item_count") or 0)),
            }
            _log("baseline_eval=reused_source_run_plan")
        optimized_report = _evaluate_program(
            compiled_program,
            raw_metric_fn,
            evaluation_inputs,
            pass_threshold,
            optimized_eval_lm,
            phase_name="optimized_eval",
            log_event=_log,
        )
        _log(f"baseline_score_pct={baseline_report['score_pct']}")
        _log(f"optimized_score_pct={optimized_report['score_pct']}")

        artifact_root = Path(artifact_dir).expanduser().resolve()
        if artifact_root.exists():
            shutil.rmtree(artifact_root)
        artifact_root.mkdir(parents=True, exist_ok=True)
        program_state_path = artifact_root / "program.json"
        _save_program_state(compiled_program, program_state_path)
        _log(f"program_state_path={program_state_path}")

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
