# Tests

This directory contains the unit tests for the implemented components.

Run them from the repository root with:

```bash
python -m unittest discover -s tests -v
```

The suite separately covers the JSON-RPC layer, local MCP server core,
deterministic symptom assessment, simulated interaction and allergy rules,
catalog, SQLite inventory, atomic orders, concurrency, in-memory client, stdio
transport, Streamable HTTP over real ephemeral loopback ports, and complete
client-server flows. HTTP coverage includes strict media types and UTF-8,
authentication, Origin, protocol and session headers, expiry, DELETE, request
and response limits, independent lifecycle, rollback, and concurrent
no-oversell behavior. Host coverage includes strict mixed-transport
configuration, subprocess/session lifecycle, reversible `server__tool`
registration across multiple servers, partial availability, durable redacted
JSONL logging, and the technical CLI.
Persistent tests use unique isolated SQLite files; common instances use an
in-memory database. `classify_symptoms` is tested only as an internal engine;
the public tool is `assess_symptoms`. Repository-policy tests cover canonical
path enforcement, mutation authorization, local policy logging, and resilient
multi-process cleanup. Filesystem-policy tests cover scalar and array paths,
safe missing creation targets, siblings, `..`, Windows case handling,
symlink/junction escapes, conservative annotations, and content-free policy
logs. Logging tests verify redaction-before-truncation, bounded-payload markers,
binary omission, and write/edit body omission without modifying wire messages.
Gemini and Anthropic client tests inject transports and verify URLs, POST
bodies, authentication shape, response limits, request IDs, and safe
HTTP/timeout/connection errors without network access or credentials. Gemini
coverage also checks provider selection, model normalization, dynamic function
declarations, `functionCall`/`functionResponse` correlation, fallback IDs,
thought-signature preservation, optional bounded retries, and blocked or
malformed responses.
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
The simulated chatbot integrations use fake Gemini HTTP responses and
normalized fake Anthropic responses while starting all three real MCP
processes. They cover general conversation, context, Pharmacy, Git, Filesystem,
multiple calls, mutation decisions, safe logging, and process cleanup. No test
calls either provider or consumes quota or credits.
