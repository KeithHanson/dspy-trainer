from __future__ import annotations

import importlib.util
from pathlib import Path
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


def run_bundle_eval(bundle_path: str, eval_inputs: list[dict[str, Any]], num_threads: int = 1) -> dict[str, Any]:
    root = Path(bundle_path).expanduser().resolve()
    module_mod = _load_module("user_module", root / "module.py")
    metric_mod = _load_module("user_metric", root / "metric.py")

    if not hasattr(module_mod, "build_program"):
        raise RuntimeError("module.py must define build_program()")
    if not hasattr(metric_mod, "judge_metric"):
        raise RuntimeError("metric.py must define judge_metric(example, prediction)")

    program = module_mod.build_program()
    lm = module_mod.build_lm() if hasattr(module_mod, "build_lm") else None
    raw_metric_fn = metric_mod.judge_metric

    metric_call_index = [0]
    judge_results: list[dict[str, Any]] = []

    def metric_fn(example: Any, prediction: Any) -> float:
        item_index = metric_call_index[0]
        normalized = _normalize_judge_result(raw_metric_fn(example, prediction), item_index)
        judge_results.append(normalized)
        metric_call_index[0] = item_index + 1
        return normalized["score"]

    devset = []
    for item in eval_inputs:
        input_payload = item.get("input", {})
        label_payload = item.get("label", {})
        ex = dspy.Example(**input_payload, label=label_payload).with_inputs(*input_payload.keys())
        devset.append(ex)

    evaluator = dspy.Evaluate(
        devset=devset,
        metric=metric_fn,
        num_threads=max(1, int(num_threads)),
        display_progress=False,
        display_table=False,
    )
    if lm is not None:
        with dspy.context(lm=lm):
            result = evaluator(program)
    else:
        result = evaluator(program)

    normalized = []
    for idx, (example, prediction, score) in enumerate(result.results):
        judge_result = judge_results[idx] if idx < len(judge_results) else _normalize_judge_result(raw_metric_fn(example, prediction), idx)
        normalized.append(
            {
                "item_index": idx,
                "score": judge_result["score"],
                "input": example.inputs().toDict(),
                "label": example.labels().toDict(),
                "prediction": prediction.toDict() if hasattr(prediction, "toDict") else {"value": str(prediction)},
                "rationale": judge_result["rationale"],
                "flags": judge_result["flags"],
                "raw_response": judge_result["raw_response"],
            }
        )

    return {
        "score_pct": result.score,
        "items": normalized,
        "judge_instructions": getattr(metric_mod, "JUDGE_INSTRUCTIONS", ""),
    }
