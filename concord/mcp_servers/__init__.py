"""MCP server wrappers.

The retrieval and action layers are designed so they can be consumed either:
- in-process via `RetrievalService` / `ActionService` (the default in the
  orchestrator, fastest path), OR
- over MCP via the servers in this package, so any MCP-compatible client
  (desktop assistants, IDE extensions, custom agents) can use them.

This satisfies ADR-004 without forcing the orchestrator to pay the protocol
overhead on every internal call.
"""
