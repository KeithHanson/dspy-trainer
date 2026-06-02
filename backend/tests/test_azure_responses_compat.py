import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.lm.azure_responses_compat import _rewrite_assistant_responses_content


def test_rewrite_assistant_responses_content_uses_output_text_for_assistant_turns():
    payload = {
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            {"role": "assistant", "content": [{"type": "input_text", "text": "world"}]},
        ]
    }

    rewritten = _rewrite_assistant_responses_content(payload)

    assert rewritten["input"][0]["content"][0]["type"] == "input_text"
    assert rewritten["input"][1]["content"][0]["type"] == "output_text"
