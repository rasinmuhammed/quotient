"""The language model, behind one narrow interface: a system prompt and a document in, schema-valid JSON out.

The model never sees a price and never returns a number the pipeline uses unchecked. Every call records its
tokens, cache hits, latency and cost, so the evals can report cost per RFQ.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from quotient import tracing
from quotient.config import settings

# USD per million tokens: input, cached input, output. From Groq's model pages (console.groq.com/docs/model/...),
# checked on PRICES_CHECKED. Cached input is half price (Groq prompt-caching docs; the 20B page shows $0.037).
# Reasoning tokens are reported inside completion tokens and assumed billed as output (Groq does not say).
PRICES_CHECKED = "2026-10-11"
PRICES: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "openai/gpt-oss-120b": (Decimal("0.15"), Decimal("0.075"), Decimal("0.60")),
    "openai/gpt-oss-20b": (Decimal("0.075"), Decimal("0.0375"), Decimal("0.30")),
    "qwen/qwen3.8-27b": (Decimal("0.80"), Decimal("0.80"), Decimal("4.00")),  # no prompt caching on this model
}
MILLION = Decimal(1_000_000)


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMCall:
    name: str
    model: str
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    seconds: float  # working time, rate-limit waits excluded
    cost_usd: Decimal
    request_id: str | None = None
    waited_seconds: float = 0.0  # time spent waiting out rate limits and retrying transient errors


def cost_of(model: str, prompt: int, cached: int, completion: int) -> Decimal:
    p_in, p_cached, p_out = PRICES.get(model, (Decimal(0), Decimal(0), Decimal(0)))
    return ((prompt - cached) * p_in + cached * p_cached + completion * p_out) / MILLION


class LLM(Protocol):
    model: str

    def json(self, *, name: str, schema: dict[str, Any], system: str, user: str) -> tuple[dict[str, Any], LLMCall]: ...


class GroqLLM:
    """Groq chat completions with strict JSON-schema output (constrained decoding on gpt-oss models).

    Prompt caching on Groq is automatic exact-prefix matching, so callers keep the static part of the prompt
    (instructions and catalog glossary) first and byte-identical across calls. Retries are done here rather than
    in the SDK so the time spent waiting on rate limits is measured apart from the time the model works.
    """

    RETRYABLE = ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError")

    def __init__(self, api_key: str, model: str, reasoning_effort: str = "medium", timeout: float = 90.0,
                 max_retries: int = 8) -> None:
        from groq import Groq

        if not api_key:
            raise LLMError("GROQ_API_KEY is not set; copy .env.example to .env and add a key")
        self.client = Groq(api_key=api_key, timeout=timeout, max_retries=0)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_retries = max_retries
        self.strict = model.startswith(("openai/gpt-oss", "qwen/qwen3.8"))  # Groq's strict (constrained) models

    @staticmethod
    def _retry_after(exc: Exception, attempt: int) -> float:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        try:
            return min(max(float(headers.get("retry-after", "")), 0.5), 60.0)
        except ValueError:
            return min(2.0 ** attempt, 30.0)

    def json(self, *, name: str, schema: dict[str, Any], system: str, user: str) -> tuple[dict[str, Any], LLMCall]:
        with tracing.generation(name, self.model, input=[{"role": "system", "content": system},
                                                         {"role": "user", "content": user}],
                                model_parameters={"reasoning_effort": self.reasoning_effort, "temperature": 0}) as gen:
            data, call = self._json(name=name, schema=schema, system=system, user=user)
            if gen is not None:
                gen.update(output=data, usage_details={"input": call.prompt_tokens - call.cached_tokens,
                                                       "input_cached": call.cached_tokens,
                                                       "output": call.completion_tokens,
                                                       "output_reasoning": call.reasoning_tokens},
                           cost_details={"total": float(call.cost_usd)},
                           metadata={"waited_seconds": call.waited_seconds, "request_id": call.request_id})
        return data, call

    def _json(self, *, name: str, schema: dict[str, Any], system: str, user: str) -> tuple[dict[str, Any], LLMCall]:
        import groq

        kwargs: dict[str, Any] = {}
        if self.model.startswith("openai/gpt-oss"):
            kwargs.update(reasoning_effort=self.reasoning_effort, include_reasoning=False)
        elif self.model.startswith("qwen/"):  # "none" is instruct mode; other efforts think, with the thinking hidden
            kwargs.update(reasoning_effort=self.reasoning_effort)
            if self.reasoning_effort != "none":
                kwargs.update(reasoning_format="hidden")
        started, waited = time.perf_counter(), 0.0
        for attempt in range(self.max_retries + 1):
            try:
                r = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_schema",
                                     "json_schema": {"name": name, "strict": self.strict, "schema": schema}},
                    temperature=0,
                    seed=7,
                    **kwargs,
                )
                break
            except groq.APIError as exc:
                # an answer that failed Groq's schema check is sampled again, once
                retry = type(exc).__name__ in self.RETRYABLE or (attempt == 0 and "json_validate_failed" in str(exc))
                if not retry or attempt == self.max_retries:
                    raise LLMError(f"{name}: {type(exc).__name__}: {exc}") from exc
                pause = self._retry_after(exc, attempt)
                time.sleep(pause)
                waited += pause
        seconds = time.perf_counter() - started - waited
        choice = r.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"{name}: output hit the token limit")
        try:
            data = json.loads(choice.message.content or "")
        except json.JSONDecodeError as exc:
            raise LLMError(f"{name}: model returned invalid JSON: {exc}") from exc
        u = r.usage
        prompt = u.prompt_tokens if u else 0
        completion = u.completion_tokens if u else 0
        cached = (u.prompt_tokens_details.cached_tokens or 0) if u and u.prompt_tokens_details else 0
        reasoning = (u.completion_tokens_details.reasoning_tokens or 0) if u and u.completion_tokens_details else 0
        call = LLMCall(name=name, model=self.model, prompt_tokens=prompt, cached_tokens=cached,
                       completion_tokens=completion, reasoning_tokens=reasoning, seconds=round(seconds, 3),
                       cost_usd=cost_of(self.model, prompt, cached, completion),
                       request_id=r.x_groq.id if getattr(r, "x_groq", None) else None, waited_seconds=round(waited, 3))
        return data, call


class ScriptedLLM:
    """A stand-in for tests: answers each named call from a function of the prompt, records calls like the real one."""

    def __init__(self, answers: dict[str, Callable[[str, str], dict[str, Any]]], model: str = "scripted") -> None:
        self.answers = answers
        self.model = model
        self.calls: list[tuple[str, str, str]] = []

    def json(self, *, name: str, schema: dict[str, Any], system: str, user: str) -> tuple[dict[str, Any], LLMCall]:
        self.calls.append((name, system, user))
        if name not in self.answers:
            raise LLMError(f"no scripted answer for {name}")
        return self.answers[name](system, user), LLMCall(name, self.model, 0, 0, 0, 0, 0.0, Decimal(0))


def default_llm(model: str | None = None) -> GroqLLM:
    s = settings()
    return GroqLLM(api_key=s.groq_api_key, model=model or s.quotient_model, reasoning_effort=s.quotient_reasoning_effort)
