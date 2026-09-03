# PharmaMCP

## Project overview

PharmaMCP is an academic pharmacy project that demonstrates a local Model Context
Protocol (MCP) server built manually on JSON-RPC 2.0. The protocol messages,
server lifecycle, method dispatch, tool registry, and stdio transport use only the
Python standard library. The project does not use FastMCP, an MCP SDK, or an
external JSON-RPC package.

The current server accepts newline-delimited JSON-RPC messages over standard
input and returns protocol responses over standard output. A configurable
terminal host can launch that server as a child process, discover namespaced
tools, and invoke them while recording the complete MCP exchange in a redacted
JSONL log. The direct in-memory client and its interactive CLI remain available
for local demonstrations and tests.

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
- Seven registered MCP tools: `assess_symptoms`, `search_medications`,
  `get_medication_details`, `check_interactions`, `check_stock`,
  `create_order`, and `get_order_status`.
- A manual stdio transport using UTF-8 NDJSON framing: one JSON object per line.
- A configurable multi-server host with subprocess lifecycle management and
  reversible namespaced tool routing such as `pharmacy__check_stock`.
- A technical host CLI that lists configured servers, discovers tools, invokes
  tools, and appends redacted MCP traffic to a durable JSONL file.
- JSON-RPC responses and standard error objects with request-ID correlation.
- Notifications that never produce a JSON-RPC response.
- Graceful process termination when stdin reaches EOF.

The pharmacy tools reuse the validated medication catalog, controlled simulated
interaction rules, and one transactional inventory for Zona 5, Zona 15, and
Mixco. Search covers medication names, aliases, active ingredients, therapeutic
categories, and SKUs. Successful simulated orders atomically reserve stock in
SQLite; subsequent stock calls read that same state.

## Implemented features

- Manual JSON-RPC 2.0 request, notification, response, error, serialization, and
  deserialization support.
- Stateful MCP initialization and readiness enforcement.
- Tool registration, discovery, argument checks, and invocation.
- Deterministic assessment of controlled Spanish or English symptom phrases,
  optional age and duration context, severity, and urgent red flags. The prior
  identifier classifier remains an internal rule engine and is no longer a
  public MCP tool.
- Read-only medication search, complete catalog details, and current branch
  inventory queries with structured results.
- Non-exhaustive medication-interaction and recorded-allergy checks backed by a
  validated simulated dataset.
- Persistent simulated orders, exact centavo totals, format-only academic
  prescription references, and atomic all-or-nothing stock updates.
- Standard-input/standard-output process transport without replacing the
  existing in-memory client-server path.
- Strict local JSON configuration for one or more stdio servers. The committed
  configuration enables only the local `pharmacy` server.
- Unit, integration, lifecycle, catalog, transactional order, concurrency, and
  subprocess client/transport tests.

## Architecture

```text
Terminal host CLI
  -> MCP server manager
  -> namespaced registry: pharmacy__<tool>
  -> stdio MCP client
  -> pharmacy child process
  -> stdin/stdout: one UTF-8 JSON-RPC object per line
  -> stateful MCP server / tool registry
  -> pharmacy domain and shared SQLite state
```

The host completes the MCP initialization lifecycle, discovers each enabled
server's tools, and owns its child processes. It starts children directly with
`shell=False`; it does not execute configuration through a command shell.
Every outbound MCP request or notification and every inbound response is appended
to `runtime/mcp-host.jsonl` by default. Machine-readable CLI results are written
only to stdout. Child diagnostics use stderr; the redacted protocol trace can
also be mirrored there with `--show-log`.

The stdio loop owns one `PharmacyMCPServer` instance, so lifecycle state is
preserved across input lines until EOF. Protocol output is reserved for stdout;
unexpected transport diagnostics go to stderr.

The stdio entry point initializes `runtime/pharmacy.sqlite3` explicitly. That
runtime directory is ignored by Git. Set `PHARMACY_MCP_DATABASE_PATH` to use a
different database file. A new database is seeded from the validated catalog and
inventory JSON files; reopening it preserves orders and remaining stock without
changing those source files. Directly constructed in-memory servers use an
isolated SQLite database, and persistence/concurrency tests use unique database
files.

## Repository structure

```text
.
|-- config/
|   `-- mcp-servers.json  # Local stdio server definitions for the host
|-- docs/
|   |-- Proyecto 1 - Uso de un protocolo existente.pdf
|   |-- demo-guide.md
|   |-- mcp-host-guide.md
|   `-- mcp-server-specification.md
|-- src/
|   `-- pharmacy_mcp/
|       |-- client/       # Existing in-memory client and interactive CLI
|       |-- host/         # Config, subprocess client, manager, and host CLI
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

## Use the configurable terminal host

Run these commands from the repository root. The default configuration starts
only the local pharmacy server when a command needs it:

```bash
PYTHONPATH=src python -m pharmacy_mcp.host.cli list-servers
PYTHONPATH=src python -m pharmacy_mcp.host.cli list-tools
PYTHONPATH=src python -m pharmacy_mcp.host.cli call-tool pharmacy__check_stock --arguments '{"sku":"MED-ANA-001","branch_id":"zona-5"}'
```

In PowerShell, set `$env:PYTHONPATH = "src"` first and then run the same
`python -m pharmacy_mcp.host.cli ...` commands. Use the global
`--config path/to/config.json` option before the subcommand to select another
local configuration. Use `--log-file path/to/host.jsonl` to change the durable
log and `--show-log` to mirror its redacted entries to stderr. Global options
must appear before the subcommand. See the
[terminal host guide](docs/mcp-host-guide.md) for the complete configuration,
namespace, logging, redaction, and output contracts.

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
count. It includes the real stdio entry point, subprocess-client lifecycle,
multi-server namespacing, independent server state, protocol logging, and host
CLI behavior.

## Documentation

- [Local MCP server specification](docs/mcp-server-specification.md)
- [Reproducible stdio demonstration guide](docs/demo-guide.md)
- [Configurable terminal host guide](docs/mcp-host-guide.md)

The specification contains the exact lifecycle, message shapes, method behavior,
tool schema, result format, and implemented errors. The demonstration guide walks
through a complete manual handshake and tool call.

## Current limitations

- Only local stdio and direct in-memory communication are implemented; there is
  no HTTP or remote transport.
- Messages are processed sequentially by one server instance; there is no
  concurrent request execution.
- NDJSON batches and multi-line JSON documents are not supported.
- Orders have only the initial `created` status; payment, fulfillment,
  cancellation, delivery, and stock restoration are not implemented.
- Prescription validation is deliberately limited to the simulated `RX-...`
  identifier format. It does not validate a real prescription or authorize use.
- The published input schema is descriptive, while runtime validation is manual
  rather than a complete JSON Schema implementation.
- Prompts, resources, pagination, cancellation, progress, logging messages,
  subscriptions, server-initiated requests, and tool-list change notifications
  are not implemented.
- There is no authentication because the server is a local child process using
  stdio.
- The host supports only configured local stdio processes. It does not include
  HTTP, remote servers, Git/Filesystem servers, or an LLM.
- Natural-language symptom handling is limited to deterministic controlled
  phrases; there is no LLM interpretation.
- Interaction and allergy rules are deliberately small, simulated, and
  non-exhaustive. No tool output should be treated as medical advice or proof of
  medication safety.

## Future work

The following items are planned possibilities, not implemented functionality:

- Add later fulfillment and cancellation transitions if the course scope
  requires them.
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
