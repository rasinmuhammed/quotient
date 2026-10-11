"""Optional Langfuse tracing: one span per RFQ, one generation per model call, with tokens and cost.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set and the `tracing` extra is installed
(`uv sync --extra tracing`); every function here is then a no-op. Tracing never changes a result.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from quotient.config import settings


@lru_cache
def client() -> Any | None:
    s = settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        return None
    return Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key, base_url=s.langfuse_host)


@contextmanager
def span(name: str, **fields: Any) -> Iterator[Any | None]:
    lf = client()
    if lf is None:
        yield None
        return
    with lf.start_as_current_observation(name=name, as_type="chain", **fields) as obs:
        yield obs


@contextmanager
def generation(name: str, model: str, input: Any, **fields: Any) -> Iterator[Any | None]:
    lf = client()
    if lf is None:
        yield None
        return
    with lf.start_as_current_observation(name=name, as_type="generation", model=model, input=input, **fields) as gen:
        yield gen


def flush() -> None:
    lf = client()
    if lf is not None:
        lf.flush()
