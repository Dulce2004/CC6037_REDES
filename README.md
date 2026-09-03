# PharmaMCP

## Project overview

PharmaMCP is an academic pharmacy project that demonstrates a local Model Context
Protocol (MCP) server built manually on JSON-RPC 2.0. The protocol messages,
server lifecycle, method dispatch, tool registry, and stdio transport use only the
Python standard library. The project does not use FastMCP, an MCP SDK, or an
external JSON-RPC package.

The current server accepts newline-delimited JSON-RPC messages over standard
input and returns protocol responses over standard output. A direct in-memory
client and its interactive CLI remain available for local demonstrations and
tests.

> **Medical disclaimer:** This project is for education and demonstration only.
> It does not provide a diagnosis, recommend treatment, or replace advice from a
> qualified healthcare professional. Its simulated interaction check is not
> exhaustive and cannot establish that a medication is safe.

## Current status

The implemented MCP subset supports:

- MCP protocol version `2025-11-25`.
- The `UNINITIALIZED`, `INITIALIZING`, and `READY` lifecycle states.
- `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`.
- A tools capability with `listChanged: false`.
- Five registered MCP tools: `assess_symptoms`, `search_medications`,
  `get_medication_details`, `check_interactions`, and `check_stock`.
- A manual stdio transport using UTF-8 NDJSON framing: one JSON object per line.
- JSON-RPC responses and standard error objects with request-ID correlation.
- Notifications that never produce a JSON-RPC response.
- Graceful process termination when stdin reaches EOF.

The pharmacy tools reuse the validated medication catalog, controlled simulated
interaction rules, and read-only inventory for Zona 5, Zona 15, and Mixco.
Search covers medication names, aliases, active ingredients, therapeutic
categories, and SKUs. Inventory queries never modify stock.

## Implemented features

- Manual JSON-RPC 2.0 request, notification, response, error, serialization, and
  deserialization support.
- Stateful MCP initialization and readiness enforcement.
- Tool registration, discovery, argument checks, and invocation.
- Deterministic assessment of controlled Spanish or English symptom phrases,
  optional age and duration context, severity, and urgent red flags. The prior
  identifier classifier remains an internal rule engine and is no longer a
  public MCP tool.
- Read-only medication search, complete catalog details, and branch inventory
  queries with structured results.
- Non-exhaustive medication-interaction and recorded-allergy checks backed by a
  validated simulated dataset.
- Standard-input/standard-output process transport without replacing the
  existing in-memory client-server path.
- Unit, integration, lifecycle, catalog, inventory, and subprocess transport
  tests.

## Architecture

```text
MCP client process
  -> stdin: one UTF-8 JSON-RPC object plus LF
  -> stdio transport
  -> manual JSON-RPC deserializer
  -> stateful local MCP server
  -> method dispatcher / tool registry
  -> pharmacy tool adapters
  -> deterministic assessment / catalog / interactions / inventory domain
  -> JSON-RPC Response or ErrorResponse
  -> stdout: one UTF-8 JSON-RPC object plus LF
```

The stdio loop owns one `PharmacyMCPServer` instance, so lifecycle state is
preserved across input lines until EOF. Protocol output is reserved for stdout;
unexpected transport diagnostics go to stderr.

## Repository structure

```text
.
|-- docs/
|   |-- Proyecto 1 - Uso de un protocolo existente.pdf
|   |-- demo-guide.md
|   `-- mcp-server-specification.md
|-- src/
|   `-- pharmacy_mcp/
|       |-- client/       # Existing in-memory client and interactive CLI
|       |-- jsonrpc/      # Manual JSON-RPC messages, errors, and conversion
|       |-- pharmacy/     # Assessment, catalog, interactions, inventory, and data
|       `-- server/       # MCP core, tool adapter, and stdio entry point
|-- tests/                # Unit, integration, lifecycle, and stdio tests
|-- README.md
`-- requirements.txt
```

## Requirements

- Python 3.12, the version declared for this project.
- PowerShell or a Bash-compatible terminal.

There are no third-party runtime dependencies. `requirements.txt` intentionally
contains no package requirements, so creating a virtual environment is optional.

## Start the stdio server

Run the command from the repository root.

Bash-compatible shell:

```bash
PYTHONPATH=src python -m pharmacy_mcp.server.stdio
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m pharmacy_mcp.server.stdio
```

The process waits for one complete JSON-RPC object on each input line. Complete
the MCP handshake before calling tools:

1. Send an `initialize` request.
2. Read its response.
3. Send `notifications/initialized` without an `id`.
4. Send `tools/list` or `tools/call` requests.
5. Close stdin to terminate the server cleanly.

Do not send pretty-printed, multi-line JSON. The stdio implementation uses a
newline as its message delimiter.

The existing interactive in-memory client can still be started with:

```bash
PYTHONPATH=src python -m pharmacy_mcp.client.cli
```

## Run the tests

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B -m unittest discover -s tests -v
```

PowerShell equivalent:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "src"
python -B -m unittest discover -s tests -v
```

The suite discovers all current tests instead of relying on a hard-coded test
count. It includes a subprocess test of the real stdio module entry point.

## Documentation

- [Local MCP server specification](docs/mcp-server-specification.md)
- [Reproducible stdio demonstration guide](docs/demo-guide.md)

The specification contains the exact lifecycle, message shapes, method behavior,
tool schema, result format, and implemented errors. The demonstration guide walks
through a complete manual handshake and tool call.

## Current limitations

- Only local stdio and direct in-memory communication are implemented; there is
  no HTTP or remote transport.
- Messages are processed sequentially by one server instance; there is no
  concurrent request execution.
- NDJSON batches and multi-line JSON documents are not supported.
- Five read-only tools are registered; purchase ordering is not yet implemented.
- Catalog and inventory access is read-only; there are no stock mutations or
  purchase operations.
- The published input schema is descriptive, while runtime validation is manual
  rather than a complete JSON Schema implementation.
- Prompts, resources, pagination, cancellation, progress, logging messages,
  subscriptions, server-initiated requests, and tool-list change notifications
  are not implemented.
- There is no authentication because the server is a local child process using
  stdio.
- Natural-language symptom handling is limited to deterministic controlled
  phrases; there is no LLM interpretation.
- Interaction and allergy rules are deliberately small, simulated, and
  non-exhaustive. No tool output should be treated as medical advice or proof of
  medication safety.

## Future work

The following items are planned possibilities, not implemented functionality:

- Add purchase-order creation and order-status tools with prescription and stock
  validation.
- Integrate an LLM for natural-language interaction.
- Add Streamable HTTP and a remote-server deployment.
- Explore separate Git and Filesystem MCP servers.
- Capture and analyze later network transports with Wireshark.

## Technical references

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [MCP Specification, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP architecture, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/architecture)
- [MCP lifecycle, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP transports, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP tools, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
