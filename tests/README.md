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
multi-process cleanup. Filesystem-policy tests cover scalar and array paths,
safe missing creation targets, siblings, `..`, Windows case handling,
symlink/junction escapes, conservative annotations, and content-free policy
logs. Logging tests verify redaction-before-truncation, bounded-payload markers,
binary omission, and write/edit body omission without modifying wire messages.
Anthropic client tests inject a transport and verify the URL, POST body,
authentication shape, stable API version, response limits, request IDs, and
safe HTTP/timeout/connection errors without network access or credentials.
Conversation and orchestration tests cover complete context, `/clear`, safe
history trimming, dynamic Pharmacy/Git/Filesystem schemas, single and multiple
tool calls, MCP and JSON-RPC errors, bounded media/results, tool-round limits,
and per-mutation confirmations in Spanish and English.
The Git integration test runs the exact pinned
`uvx --from mcp-server-git==2026.8.18 mcp-server-git` process together with
Pharmacy in a generated ignored repository. The combined real integration also
runs pinned `@modelcontextprotocol/server-filesystem@2026.8.31` through npx in
offline cache mode, starts all three servers, writes and reads through
Filesystem, commits through Git, checks the protocol log and process shutdown,
and removes only its generated root. External server integration tests use local
stdio; first executions may need network access only to populate external uv and
npm user caches.
The simulated chatbot integration uses a fake LLM while starting all three real
MCP processes; no test calls Anthropic or consumes credits.
