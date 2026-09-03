# Configurable MCP Terminal Host

## Purpose and scope

The terminal host is an independent local MCP client layer. It reads a JSON
configuration, starts enabled stdio servers as child processes, completes the
MCP initialization lifecycle, discovers their tools, and routes manual calls by
a reversible global name.

The committed configuration contains only the local `pharmacy` server. The host
does not integrate an LLM, HTTP transport, remote services, or Git and
Filesystem servers.

```text
Host CLI
  -> MCPServerManager
      -> registry: <server>__<tool>
      -> StdioMCPClient
          -> configured child process
              -> NDJSON JSON-RPC over stdin/stdout
```

The existing in-memory client and the server's standalone stdio entry point are
unchanged and remain usable independently.

## Server configuration

The default file is `config/mcp-servers.json`. It has one non-empty `servers`
array. Each server definition supports:

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique namespace prefix made of letters, digits, `_`, or `-`; it must start with a letter, cannot contain `__`, and cannot end in `_`. |
| `transport` | yes | Must currently be `stdio`. |
| `command` | yes | Executable path or `${PYTHON_EXECUTABLE}` for the current Python interpreter. |
| `args` | no | Array of literal process arguments. |
| `cwd` | no | Working directory, resolved relative to the configuration file; default is `.`. |
| `env` | no | String environment variables added to the inherited process environment. |
| `request_timeout_seconds` | no | Positive request timeout up to 300 seconds; default is 10. |
| `shutdown_timeout_seconds` | no | Positive graceful-shutdown timeout up to 300 seconds; default is 5. |
| `enabled` | no | Whether `list-tools` starts the server; default is `true`. |

Unknown fields, duplicate or ambiguous names, missing required fields, invalid
directories, and unsupported transports are rejected before a process starts.
Commands are passed directly to the operating system with `shell=False`.

The default pharmacy process inherits the terminal environment and adds
`PYTHONPATH=src`. The committed configuration has no personal absolute paths or
secrets. `${PYTHON_EXECUTABLE}` resolves to the interpreter running the host, and
`cwd` is portable because it resolves relative to the configuration file.

To isolate pharmacy state, set `PHARMACY_MCP_DATABASE_PATH` in a private local
configuration's `env` object. The host never writes or logs the complete child
environment.

## Commands

Set the source directory once, then invoke the host from the repository root.

Bash-compatible shell:

```bash
export PYTHONPATH=src
python -m pharmacy_mcp.host.cli list-servers
python -m pharmacy_mcp.host.cli list-tools
python -m pharmacy_mcp.host.cli list-tools --server pharmacy
python -m pharmacy_mcp.host.cli call-tool pharmacy__check_stock --arguments '{"sku":"MED-ANA-001","branch_id":"zona-5"}'
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m pharmacy_mcp.host.cli list-servers
python -m pharmacy_mcp.host.cli list-tools
python -m pharmacy_mcp.host.cli list-tools --server pharmacy
python -m pharmacy_mcp.host.cli call-tool pharmacy__check_stock --arguments '{"sku":"MED-ANA-001","branch_id":"zona-5"}'
```

Global options go before the subcommand. To select another local server
configuration or log destination:

```bash
python -m pharmacy_mcp.host.cli --config path/to/servers.json --log-file path/to/host.jsonl list-tools
```

`list-servers` reports configuration and current host state without starting
children. Each other CLI invocation owns its child processes only for that
command and closes their stdin afterward, allowing the servers to exit on EOF.
Invalid argument JSON and malformed namespaces fail with a nonzero status before
a tool call. Unknown tools return a clear host error, and all started children
are still closed.

## Namespaced tools

The only separator is `__`. After `tools/list`, a server tool such as
`check_stock` is registered as `pharmacy__check_stock`. The registry keeps the
server name and original tool name, and sends only `check_stock` to Pharmacy.
No dotted alias is retained; the former dotted form is an unknown global name.

Server and tool components cannot contain `__`. Server names cannot end in `_`,
and tool names cannot start with `_`; these boundary rules prevent separator
ambiguity and make the mapping reversible. Tool components otherwise contain
only letters, numbers, hyphens, and underscores. Duplicate global names are
rejected, and registry order follows server configuration order and each
server's `tools/list` order.

With the default configuration, `list-tools` publishes exactly:

- `pharmacy__assess_symptoms`
- `pharmacy__search_medications`
- `pharmacy__get_medication_details`
- `pharmacy__check_interactions`
- `pharmacy__check_stock`
- `pharmacy__create_order`
- `pharmacy__get_order_status`

Arguments must be one JSON object. Protocol-level JSON-RPC errors cause a host
error and nonzero exit status. A tool execution result containing
`"isError": true` remains a successful JSON-RPC response and is printed as the
tool result, preserving MCP error semantics.

## Durable JSONL protocol log

Every CLI run explicitly opens a durable append-only log before it can start a
server. The default path is:

```text
runtime/mcp-host.jsonl
```

The host creates the parent directory explicitly. `runtime/` is ignored by Git.
Use the global `--log-file` option to select another destination. Existing log
content is preserved, every entry is flushed immediately, and the file is
closed when the command finishes. If the directory or file cannot be opened or
written, the CLI reports a clear error and does not pretend logging succeeded.

Each line is one complete JSON object with:

- `timestamp`: UTC ISO 8601 timestamp ending in `Z`;
- `server`: configured server name;
- `transport`: currently `stdio`;
- `direction`: `outbound`, `inbound`, or `diagnostic`;
- `message_type`: `request`, `notification`, `response`, `error`, `diagnostic`,
  or `invalid`;
- `method`: included when the JSON-RPC message has one;
- `id`: included when the JSON-RPC message has one, including a null ID;
- `payload`: the complete redacted message.

The log covers `initialize` and its response,
`notifications/initialized`, `tools/list` and its response, and every
`tools/call` result or JSON-RPC error.

Sensitive keys are redacted recursively and case-insensitively in objects and
arrays. The controlled key set is `api_key`, `apikey`, `authorization`, `token`,
`access_token`, `password`, `secret`, and `client_secret`. Values become
`[REDACTED]`. Redaction operates on a copy and never changes the message sent to
the child. The same redacted representation is used for optional stderr
visualization. Child stderr diagnostics are also sanitized before display and
persistence.

## stdout, stderr, and log visualization

- Host stdout contains only the requested formatted JSON result.
- Host stderr contains errors and child diagnostics.
- Child stdout contains only NDJSON JSON-RPC protocol messages.
- The JSONL file keeps the durable exchange history.

Protocol entries are not printed to stderr by default. Add `--show-log` before
the subcommand to mirror each redacted JSONL entry:

```bash
python -m pharmacy_mcp.host.cli --show-log list-tools
```

This keeps normal CLI output readable while allowing a complete live protocol
trace when needed.

## Safe cleanup

Stop the host command before removing generated files. The host normally closes
all subprocesses and the log automatically. Delete only the selected JSONL log
and, if appropriate, the configured disposable SQLite test database. Do not
delete the versioned catalog or inventory JSON files. Never recursively remove
the repository root; `runtime/` may contain persistent local order state.
