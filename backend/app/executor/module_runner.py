from __future__ import annotations

import importlib.util
from pathlib import Path
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


def run_bundle_eval(bundle_path: str, eval_inputs: list[dict[str, Any]], num_threads: int = 1) -> dict[str, Any]:
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

    program = module_mod.build_program()
    lm = module_mod.build_lm() if hasattr(module_mod, "build_lm") else None
    if lm is not None:
        _disable_lm_cache(lm)
    raw_metric_fn = metric_mod.judge_metric

    devset = []
    for item in eval_inputs:
        input_payload = item.get("input", {})
        label_payload = item.get("label", {})
        ex = dspy.Example(**input_payload, label=label_payload).with_inputs(*input_payload.keys())
        devset.append(ex)

    predictions: list[Any] = []
    for example in devset:
        inputs = example.inputs().toDict()
        if lm is not None:
            with dspy.context(lm=lm):
                prediction = program(**inputs)
        else:
            prediction = program(**inputs)
        predictions.append(prediction)

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
        "judge_instructions": getattr(metric_mod, "JUDGE_INSTRUCTIONS", ""),
    }
