"""ActionService: the only entry-point through which state-changing tools run.

Flow for every action:
1. Schema validation of arguments (jsonschema-style, minimal check).
2. Permission predicate (rule-based, fast, deterministic).
3. Optional verification pass (model-based, for medium/high-impact tools).
4. Idempotency check against the audit log.
5. Execution with timeout.
6. Audit log entry (always, success or failure, approved or denied).

Per Section 10: nothing state-changing happens that does not flow through here.
The orchestrator never calls a tool handler directly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from concord.actions.tools import Tool, _call_handler, get_tool
from concord.actions.verification import VerificationAgent
from concord.config import get_settings
from concord.models import (
    CustomerContext,
    RetrievedPassage,
    ToolCallProposal,
    ToolCallResult,
    VerificationResult,
)
from concord.observability.metrics import get_metrics
from concord.observability.tracing import span
from concord.state import AuditLog


class ActionDenied(Exception):
    """Raised internally when an action is blocked by permission or verification.
    The caller catches this and produces a `ToolCallResult(success=False, ...)`.
    """


def _idempotency_key(
    request_id: str, customer_id: str, tool_name: str, arguments: dict[str, Any]
) -> str:
    body = json.dumps(
        {"r": request_id, "c": customer_id, "t": tool_name, "a": arguments},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def _validate_against_schema(args: dict[str, Any], schema: dict[str, Any]) -> str | None:
    """Tiny JSON-Schema subset check (type, required, enum, additionalProperties).
    Returns an error message or None if the args are acceptable.
    """
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for k in required:
        if k not in args:
            return f"missing required argument: {k}"
    if schema.get("additionalProperties") is False:
        for k in args:
            if k not in props:
                return f"unknown argument: {k}"
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for k, v in args.items():
        if k not in props:
            continue
        spec = props[k]
        expected = spec.get("type")
        if expected and not isinstance(v, type_map.get(expected, object)):
            return f"argument {k!r} expected {expected}, got {type(v).__name__}"
        if "enum" in spec and v not in spec["enum"]:
            return f"argument {k!r} not in {spec['enum']}"
        if expected == "number":
            if "minimum" in spec and v < spec["minimum"]:
                return f"argument {k!r} below minimum {spec['minimum']}"
            if "maximum" in spec and v > spec["maximum"]:
                return f"argument {k!r} above maximum {spec['maximum']}"
    return None


class ActionService:
    def __init__(self) -> None:
        self._verifier = VerificationAgent()
        self._audit = AuditLog()
        self._metrics = get_metrics()
        self._settings = get_settings()

    async def execute(
        self,
        *,
        proposal: ToolCallProposal,
        customer: CustomerContext,
        original_request: str,
        request_id: str,
        trace_id: str,
        policy_passages: list[RetrievedPassage] | None = None,
    ) -> ToolCallResult:
        tool = get_tool(proposal.tool_name)
        if tool is None:
            self._metrics.tool_calls_total.labels(tool=proposal.tool_name, result="unknown").inc()
            return ToolCallResult(
                tool_name=proposal.tool_name,
                arguments=proposal.arguments,
                success=False,
                error=f"unknown tool {proposal.tool_name!r}",
            )

        async with span("action.execute", tool=tool.name, impact=tool.impact) as s:
            args = dict(proposal.arguments)
            idem_key = _idempotency_key(request_id, customer.customer_id, tool.name, args)

            # 0. Idempotency: if we already ran this exact action successfully, replay.
            existing = await self._audit.find_by_idempotency_key(idem_key)
            if existing is not None and existing.approved and existing.result is not None:
                s.attributes["idempotent_replay"] = True
                self._metrics.tool_calls_total.labels(tool=tool.name, result="replay").inc()
                return ToolCallResult(
                    tool_name=tool.name,
                    arguments=args,
                    success=True,
                    result=existing.result,
                    idempotency_key=idem_key,
                )

            # 1. Schema validation.
            schema_err = _validate_against_schema(args, tool.arguments_schema)
            if schema_err:
                await self._record(
                    tool, args, customer, request_id, trace_id, idem_key,
                    approved=False, result=None, rationale=f"schema: {schema_err}",
                )
                self._metrics.tool_calls_total.labels(tool=tool.name, result="schema_error").inc()
                return ToolCallResult(
                    tool_name=tool.name, arguments=args, success=False,
                    error=f"schema validation: {schema_err}", idempotency_key=idem_key,
                )

            # 2. Permission predicate.
            perm = tool.permission(customer, args)
            if not perm.allowed:
                await self._record(
                    tool, args, customer, request_id, trace_id, idem_key,
                    approved=False, result=None, rationale=f"permission: {perm.reason}",
                )
                self._metrics.tool_calls_total.labels(tool=tool.name, result="denied_permission").inc()
                return ToolCallResult(
                    tool_name=tool.name, arguments=args, success=False,
                    error=f"permission denied: {perm.reason}", idempotency_key=idem_key,
                )

            # 3. Verification (skip for low-impact tools and when feature-flagged off).
            verification: VerificationResult | None = None
            if self._settings.verification_enabled and tool.impact in {"medium", "high"}:
                verification = await self._verifier.verify(
                    original_request=original_request,
                    proposal=proposal,
                    policy_passages=policy_passages or [],
                )
                if not verification.approved:
                    await self._record(
                        tool, args, customer, request_id, trace_id, idem_key,
                        approved=False, result=None,
                        rationale=f"verifier: {verification.rationale}",
                    )
                    self._metrics.tool_calls_total.labels(tool=tool.name, result="denied_verify").inc()
                    return ToolCallResult(
                        tool_name=tool.name, arguments=args, success=False,
                        error=f"verification denied: {verification.rationale}",
                        idempotency_key=idem_key,
                    )

            # 4. Execute with timeout.
            try:
                result = await _call_handler(tool, customer, args)
            except TimeoutError:
                await self._record(
                    tool, args, customer, request_id, trace_id, idem_key,
                    approved=True, result=None, rationale="executed but timed out",
                )
                self._metrics.tool_calls_total.labels(tool=tool.name, result="timeout").inc()
                return ToolCallResult(
                    tool_name=tool.name, arguments=args, success=False,
                    error="tool timed out", idempotency_key=idem_key,
                )
            except Exception as exc:
                await self._record(
                    tool, args, customer, request_id, trace_id, idem_key,
                    approved=True, result=None, rationale=f"handler error: {exc}",
                )
                self._metrics.tool_calls_total.labels(tool=tool.name, result="error").inc()
                return ToolCallResult(
                    tool_name=tool.name, arguments=args, success=False,
                    error=str(exc), idempotency_key=idem_key,
                )

            await self._record(
                tool, args, customer, request_id, trace_id, idem_key,
                approved=True, result=result,
                rationale=verification.rationale if verification else "auto-approved (low impact)",
            )
            self._metrics.tool_calls_total.labels(tool=tool.name, result="success").inc()
            return ToolCallResult(
                tool_name=tool.name, arguments=args, success=True, result=result,
                idempotency_key=idem_key,
            )

    async def _record(
        self,
        tool: Tool,
        args: dict[str, Any],
        customer: CustomerContext,
        request_id: str,
        trace_id: str,
        idem_key: str,
        *,
        approved: bool,
        result: dict[str, Any] | None,
        rationale: str,
    ) -> None:
        await self._audit.record(
            request_id=request_id,
            trace_id=trace_id,
            customer_id=customer.customer_id,
            tool_name=tool.name,
            arguments=args,
            result=result,
            approved=approved,
            verification_rationale=rationale,
            idempotency_key=idem_key,
        )


_singleton: ActionService | None = None


def get_action_service() -> ActionService:
    global _singleton
    if _singleton is None:
        _singleton = ActionService()
    return _singleton
