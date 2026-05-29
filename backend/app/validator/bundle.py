from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

MODULE_FILE = "module.py"
METRIC_FILE = "metric.py"
TOML_FILE = "bundle.toml"


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    diagnostics: list[dict[str, Any]]
    summary: str
    metadata: dict[str, Any]


def validate_bundle(bundle_path: str) -> ValidationReport:
    root = Path(bundle_path).expanduser().resolve()
    diagnostics: list[dict[str, Any]] = []

    if not root.exists():
        diagnostics.append(_diag("error", "bundle_path_missing", "Bundle path does not exist", str(root)))
        return ValidationReport(False, diagnostics, _summary(diagnostics), {})
    if not root.is_dir():
        diagnostics.append(_diag("error", "bundle_path_not_dir", "Bundle path must be a directory", str(root)))
        return ValidationReport(False, diagnostics, _summary(diagnostics), {})

    module_file = root / MODULE_FILE
    metric_file = root / METRIC_FILE
    toml_file = root / TOML_FILE
    metadata: dict[str, Any] = {}

    if not module_file.exists() or not module_file.is_file():
        diagnostics.append(_diag("error", "module_missing", "Missing required file: module.py", MODULE_FILE))
    if not metric_file.exists() or not metric_file.is_file():
        diagnostics.append(_diag("error", "metric_missing", "Missing required file: metric.py", METRIC_FILE))
    if not toml_file.exists() or not toml_file.is_file():
        diagnostics.append(_diag("error", "bundle_toml_missing", "Missing required file: bundle.toml", TOML_FILE))

    if module_file.exists() and module_file.is_file():
        _validate_module_contract(module_file, diagnostics)
    if metric_file.exists() and metric_file.is_file():
        _validate_metric_contract(metric_file, diagnostics)
    if toml_file.exists() and toml_file.is_file():
        metadata.update(_validate_bundle_toml(toml_file, diagnostics))

    passed = not any(item["severity"] == "error" for item in diagnostics)
    return ValidationReport(passed, diagnostics, _summary(diagnostics), metadata)


def _validate_module_contract(module_file: Path, diagnostics: list[dict[str, Any]]) -> None:
    tree = _parse_python(module_file, diagnostics, MODULE_FILE)
    if tree is None:
        return

    has_signature = False
    has_module_subclass = False
    has_build_program = False
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = _name_of(base)
                if name.endswith("Signature"):
                    has_signature = True
                if name.endswith("Module") or name == "dspy.Module":
                    has_module_subclass = True
        if isinstance(node, ast.FunctionDef) and node.name == "build_program":
            has_build_program = True

    if not has_signature:
        diagnostics.append(
            _diag("error", "signature_missing", "module.py must define at least one DSPy Signature", MODULE_FILE)
        )
    if not has_module_subclass:
        diagnostics.append(_diag("error", "module_missing_class", "module.py must define a dspy.Module subclass", MODULE_FILE))
    if not has_build_program:
        diagnostics.append(
            _diag("error", "build_program_missing", "module.py must expose build_program() returning a dspy.Module", MODULE_FILE)
        )


def _validate_metric_contract(metric_file: Path, diagnostics: list[dict[str, Any]]) -> None:
    tree = _parse_python(metric_file, diagnostics, METRIC_FILE)
    if tree is None:
        return

    has_instructions = False
    has_metric = False
    metric_args_ok = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "JUDGE_INSTRUCTIONS":
                    has_instructions = True
        if isinstance(node, ast.FunctionDef) and node.name == "judge_metric":
            has_metric = True
            metric_args_ok = len(node.args.args) >= 2

    if not has_instructions:
        diagnostics.append(
            _diag("error", "judge_instructions_missing", "metric.py must define JUDGE_INSTRUCTIONS string", METRIC_FILE)
        )
    if not has_metric:
        diagnostics.append(_diag("error", "judge_metric_missing", "metric.py must define judge_metric(example, prediction)", METRIC_FILE))
    elif not metric_args_ok:
        diagnostics.append(
            _diag("error", "judge_metric_signature_invalid", "judge_metric must accept at least (example, prediction)", METRIC_FILE)
        )


def _parse_python(path: Path, diagnostics: list[dict[str, Any]], label: str) -> ast.Module | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        diagnostics.append(_diag("error", "read_error", f"Unable to read file: {exc}", label))
        return None
    try:
        return ast.parse(content)
    except SyntaxError as exc:
        diagnostics.append(_diag("error", "syntax_error", f"{label} is not valid Python: {exc.msg}", label))
        return None


def _validate_bundle_toml(toml_file: Path, diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = tomllib.loads(toml_file.read_text(encoding="utf-8"))
    except OSError as exc:
        diagnostics.append(_diag("error", "bundle_toml_read_error", f"Unable to read bundle.toml: {exc}", TOML_FILE))
        return {}
    except tomllib.TOMLDecodeError as exc:
        diagnostics.append(_diag("error", "bundle_toml_invalid", f"bundle.toml is not valid TOML: {exc}", TOML_FILE))
        return {}

    required_keys = ["name", "version", "lm_target"]
    for key in required_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            diagnostics.append(_diag("error", f"bundle_toml_{key}_missing", f"bundle.toml must define a non-empty '{key}' string", TOML_FILE))
    score_pass_threshold = payload.get("score_pass_threshold")
    if isinstance(score_pass_threshold, bool) or not isinstance(score_pass_threshold, (int, float)):
        diagnostics.append(
            _diag(
                "error",
                "bundle_toml_score_pass_threshold_invalid",
                "bundle.toml must define numeric 'score_pass_threshold' between 0.0 and 1.0",
                TOML_FILE,
            )
        )
    elif float(score_pass_threshold) < 0.0 or float(score_pass_threshold) > 1.0:
        diagnostics.append(
            _diag(
                "error",
                "bundle_toml_score_pass_threshold_invalid",
                "bundle.toml 'score_pass_threshold' must be between 0.0 and 1.0",
                TOML_FILE,
            )
        )

    return {
        "name": payload.get("name"),
        "version": payload.get("version"),
        "lm_target": payload.get("lm_target"),
        "score_pass_threshold": float(score_pass_threshold) if isinstance(score_pass_threshold, (int, float)) and not isinstance(score_pass_threshold, bool) else None,
    }


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name_of(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _diag(severity: str, code: str, message: str, path: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message, "path": path}


def _summary(diagnostics: list[dict[str, Any]]) -> str:
    errors = sum(1 for d in diagnostics if d["severity"] == "error")
    if errors == 0:
        return "Validation passed."
    noun = "error" if errors == 1 else "errors"
    return f"Validation failed with {errors} {noun}."
