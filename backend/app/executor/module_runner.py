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
    metric_fn = metric_mod.judge_metric

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
        normalized.append(
            {
                "item_index": idx,
                "score": 1.0 if score is True else (0.0 if score is False else float(score)),
                "input": example.inputs().toDict(),
                "label": example.labels().toDict(),
                "prediction": prediction.toDict() if hasattr(prediction, "toDict") else {"value": str(prediction)},
            }
        )

    return {
        "score_pct": result.score,
        "items": normalized,
        "judge_instructions": getattr(metric_mod, "JUDGE_INSTRUCTIONS", ""),
    }
