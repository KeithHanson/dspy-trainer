from __future__ import annotations

from typing import Any, NoReturn

from dspy.clients import lm as dspy_lm
from dspy.clients.lm import LM
import litellm


def _rewrite_assistant_responses_content(request: dict[str, Any]) -> dict[str, Any]:
    payload = dict(request)
    input_items = payload.get("input")
    if not isinstance(input_items, list):
        return payload

    for item in input_items:
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "input_text":
                block["type"] = "output_text"
    return payload


class AzureResponsesCompatLM(LM):
    def _raise_lm_error(self, error: Exception) -> NoReturn:
        wrap = getattr(dspy_lm.LM, "_wrap_litellm_exception", None)
        if callable(wrap):
            wrapped = wrap(self, error)
            if isinstance(wrapped, BaseException):
                raise wrapped from error
        raise error

    def _responses_completion(self, request: dict[str, Any], num_retries: int, cache: dict[str, Any] | None = None):
        cache = cache or {"no-cache": True, "no-store": True}
        request = dict(request)
        request.pop("rollout_id", None)
        headers = request.pop("headers", None)
        request = dspy_lm._convert_chat_request_to_responses_request(request)
        request = _rewrite_assistant_responses_content(request)

        return litellm.responses(
            cache=cache,
            num_retries=num_retries,
            retry_strategy="exponential_backoff_retry",
            headers=dspy_lm._add_dspy_identifier_to_headers(headers),
            **request,
        )

    async def _aresponses_completion(self, request: dict[str, Any], num_retries: int, cache: dict[str, Any] | None = None):
        cache = cache or {"no-cache": True, "no-store": True}
        request = dict(request)
        request.pop("rollout_id", None)
        headers = request.pop("headers", None)
        request = dspy_lm._convert_chat_request_to_responses_request(request)
        request = _rewrite_assistant_responses_content(request)

        return await litellm.aresponses(
            cache=cache,
            num_retries=num_retries,
            retry_strategy="exponential_backoff_retry",
            headers=dspy_lm._add_dspy_identifier_to_headers(headers),
            **request,
        )

    def forward(self, prompt: str | None = None, messages: list[dict[str, Any]] | None = None, **kwargs):
        if self.model_type != "responses":
            return super().forward(prompt=prompt, messages=messages, **kwargs)

        kwargs = dict(kwargs)
        cache = kwargs.pop("cache", self.cache)
        messages = messages or [{"role": "user", "content": prompt}]
        if self.use_developer_role:
            messages = [{**m, "role": "developer"} if m.get("role") == "system" else m for m in messages]
        kwargs = {**self.kwargs, **kwargs}
        self._warn_zero_temp_rollout(kwargs.get("temperature"), kwargs.get("rollout_id"))
        if kwargs.get("rollout_id") is None:
            kwargs.pop("rollout_id", None)

        completion = self._responses_completion
        completion, litellm_cache_args = self._get_cached_completion_fn(completion, cache)
        results = None
        try:
            results = completion(
                request=dict(model=self.model, messages=messages, **kwargs),
                num_retries=self.num_retries,
                cache=litellm_cache_args,
            )
        except Exception as e:
            self._raise_lm_error(e)

        assert results is not None
        self._check_truncation(results)
        return results

    async def aforward(self, prompt: str | None = None, messages: list[dict[str, Any]] | None = None, **kwargs):
        if self.model_type != "responses":
            return await super().aforward(prompt=prompt, messages=messages, **kwargs)

        kwargs = dict(kwargs)
        cache = kwargs.pop("cache", self.cache)
        messages = messages or [{"role": "user", "content": prompt}]
        if self.use_developer_role:
            messages = [{**m, "role": "developer"} if m.get("role") == "system" else m for m in messages]
        kwargs = {**self.kwargs, **kwargs}
        self._warn_zero_temp_rollout(kwargs.get("temperature"), kwargs.get("rollout_id"))
        if kwargs.get("rollout_id") is None:
            kwargs.pop("rollout_id", None)

        completion = self._aresponses_completion
        completion, litellm_cache_args = self._get_cached_completion_fn(completion, cache)
        results = None
        try:
            results = await completion(
                request=dict(model=self.model, messages=messages, **kwargs),
                num_retries=self.num_retries,
                cache=litellm_cache_args,
            )
        except Exception as e:
            self._raise_lm_error(e)

        assert results is not None
        self._check_truncation(results)
        return results
