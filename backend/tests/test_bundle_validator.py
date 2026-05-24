import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.validator import validate_bundle


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "module_bundles"


def _diag_codes(report) -> set[str]:
    return {diag["code"] for diag in report.diagnostics}


def test_validator_accepts_valid_bundle():
    report = validate_bundle(str(FIXTURES / "valid_bundle"))
    assert report.passed is True
    assert report.diagnostics == []
    assert report.summary == "Validation passed."


def test_validator_reports_missing_files():
    report = validate_bundle(str(FIXTURES / "missing_files"))
    assert report.passed is False
    assert {"module_missing", "metric_missing"}.issubset(_diag_codes(report))


def test_validator_reports_invalid_contract_shapes():
    report = validate_bundle(str(FIXTURES / "wrong_docker_base"))
    assert report.passed is False
    assert "module_missing_class" in _diag_codes(report)
    assert "build_program_missing" in _diag_codes(report)
    assert "judge_metric_signature_invalid" in _diag_codes(report)


def test_validator_reports_missing_signature_and_instructions():
    report = validate_bundle(str(FIXTURES / "missing_eval_signature"))
    assert report.passed is False
    assert "signature_missing" in _diag_codes(report)
    assert "judge_instructions_missing" in _diag_codes(report)
