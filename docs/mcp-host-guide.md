# Configurable MCP Terminal Host

## Purpose and scope

The terminal host is an independent local MCP client layer. It reads a JSON
configuration, starts enabled stdio servers as child processes, completes the
MCP initialization lifecycle, discovers their tools, and routes manual calls by
a reversible global name.

The committed configuration contains the local `pharmacy` server and the
official external `mcp-server-git==2026.8.18` process. The host does not
integrate an LLM, HTTP transport, remote services, or Filesystem MCP.

```text
Host CLI
  -> MCPServerManager
      -> registry: <server>__<tool>
      -> StdioMCPClient
          -> pharmacy child process
          -> pinned official Git MCP child process
              -> NDJSON JSON-RPC over stdin/stdout
```

The existing in-memory client and the server's standalone stdio entry point are
unchanged and remain usable independently.

## Server configuration

The default file is `config/mcp-servers.json`. It has a non-empty `servers`
array and an optional `variables` array. Each server definition supports:

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
| `repository_policy` | no | General host policy that fixes a repository argument to one canonical root and declares original mutable tool names. |

Unknown fields, duplicate or ambiguous names, missing required fields, invalid
directories, and unsupported transports are rejected before a process starts.
Commands are passed directly to the operating system with `shell=False`.

The default pharmacy process inherits the terminal environment and adds
`PYTHONPATH=src`. The committed configuration has no personal absolute paths or
secrets. `${PYTHON_EXECUTABLE}` resolves to the interpreter running the host, and
relative `cwd` values resolve from the configuration file.

The only declared environment substitution is
`${MCP_GIT_REPOSITORY_PATH}`. The loader rejects undeclared references and fails
before starting a process when a required value is missing or empty. It does not
expand the environment wholesale, log environment mappings, or invoke a shell.
Set this variable to an existing, absolute, dedicated Git repository before
using the default configuration. The value supplies both Git's
`--repository` argument and the host's independent repository policy.

The Git process command is fixed to:

```text
uvx --from mcp-server-git==2026.8.18 mcp-server-git --repository <configured-root>
```

This external server uses its own MCP SDK inside uv's isolated environment. The
project's client remains manual and imports neither that SDK nor the Git server.
The package requires Python 3.10 or newer and is MIT licensed. Its first `uvx`
run may download artifacts into the user uv cache; after the cache is populated,
set `UV_OFFLINE=1` or add uv's `--offline` option in a private configuration when
working without network access.

To isolate pharmacy state, set `PHARMACY_MCP_DATABASE_PATH` in a private local
configuration's `env` object. The host never writes or logs the complete child
environment.

## Commands

Set the source directory once, then invoke the host from the repository root.

Bash-compatible shell:

```bash
export PYTHONPATH=src
export MCP_GIT_REPOSITORY_PATH=/absolute/path/to/disposable-demo-repository
python -m pharmacy_mcp.host.cli list-servers
python -m pharmacy_mcp.host.cli list-tools
python -m pharmacy_mcp.host.cli list-tools --server pharmacy
python -m pharmacy_mcp.host.cli list-tools --server git
python -m pharmacy_mcp.host.cli call-tool pharmacy__check_stock --arguments '{"sku":"MED-ANA-001","branch_id":"zona-5"}'
python -m pharmacy_mcp.host.cli call-tool git__git_status --arguments "{\"repo_path\":\"$MCP_GIT_REPOSITORY_PATH\"}"
```

PowerShell:

```powershell
$env:PYTHONPATH = "src"
$env:MCP_GIT_REPOSITORY_PATH = (Resolve-Path "path/to/disposable-demo-repository").Path
python -m pharmacy_mcp.host.cli list-servers
python -m pharmacy_mcp.host.cli list-tools
python -m pharmacy_mcp.host.cli list-tools --server pharmacy
python -m pharmacy_mcp.host.cli list-tools --server git
python -m pharmacy_mcp.host.cli call-tool pharmacy__check_stock --arguments '{"sku":"MED-ANA-001","branch_id":"zona-5"}'
python -m pharmacy_mcp.host.cli call-tool git__git_status --arguments ('{"repo_path":' + ($env:MCP_GIT_REPOSITORY_PATH | ConvertTo-Json -Compress) + '}')
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

Add the global `--allow-mutation` option before `call-tool` only after reviewing
the intended change. Without it, configured mutable tools are rejected locally
and no `tools/call` request reaches Git. With it, the authorization is recorded
and the exact repository boundary remains mandatory:

```powershell
python -m pharmacy_mcp.host.cli --allow-mutation call-tool git__git_add --arguments ('{"repo_path":' + ($env:MCP_GIT_REPOSITORY_PATH | ConvertTo-Json -Compress) + ',"files":["README.md"]}')
```

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

Pharmacy publishes exactly:

- `pharmacy__assess_symptoms`
- `pharmacy__search_medications`
- `pharmacy__get_medication_details`
- `pharmacy__check_interactions`
- `pharmacy__check_stock`
- `pharmacy__create_order`
- `pharmacy__get_order_status`

The Git list is never hardcoded by the host. It is obtained from the real
server's `tools/list` response. Version `2026.8.18` was observed publishing the
following original/global pairs:

- `git_status` / `git__git_status`
- `git_diff_unstaged` / `git__git_diff_unstaged`
- `git_diff_staged` / `git__git_diff_staged`
- `git_diff` / `git__git_diff`
- `git_commit` / `git__git_commit`
- `git_add` / `git__git_add`
- `git_reset` / `git__git_reset`
- `git_log` / `git__git_log`
- `git_create_branch` / `git__git_create_branch`
- `git_checkout` / `git__git_checkout`
- `git_show` / `git__git_show`
- `git_branch` / `git__git_branch`

The registry preserves descriptions, input schemas, valid annotations, and the
original name. Results are passed through as valid MCP values: `content`, text
blocks, `isError`, and additional fields are preserved, and
`structuredContent` is not required. JSON-RPC errors remain host errors; the
host does not invent JSON from Git's text output.

## Repository and mutation policy

For a server with `repository_policy`, every call must contain the configured
repository argument (`repo_path` for Git). The host requires an absolute,
existing directory, rejects lexical `..`, resolves filesystem links, and accepts
only an exact canonical match with the configured root. Subdirectories,
siblings, missing paths, and symlink or junction escapes are rejected. Windows
case aliases are compared using normal platform semantics. The host replaces the
accepted value with its canonical configured path before sending it.

Mutation classification uses the registered original tool name, not merely the
user-supplied namespaced string. The current policy requires explicit
authorization for `git_add`, `git_commit`, `git_reset`, `git_checkout`, and
`git_create_branch`. Read-only Git tools do not require the flag. Point the
configuration only at a disposable or explicitly dedicated local repository;
tests never point Git MCP at this project.

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
- `direction`: `outbound`, `inbound`, `diagnostic`, or `local`;
- `message_type`: `request`, `notification`, `response`, `error`, `diagnostic`,
  `invalid`, `mutation_rejected`, `mutation_authorized`, or
  `repository_rejected`;
- `method`: included when the JSON-RPC message has one;
- `id`: included when the JSON-RPC message has one, including a null ID;
- `payload`: the complete redacted message.

The log covers `initialize` and its response,
`notifications/initialized`, `tools/list` and its response, and every
`tools/call` result or JSON-RPC error.

Local policy entries are not protocol traffic. They identify the server and
registered tool, but omit arguments and environment values. A rejected call has
a local event and no matching outbound `tools/call`; an authorized mutation has
a local authorization event followed by normal protocol traffic.

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

Stop the host command before removing generated files. The host closes every
started subprocess even when closing another server fails, and closes the log
automatically. Delete only a repository that you deliberately created for the
demo; the host never deletes a user-provided path. Do not delete the versioned
catalog or inventory JSON files. Never recursively remove the project root;
`runtime/` may contain persistent local order state.

See [Safe Git MCP demonstration](git-mcp-demo.md) for the complete local-only
workflow and human confirmation point.

## References

- [Official MCP Git server](https://github.com/modelcontextprotocol/servers/tree/main/src/git)
- [`mcp-server-git` 2026.8.18 on PyPI](https://pypi.org/project/mcp-server-git/2026.8.18/)
- [MCP lifecycle and version negotiation, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
