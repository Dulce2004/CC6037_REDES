# Tests

This directory contains the unit tests for the implemented components.

Run them from the repository root with:

```bash
python -m unittest discover -s tests -v
```

The suite separately covers the JSON-RPC layer, local MCP server core,
deterministic symptom assessment, simulated interaction and allergy rules,
catalog, SQLite inventory, atomic orders, concurrency, in-memory client, stdio
transport, and complete client-server flows. Host coverage includes strict
configuration, subprocess lifecycle, reversible `server__tool` registration
across multiple servers, durable redacted JSONL logging, and the technical CLI.
Persistent tests use unique isolated SQLite files; common instances use an
in-memory database. `classify_symptoms` is tested only as an internal engine;
the public tool is `assess_symptoms`. Repository-policy tests cover canonical
path enforcement, mutation authorization, local policy logging, and resilient
multi-process cleanup. The identified Git integration test runs the exact pinned
`uvx --from mcp-server-git==2026.8.18 mcp-server-git` process together with
Pharmacy, performs a complete local status/add/commit/log workflow only in a
generated ignored repository, verifies both processes exit, and removes all
artifacts. There is no network transport to test yet; the first `uvx` execution
may only need network access to populate its external user cache.
