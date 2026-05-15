"""In-process structured tracing.

Each request gets a Trace. Every stage opens a Span on that trace via the
`span(...)` async context manager. Spans collect timing, inputs, outputs, and
errors. The Trace is flushed at the end of the request: persisted to the state
store and made available through the API for the live trace viewer.

We deliberately avoid OpenTelemetry here because the goal is to ship a complete
working system with zero external collectors. The Trace model can be swapped
for an OTel exporter later without changing call sites.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog

_log = structlog.get_logger("concord.trace")
_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "concord_current_trace", default=None
)


@dataclass
class Span:
    name: str
    started_at: float
    ended_at: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    parent: str | None = None
    span_id: str = field(default_factory=lambda: uuid4().hex[:12])

    @property
    def duration_ms(self) -> int:
        if self.ended_at is None:
            return 0
        return int((self.ended_at - self.started_at) * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "parent": self.parent,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "error": self.error,
        }


@dataclass
class Trace:
    trace_id: str
    request_id: str
    customer_id: str | None
    started_at: datetime
    spans: list[Span] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    _stack: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "customer_id": self.customer_id,
            "started_at": self.started_at.isoformat(),
            "metadata": self.metadata,
            "spans": [s.to_dict() for s in self.spans],
        }


class Tracer:
    """Thin wrapper for starting traces and spans. Stateless across requests."""

    def start_trace(self, request_id: str, customer_id: str | None = None) -> Trace:
        trace = Trace(
            trace_id=uuid4().hex,
            request_id=request_id,
            customer_id=customer_id,
            started_at=datetime.now(UTC),
        )
        _current_trace.set(trace)
        return trace

    def end_trace(self) -> None:
        _current_trace.set(None)


_default_tracer = Tracer()


def get_tracer() -> Tracer:
    return _default_tracer


def get_current_trace() -> Trace | None:
    return _current_trace.get()


@asynccontextmanager
async def span(name: str, **attributes: Any):
    """Async context manager that opens a span on the current trace.

    Usage:
        async with span("router.classify", intent_count=3) as s:
            ...
            s.attributes["chose"] = intent
    """
    trace = _current_trace.get()
    new_span = Span(
        name=name,
        started_at=time.perf_counter(),
        attributes=dict(attributes),
        parent=trace._stack[-1] if (trace and trace._stack) else None,
    )
    if trace is not None:
        trace.spans.append(new_span)
        trace._stack.append(new_span.span_id)
    try:
        yield new_span
    except Exception as exc:
        # Record on the span and re-raise. We do NOT log here because the
        # caller may catch and handle the exception (e.g. intake's
        # GibberishInputError is expected control flow). The orchestrator
        # logs uncaught failures at the top level, and the span error is
        # always inspectable via /traces.
        new_span.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        new_span.ended_at = time.perf_counter()
        if trace is not None and trace._stack and trace._stack[-1] == new_span.span_id:
            trace._stack.pop()
