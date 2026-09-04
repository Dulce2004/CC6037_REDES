# PharmaMCP

## Project overview

PharmaMCP is an academic pharmacy project that demonstrates a local Model Context
Protocol (MCP) server built manually on JSON-RPC 2.0. The protocol messages,
server lifecycle, method dispatch, tool registry, and stdio transport use only the
Python standard library. The project does not use FastMCP, an MCP SDK, or an
external JSON-RPC package.

The current server accepts newline-delimited JSON-RPC messages over standard
input and returns protocol responses over standard output. A configurable
terminal host can launch that server plus the pinned official Git and Filesystem
MCP servers as independent child processes, discover namespaced tools
dynamically, and invoke them while recording a bounded, redacted JSONL protocol
trace. The
host's stdio client, lifecycle, JSON-RPC correlation, routing, and policy checks
remain manually implemented. The direct in-memory pharmacy client and its
interactive CLI remain available for local demonstrations and tests.

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
- Official external `mcp-server-git==2026.8.18` integration through `uvx`, with
  dynamically discovered `git__<tool>` names, an exact configured repository
  boundary, and explicit authorization for mutable tools.
- Official external `@modelcontextprotocol/server-filesystem@2026.8.31`
  integration through `npx`, with dynamically discovered
  `filesystem__<tool>` names, one canonical allowed directory, annotation-based
  mutation authorization, and path-escape protection.
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
  configuration enables local `pharmacy` and the pinned external `git` and
  `filesystem` servers. Only explicitly declared environment variables can be
  substituted.
- Unit, integration, lifecycle, catalog, transactional order, concurrency, and
  subprocess client/transport tests.

## Architecture

```text
Terminal host CLI
  -> MCP server manager
  -> namespaced registry: <server>__<tool>
  -> one manual stdio MCP client per configured server
  -> pharmacy + official Git + official Filesystem MCP child processes
  -> stdin/stdout: one UTF-8 JSON-RPC object per line
  -> dynamically discovered server tool registries
  -> pharmacy domain / SQLite or one bounded disposable Git+Filesystem root
```

The host completes the MCP initialization lifecycle, discovers each enabled
server's tools, and owns its child processes. It starts children with
`shell=False`; on Windows the controlled npx launcher is `cmd /c npx`, while
arbitrary configured commands never receive general shell evaluation. A
general repository policy canonicalizes Git's `repo_path`, requires an exact
match with the configured root, and blocks configured mutable tools unless the
CLI invocation includes `--allow-mutation`.
The Filesystem policy validates every configured `path`, `paths`, `source`, and
`destination` value against one canonical dedicated root. It rejects relative
paths, lexical `..`, siblings, missing read targets, and symlink or junction
escapes. New write destinations are accepted only when their nearest existing
ancestor remains inside the root. A Filesystem tool is treated as read-only only
when its discovered annotations say so unambiguously; every other tool requires
`--allow-mutation`.
Every outbound MCP request or notification and every inbound response is appended
to `runtime/mcp-host.jsonl` by default. Machine-readable CLI results are written
only to stdout. Child diagnostics use stderr; the redacted protocol trace can
also be mirrored there with `--show-log`. Logged payloads and individual strings
have explicit size limits, binary fields are omitted, and write/edit bodies are
replaced by markers before persistence; the wire message sent to the child is
unchanged.

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
|   |-- filesystem-git-mcp-demo.md
|   |-- git-mcp-demo.md
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
- Git and `uvx` for the external Git server. The pinned package itself supports
  Python 3.10 or newer and is resolved in the uv cache, not installed into this
  project.
- Node.js with npm/npx for the official Filesystem server. Its pinned package is
  resolved in npm's user cache and does not create `node_modules` or package
  metadata in this repository.
- PowerShell or a Bash-compatible terminal.

There are no third-party imports in the project's runtime code.
`requirements.txt` intentionally contains no package requirements. The external
Git and Filesystem processes use their own MCP implementations externally; this
project does not import their SDKs, FastMCP, `mcp_server_git`, or Node modules.

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

Run these commands from the repository root. The default configuration includes
Pharmacy, Git, and Filesystem. Point both external servers at the same dedicated
existing disposable repository for the combined demo. Never use the course
project's repository or the whole user home:

```bash
export MCP_GIT_REPOSITORY_PATH=/absolute/path/to/disposable-demo-repository
export MCP_FILESYSTEM_ROOT="$MCP_GIT_REPOSITORY_PATH"
PYTHONPATH=src python -m pharmacy_mcp.host.cli list-servers
PYTHONPATH=src python -m pharmacy_mcp.host.cli list-tools
PYTHONPATH=src python -m pharmacy_mcp.host.cli call-tool pharmacy__check_stock --arguments '{"sku":"MED-ANA-001","branch_id":"zona-5"}'
PYTHONPATH=src python -m pharmacy_mcp.host.cli call-tool git__git_status --arguments "{\"repo_path\":\"$MCP_GIT_REPOSITORY_PATH\"}"
PYTHONPATH=src python -m pharmacy_mcp.host.cli call-tool filesystem__list_allowed_directories
```

In PowerShell, set `$env:PYTHONPATH = "src"` and set
`$env:MCP_GIT_REPOSITORY_PATH` to the absolute disposable repository path, then
set `$env:MCP_FILESYSTEM_ROOT = $env:MCP_GIT_REPOSITORY_PATH`.
Then run the same `python -m pharmacy_mcp.host.cli ...` commands. Use the global
`--config path/to/config.json` option before the subcommand to select another
local configuration. Use `--log-file path/to/host.jsonl` to change the durable
log, `--show-log` to mirror its redacted entries to stderr, and
`--allow-mutation` to authorize one mutable call after human review. Repository
path checks still apply when mutation is authorized. Global options must appear
before the subcommand. See the [terminal host guide](docs/mcp-host-guide.md) and
[safe Git MCP demonstration](docs/git-mcp-demo.md).
The [combined Filesystem and Git demonstration](docs/filesystem-git-mcp-demo.md)
shows the complete create/read/stage/commit workflow.

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
CLI behavior. Real integrations launch the exact pinned `uvx` and `npx`
commands and use only generated repositories under ignored `runtime/`; the
combined test runs all three servers and verifies cleanup.

## Documentation

- [Local MCP server specification](docs/mcp-server-specification.md)
- [Reproducible stdio demonstration guide](docs/demo-guide.md)
- [Configurable terminal host guide](docs/mcp-host-guide.md)
- [Safe external Git MCP demonstration](docs/git-mcp-demo.md)
- [Combined Filesystem and Git MCP demonstration](docs/filesystem-git-mcp-demo.md)

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
  HTTP, remote servers, Git remotes, or an LLM.
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
- Capture and analyze later network transports with Wireshark.

## Technical references

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [MCP Specification, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP architecture, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/architecture)
- [MCP lifecycle, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP transports, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP tools, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Official MCP Git server](https://github.com/modelcontextprotocol/servers/tree/main/src/git)
- [`mcp-server-git` 2026.8.18 on PyPI](https://pypi.org/project/mcp-server-git/2026.8.18/)
- [Official MCP Filesystem server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
- [`@modelcontextprotocol/server-filesystem` on npm](https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem)
