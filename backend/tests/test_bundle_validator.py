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


def test_validator_reports_missing_signature():
    report = validate_bundle(str(FIXTURES / "missing_eval_signature"))
    assert report.passed is False
    assert "signature_missing" in _diag_codes(report)


def test_validator_accepts_optional_build_lm_with_no_args(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class Sig(dspy.Signature):\n"
        "  q=dspy.InputField()\n"
        "  a=dspy.OutputField()\n"
        "class Agent(dspy.Module):\n"
        "  def forward(self, q: str):\n"
        "    return dspy.Prediction(a='x')\n"
        "def build_program():\n"
        "  return Agent()\n"
        "def build_lm():\n"
        "  return dspy.LM(model='openai/codex-5.3', api_base='http://localhost:4000', api_key='sk-test')\n",
        encoding="utf-8",
    )
    (bundle / "metric.py").write_text(
        "def judge_metric(example, prediction, trace=None):\n"
        "  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (bundle / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nlm_target='x'\nscore_pass_threshold=0.8\n",
        encoding="utf-8",
    )
    report = validate_bundle(str(bundle))
    assert report.passed is True
    assert "build_lm_signature_invalid" not in _diag_codes(report)


def test_validator_rejects_build_lm_with_required_args(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class Sig(dspy.Signature):\n"
        "  q=dspy.InputField()\n"
        "  a=dspy.OutputField()\n"
        "class Agent(dspy.Module):\n"
        "  def forward(self, q: str):\n"
        "    return dspy.Prediction(a='x')\n"
        "def build_program():\n"
        "  return Agent()\n"
        "def build_lm(model_name):\n"
        "  return dspy.LM(model=model_name)\n",
        encoding="utf-8",
    )
    (bundle / "metric.py").write_text(
        "def judge_metric(example, prediction, trace=None):\n"
        "  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (bundle / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nlm_target='x'\nscore_pass_threshold=0.8\n",
        encoding="utf-8",
    )
    report = validate_bundle(str(bundle))
    assert report.passed is False
    assert "build_lm_signature_invalid" in _diag_codes(report)


def test_validator_accepts_optimized_program_state_file(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class Sig(dspy.Signature):\n"
        "  q=dspy.InputField()\n"
        "  a=dspy.OutputField()\n"
        "class Agent(dspy.Module):\n"
        "  def forward(self, q: str):\n"
        "    return dspy.Prediction(a='x')\n"
        "def build_program():\n"
        "  return Agent()\n",
        encoding="utf-8",
    )
    (bundle / "metric.py").write_text(
        "def judge_metric(example, prediction, trace=None):\n"
        "  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (bundle / "program.json").write_text("{}", encoding="utf-8")
    (bundle / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nlm_target='x'\nscore_pass_threshold=0.8\noptimized_program_state='program.json'\n",
        encoding="utf-8",
    )
    report = validate_bundle(str(bundle))
    assert report.passed is True
    assert report.metadata["optimized_program_state"] == "program.json"


def test_validator_rejects_missing_optimized_program_state_file(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class Sig(dspy.Signature):\n"
        "  q=dspy.InputField()\n"
        "  a=dspy.OutputField()\n"
        "class Agent(dspy.Module):\n"
        "  def forward(self, q: str):\n"
        "    return dspy.Prediction(a='x')\n"
        "def build_program():\n"
        "  return Agent()\n",
        encoding="utf-8",
    )
    (bundle / "metric.py").write_text(
        "def judge_metric(example, prediction, trace=None):\n"
        "  return {'score': 1.0, 'rationale': 'ok', 'flags': [], 'raw_response': {}}\n",
        encoding="utf-8",
    )
    (bundle / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nlm_target='x'\nscore_pass_threshold=0.8\noptimized_program_state='program.json'\n",
        encoding="utf-8",
    )
    report = validate_bundle(str(bundle))
    assert report.passed is False
    assert "optimized_program_state_missing" in _diag_codes(report)
