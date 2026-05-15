"""Prometheus-style metrics. Exposed at /metrics by the FastAPI app.

Defines the metrics we actually look at on the dashboard:
- resolution rate (counter, labeled by outcome)
- latency (histogram, labeled by stage)
- token usage / cost (counter, labeled by tier)
- escalation rate (counter, labeled by reason)
- tool failures (counter, labeled by tool)
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()

        self.requests_total = Counter(
            "concord_requests_total",
            "Total support requests received, labeled by outcome and intent.",
            ["outcome", "intent"],
            registry=self.registry,
        )
        self.stage_latency = Histogram(
            "concord_stage_latency_ms",
            "Stage latency in milliseconds.",
            ["stage"],
            buckets=(25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
            registry=self.registry,
        )
        self.tokens_total = Counter(
            "concord_tokens_total",
            "Total tokens consumed, labeled by tier and direction.",
            ["tier", "direction"],
            registry=self.registry,
        )
        self.cost_micro_usd = Counter(
            "concord_cost_micro_usd_total",
            "Estimated cost in micro-USD (1e-6 USD), labeled by tier.",
            ["tier"],
            registry=self.registry,
        )
        self.escalations_total = Counter(
            "concord_escalations_total",
            "Escalations, labeled by reason.",
            ["reason"],
            registry=self.registry,
        )
        self.tool_calls_total = Counter(
            "concord_tool_calls_total",
            "Tool calls, labeled by tool and result.",
            ["tool", "result"],
            registry=self.registry,
        )
        self.verification_outcomes = Counter(
            "concord_verification_outcomes_total",
            "Verification pass outcomes.",
            ["approved"],
            registry=self.registry,
        )

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


_singleton: Metrics | None = None


def get_metrics() -> Metrics:
    global _singleton
    if _singleton is None:
        _singleton = Metrics()
    return _singleton
