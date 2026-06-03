import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.executor import module_runner
from app.executor.module_runner import run_bundle_eval, run_bundle_optimization


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "module_bundles"


def test_run_bundle_eval_returns_items():
    result = run_bundle_eval(
        bundle_path=str(FIXTURES / "valid_bundle"),
        eval_inputs=[
            {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}},
        ],
        num_threads=1,
    )
    assert "score_pct" in result
    assert len(result["items"]) == 1
    assert result["items"][0]["input"]["question"] == "France capital?"
    assert result["items"][0]["rationale"] == "exact_match"
    assert result["items"][0]["flags"] == []
    assert result["items"][0]["raw_response"]["got"] == "Paris"


def test_run_bundle_eval_rejects_legacy_metric_return_type():
    with pytest.raises(Exception) as exc_info:
        run_bundle_eval(
            bundle_path=str(FIXTURES / "missing_eval_signature"),
            eval_inputs=[
                {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}},
            ],
            num_threads=1,
        )
    msg = str(exc_info.value)
    assert "judge_metric must return a dict" in msg or "unsupported operand type" in msg


def test_run_bundle_eval_uses_lm_profile_when_build_lm_absent(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "module.py").write_text(
        "import dspy\n"
        "class QA(dspy.Signature):\n"
        "  question = dspy.InputField()\n"
        "  answer = dspy.OutputField()\n"
        "class Program(dspy.Module):\n"
        "  def __init__(self):\n"
        "    super().__init__()\n"
        "    self.predict = dspy.Predict(QA)\n"
        "  def forward(self, question: str):\n"
        "    return self.predict(question=question)\n"
        "def build_program():\n"
        "  return Program()\n",
        encoding="utf-8",
    )
    (bundle / "metric.py").write_text(
        "def judge_metric(example, prediction):\n"
        "  expected = str(example.label.get('expected', ''))\n"
        "  got = str(prediction.answer)\n"
        "  matched = expected == got\n"
        "  return {'score': 1.0 if matched else 0.0, 'rationale': 'exact_match' if matched else 'mismatch', 'flags': [] if matched else ['answer_mismatch'], 'raw_response': {'expected': expected, 'got': got}}\n",
        encoding="utf-8",
    )
    (bundle / "bundle.toml").write_text(
        "name='x'\nversion='0.1.0'\nlm_target='x'\nscore_pass_threshold=0.8\n",
        encoding="utf-8",
    )

    result = run_bundle_eval(
        bundle_path=str(bundle),
        eval_inputs=[{"input": {"question": "France capital?"}, "label": {"expected": "Paris"}}],
        num_threads=1,
        lm_profile={
            "model": "dummy",
            "api_base": "http://unused",
            "model_type": "chat",
            "lm_class_path": "dspy.utils.DummyLM",
            "default_params": {"answers": [{"answer": "Paris"}]},
        },
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["score"] == 1.0


def test_run_bundle_eval_prefers_module_build_lm_over_profile():
    result = run_bundle_eval(
        bundle_path=str(FIXTURES / "valid_bundle"),
        eval_inputs=[
            {"input": {"question": "France capital?"}, "label": {"expected": "Paris"}},
        ],
        num_threads=1,
        lm_profile={
            "model": "dummy",
            "api_base": "http://unused",
            "model_type": "chat",
            "lm_class_path": "dspy.utils.DummyLM",
            "default_params": {"answers": [{"answer": "London"}]},
        },
    )
    assert result["items"][0]["score"] == 1.0


def test_build_lm_profile_alias_omits_upstream_api_base(monkeypatch):
    captured: dict[str, object] = {}

    class CaptureLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module_runner, "_load_class", lambda _class_path: CaptureLM)

    module_runner._build_lm_from_profile(
        {
            "id": "profile-123",
            "model": "azure/some-deployment",
            "api_base": "https://example.cognitiveservices.azure.com",
            "proxy_api_base": "http://litellm-proxy:4000",
            "virtual_key": "sk-virtual-123",
            "model_type": "responses",
            "lm_class_path": "ignored.path.CaptureLM",
            "default_params": {},
        }
    )

    assert captured["model"] == "openai/lm-profile:profile-123"
    assert captured["api_key"] == "sk-virtual-123"
    assert captured["api_base"] == "http://litellm-proxy:4000"


def test_build_lm_profile_alias_uses_default_proxy_when_not_provided(monkeypatch):
    captured: dict[str, object] = {}

    class CaptureLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module_runner, "_load_class", lambda _class_path: CaptureLM)

    module_runner._build_lm_from_profile(
        {
            "id": "profile-456",
            "model": "azure/some-deployment",
            "api_base": "https://example.cognitiveservices.azure.com",
            "virtual_key": "sk-virtual-456",
            "model_type": "responses",
            "lm_class_path": "ignored.path.CaptureLM",
            "default_params": {},
        }
    )

    assert captured["model"] == "openai/lm-profile:profile-456"
    assert captured["api_key"] == "sk-virtual-456"
    assert captured["api_base"] == "http://litellm-proxy:4000"


def test_build_lm_profile_uses_azure_responses_compat_class_by_default(monkeypatch):
    captured = {}

    class CaptureLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    def fake_load_class(class_path):
        captured["class_path"] = class_path
        return CaptureLM

    monkeypatch.setattr(module_runner, "_load_class", fake_load_class)

    module_runner._build_lm_from_profile(
        {
            "id": "profile-789",
            "model": "azure/codex-5.3-eval-deployment-1",
            "api_base": "https://example.cognitiveservices.azure.com",
            "virtual_key": "sk-virtual-789",
            "model_type": "responses",
            "default_params": {},
        }
    )

    assert captured["class_path"] == module_runner.AZURE_RESPONSES_COMPAT_CLASS_PATH


def test_run_bundle_optimization_reuses_source_baseline(monkeypatch, tmp_path):
    phases: list[str] = []

    def fake_evaluate_program(program, raw_metric_fn, eval_inputs, pass_threshold, lm, phase_name="eval", log_event=None):
        del program, raw_metric_fn, eval_inputs, pass_threshold, lm, log_event
        phases.append(phase_name)
        return {"score_pct": 100.0, "items": [{}]}

    monkeypatch.setattr(module_runner, "_evaluate_program", fake_evaluate_program)

    result = run_bundle_optimization(
        bundle_path=str(FIXTURES / "valid_bundle"),
        strategy="bootstrap_fewshot",
        train_records=[
            {
                "input": {"question": "France capital?"},
                "label": {"expected": "Paris"},
                "prediction": {"answer": "Paris"},
            }
        ],
        val_inputs=[],
        artifact_dir=str(tmp_path / "artifacts"),
        num_threads=1,
        baseline_summary={"score_pct": 50.0, "item_count": 3},
    )

    assert phases == ["optimized_eval"]
    assert result["comparison_summary"]["baseline_score_pct"] == 50.0
    assert result["comparison_summary"]["baseline_item_count"] == 3
    assert result["comparison_summary"]["optimized_score_pct"] == 100.0
    assert result["comparison_summary"]["score_delta_pct"] == 50.0
