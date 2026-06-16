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


def read_bundle_metadata(bundle_path: str) -> dict[str, Any]:
    root = Path(bundle_path).expanduser().resolve()
    toml_file = root / TOML_FILE
    payload = _load_bundle_toml_payload(toml_file, diagnostics=None)
    if payload is None:
        return {}
    return _extract_bundle_metadata(payload, toml_file.parent, diagnostics=None)


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
    build_lm_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "build_lm":
            build_lm_node = node

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
    if build_lm_node is not None:
        if not _has_zero_required_args(build_lm_node):
            diagnostics.append(
                _diag(
                    "error",
                    "build_lm_signature_invalid",
                    "build_lm() must not require positional arguments when defined",
                    MODULE_FILE,
                )
            )


def _validate_metric_contract(metric_file: Path, diagnostics: list[dict[str, Any]]) -> None:
    tree = _parse_python(metric_file, diagnostics, METRIC_FILE)
    if tree is None:
        return

    has_metric = False
    metric_args_ok = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "judge_metric":
            has_metric = True
            metric_args_ok = len(node.args.args) >= 2

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
    payload = _load_bundle_toml_payload(toml_file, diagnostics)
    if payload is None:
        return {}

    return _extract_bundle_metadata(payload, toml_file.parent, diagnostics)


def _load_bundle_toml_payload(
    toml_file: Path,
    diagnostics: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    try:
        return tomllib.loads(toml_file.read_text(encoding="utf-8"))
    except OSError as exc:
        _append_diag(diagnostics, "error", "bundle_toml_read_error", f"Unable to read bundle.toml: {exc}", TOML_FILE)
        return None
    except tomllib.TOMLDecodeError as exc:
        _append_diag(diagnostics, "error", "bundle_toml_invalid", f"bundle.toml is not valid TOML: {exc}", TOML_FILE)
        return None


def _extract_bundle_metadata(
    payload: dict[str, Any],
    bundle_root: Path,
    diagnostics: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    required_keys = ["name", "version"]
    for key in required_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_{key}_missing",
                f"bundle.toml must define a non-empty '{key}' string",
                TOML_FILE,
            )
    score_pass_threshold = payload.get("score_pass_threshold")
    if isinstance(score_pass_threshold, bool) or not isinstance(score_pass_threshold, (int, float)):
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_score_pass_threshold_invalid",
            "bundle.toml must define numeric 'score_pass_threshold' between 0.0 and 1.0",
            TOML_FILE,
        )
    elif float(score_pass_threshold) < 0.0 or float(score_pass_threshold) > 1.0:
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_score_pass_threshold_invalid",
            "bundle.toml 'score_pass_threshold' must be between 0.0 and 1.0",
            TOML_FILE,
        )

    optimized_program_state = payload.get("optimized_program_state")
    if optimized_program_state is not None:
        if not isinstance(optimized_program_state, str) or not optimized_program_state.strip():
            _append_diag(
                diagnostics,
                "error",
                "bundle_toml_optimized_program_state_invalid",
                "bundle.toml optional 'optimized_program_state' must be a non-empty string when provided",
                TOML_FILE,
            )
        else:
            state_path = bundle_root / optimized_program_state.strip()
            if not state_path.exists() or not state_path.is_file():
                _append_diag(
                    diagnostics,
                    "error",
                    "optimized_program_state_missing",
                    "optimized_program_state file referenced by bundle.toml does not exist",
                    optimized_program_state.strip(),
                )

    evaluation_contract = _extract_evaluation_contract(payload.get("evaluation"), diagnostics)
    system_dependency_commands = _extract_system_dependency_commands(payload.get("runtime"), diagnostics)
    optimization_config = _extract_optimization_config(payload.get("optimization"), diagnostics)

    return {
        "name": payload.get("name"),
        "version": payload.get("version"),
        "score_pass_threshold": float(score_pass_threshold) if isinstance(score_pass_threshold, (int, float)) and not isinstance(score_pass_threshold, bool) else None,
        "optimized_program_state": optimized_program_state.strip() if isinstance(optimized_program_state, str) and optimized_program_state.strip() else None,
        "evaluation_contract": evaluation_contract,
        "system_dependency_commands": system_dependency_commands,
        "optimization": optimization_config,
    }


def _extract_system_dependency_commands(
    runtime_payload: Any,
    diagnostics: list[dict[str, Any]] | None,
) -> list[str]:
    if runtime_payload is None:
        return []
    if not isinstance(runtime_payload, dict):
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_runtime_invalid",
            "bundle.toml optional 'runtime' section must be a table when provided",
            TOML_FILE,
        )
        return []

    raw_commands = runtime_payload.get("system_dependency_commands")
    if raw_commands is None:
        return []
    if not isinstance(raw_commands, list) or any(not isinstance(item, str) or not item.strip() for item in raw_commands):
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_runtime_system_dependency_commands_invalid",
            "bundle.toml runtime.system_dependency_commands must be an array of non-empty strings when provided",
            TOML_FILE,
        )
        return []
    return [item.strip() for item in raw_commands]


def _extract_optimization_config(
    optimization_payload: Any,
    diagnostics: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if optimization_payload is None:
        return {"target_output_fields": None}
    if not isinstance(optimization_payload, dict):
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_optimization_invalid",
            "bundle.toml optional 'optimization' section must be a table when provided",
            TOML_FILE,
        )
        return {"target_output_fields": None}

    raw_target_output_fields = optimization_payload.get("target_output_fields")
    if raw_target_output_fields is None:
        return {"target_output_fields": None}
    if not isinstance(raw_target_output_fields, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_target_output_fields
    ):
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_optimization_target_output_fields_invalid",
            "bundle.toml optimization.target_output_fields must be an array of non-empty strings when provided",
            TOML_FILE,
        )
        return {"target_output_fields": None}

    target_output_fields: list[str] = []
    for item in raw_target_output_fields:
        normalized = item.strip()
        if normalized not in target_output_fields:
            target_output_fields.append(normalized)
    return {"target_output_fields": target_output_fields}


def _extract_evaluation_contract(
    evaluation_payload: Any,
    diagnostics: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if evaluation_payload is None:
        return None
    if not isinstance(evaluation_payload, dict):
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_evaluation_invalid",
            "bundle.toml optional 'evaluation' section must be a table when provided",
            TOML_FILE,
        )
        return None

    input_fields = _extract_contract_fields(evaluation_payload.get("input"), "input", diagnostics)
    label_fields = _extract_contract_fields(evaluation_payload.get("label"), "label", diagnostics)
    if not input_fields and not label_fields:
        _append_diag(
            diagnostics,
            "error",
            "bundle_toml_evaluation_empty",
            "bundle.toml optional 'evaluation' section must define input.fields or label.fields when provided",
            TOML_FILE,
        )

    return {
        "input_fields": input_fields,
        "label_fields": label_fields,
        "input_template": {field["key"]: "" for field in input_fields},
        "label_template": {field["key"]: "" for field in label_fields},
    }


def _extract_contract_fields(
    section_payload: Any,
    section_name: str,
    diagnostics: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if section_payload is None:
        return []
    if not isinstance(section_payload, dict):
        _append_diag(
            diagnostics,
            "error",
            f"bundle_toml_evaluation_{section_name}_invalid",
            f"bundle.toml evaluation.{section_name} must be a table when provided",
            TOML_FILE,
        )
        return []

    raw_fields = section_payload.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        _append_diag(
            diagnostics,
            "error",
            f"bundle_toml_evaluation_{section_name}_fields_invalid",
            f"bundle.toml evaluation.{section_name}.fields must be a non-empty array of field definitions",
            TOML_FILE,
        )
        return []

    fields: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(raw_fields):
        if not isinstance(item, dict):
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_evaluation_{section_name}_field_invalid",
                f"bundle.toml evaluation.{section_name}.fields[{index}] must be a table",
                TOML_FILE,
            )
            continue

        key = item.get("key")
        if not isinstance(key, str) or not key.strip():
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_evaluation_{section_name}_field_key_missing",
                f"bundle.toml evaluation.{section_name}.fields[{index}] must define a non-empty key",
                TOML_FILE,
            )
            continue
        normalized_key = key.strip()
        if normalized_key in seen_keys:
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_evaluation_{section_name}_field_key_duplicate",
                f"bundle.toml evaluation.{section_name}.fields keys must be unique; duplicate '{normalized_key}' found",
                TOML_FILE,
            )
            continue
        seen_keys.add(normalized_key)

        label = item.get("label")
        description = item.get("description")
        required = item.get("required", True)
        multiline = item.get("multiline", False)
        if label is not None and (not isinstance(label, str) or not label.strip()):
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_evaluation_{section_name}_field_label_invalid",
                f"bundle.toml evaluation.{section_name}.fields[{index}] label must be a non-empty string when provided",
                TOML_FILE,
            )
            label = None
        if description is not None and not isinstance(description, str):
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_evaluation_{section_name}_field_description_invalid",
                f"bundle.toml evaluation.{section_name}.fields[{index}] description must be a string when provided",
                TOML_FILE,
            )
            description = None
        if not isinstance(required, bool):
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_evaluation_{section_name}_field_required_invalid",
                f"bundle.toml evaluation.{section_name}.fields[{index}] required must be a boolean when provided",
                TOML_FILE,
            )
            required = True
        if not isinstance(multiline, bool):
            _append_diag(
                diagnostics,
                "error",
                f"bundle_toml_evaluation_{section_name}_field_multiline_invalid",
                f"bundle.toml evaluation.{section_name}.fields[{index}] multiline must be a boolean when provided",
                TOML_FILE,
            )
            multiline = False

        fields.append(
            {
                "key": normalized_key,
                "label": label.strip() if isinstance(label, str) and label.strip() else normalized_key,
                "description": description.strip() if isinstance(description, str) and description.strip() else None,
                "required": required,
                "multiline": multiline,
            }
        )
    return fields


def _append_diag(
    diagnostics: list[dict[str, Any]] | None,
    severity: str,
    code: str,
    message: str,
    path: str,
) -> None:
    if diagnostics is None:
        return
    diagnostics.append(_diag(severity, code, message, path))


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name_of(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _has_zero_required_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    required_positional = len(positional) - len(args.defaults)
    has_required_kwonly = any(default is None for default in args.kw_defaults)
    return required_positional == 0 and not has_required_kwonly


def _diag(severity: str, code: str, message: str, path: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message, "path": path}


def _summary(diagnostics: list[dict[str, Any]]) -> str:
    errors = sum(1 for d in diagnostics if d["severity"] == "error")
    if errors == 0:
        return "Validation passed."
    noun = "error" if errors == 1 else "errors"
    return f"Validation failed with {errors} {noun}."
