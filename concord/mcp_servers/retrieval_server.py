"""MCP server exposing Concord's retrieval subsystem.

Run with:
    python -m concord.mcp_servers.retrieval_server

Exposes two tools:
- kb_search(query, scope?): returns the top-k passages with sources and scores.
- kb_list_scopes(): returns the known knowledge scopes.

This server is stateless across calls. All state lives in the persistent
Chroma collection initialized by `RetrievalService`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from concord.retrieval.service import get_retrieval_service

server: Server = Server("concord-retrieval")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="kb_search",
            description="Search the Concord knowledge base. Returns top passages with sources.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "description": "Optional scope filter (billing, technical, account, general).",
                    },
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 4},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="kb_list_scopes",
            description="List the known knowledge scopes.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    service = get_retrieval_service()
    if name == "kb_search":
        passages = await service.query(
            text=arguments["query"],
            scope=arguments.get("scope"),
            top_k=arguments.get("top_k"),
        )
        payload = [p.model_dump() for p in passages]
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]
    if name == "kb_list_scopes":
        return [TextContent(type="text", text=json.dumps(
            ["billing", "technical", "account", "general"]
        ))]
    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    # Ensure the index is populated on first run.
    get_retrieval_service().index_knowledge_dir()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
