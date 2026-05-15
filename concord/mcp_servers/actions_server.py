"""MCP server exposing Concord's governed action layer.

Run with:
    python -m concord.mcp_servers.actions_server

Every tool call goes through ActionService, meaning external MCP clients are
subject to the same permission checks, verification pass, and audit trail as
the in-process orchestrator. There is no privileged path.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from concord.actions.service import get_action_service
from concord.actions.tools import all_tools
from concord.models import CustomerContext, ToolCallProposal

server: Server = Server("concord-actions")


@server.list_tools()
async def list_tools() -> list[Tool]:
    out: list[Tool] = []
    for t in all_tools():
        # MCP-exposed tools take both action arguments and customer/context
        # fields so an external client can identify whose account is acted on.
        schema = {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "customer_plan": {"type": "string", "default": "free"},
                "customer_status": {"type": "string", "default": "active"},
                "original_request": {"type": "string"},
                "request_id": {"type": "string"},
                "rationale": {"type": "string"},
                **t.arguments_schema["properties"],
            },
            "required": [
                "customer_id",
                "original_request",
                "request_id",
                *t.arguments_schema.get("required", []),
            ],
        }
        out.append(
            Tool(
                name=t.name,
                description=(
                    f"{t.description}\n\nImpact: {t.impact}. "
                    f"Subject to permission and verification checks."
                ),
                inputSchema=schema,
            )
        )
    return out


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    service = get_action_service()
    customer = CustomerContext(
        customer_id=arguments["customer_id"],
        plan=arguments.get("customer_plan", "free"),
        account_status=arguments.get("customer_status", "active"),
    )
    # The action tool arguments are everything except the customer/context fields.
    tool_args = {
        k: v
        for k, v in arguments.items()
        if k
        not in {
            "customer_id",
            "customer_plan",
            "customer_status",
            "original_request",
            "request_id",
            "rationale",
        }
    }
    proposal = ToolCallProposal(
        tool_name=name,
        arguments=tool_args,
        rationale=arguments.get("rationale", "(no rationale provided)"),
        estimated_impact="unknown",
    )
    result = await service.execute(
        proposal=proposal,
        customer=customer,
        original_request=arguments["original_request"],
        request_id=arguments["request_id"],
        trace_id=os.environ.get("CONCORD_MCP_TRACE_ID", "external-mcp"),
        policy_passages=[],
    )
    return [TextContent(type="text", text=json.dumps(result.model_dump(), indent=2))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
