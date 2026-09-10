# Anthropic Chat and MCP Tool Loop

## Scope

The `chat` command is a terminal-only integration between Anthropic's Messages
API and the existing manual MCP host. It does not replace JSON-RPC, the stdio
clients, the server manager, namespaced registration, or any Git/Filesystem
policy. It adds no SDK or third-party HTTP package and does not provide
streaming, automatic retries, persistent memory, a web interface, or remote MCP.

```text
terminal input
  -> ChatOrchestrator (system prompt version pharmacy-mcp-chat-v1)
      -> AnthropicMessagesClient -> POST /v1/messages
      <- assistant text and/or tool_use blocks
      -> MCPServerManager -> <server>__<tool> -> local stdio child
      <- MCP result -> bounded tool_result -> Anthropic
  <- final assistant text
```

## Anthropic REST configuration

The client performs one non-streaming `POST` to
`https://api.anthropic.com/v1/messages`. It sends `x-api-key`,
`anthropic-version: 2023-06-01`, `content-type: application/json`, and no beta
headers. The JSON body contains the configured `model`, `max_tokens`, complete
in-memory `messages`, the short versioned `system` prompt, and dynamically
discovered `tools` when any server is available.

Environment variables:

| Variable | Required | Default and validation |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | No default. It is never placed in configuration, output, logs, or exception text. |
| `ANTHROPIC_MODEL` | yes | No default. Choose a current model identifier available to the API account. |
| `ANTHROPIC_BASE_URL` | no | `https://api.anthropic.com`. HTTPS is mandatory except for an explicit `http://localhost`, `127.0.0.1`, or `::1` simulator. |
| `ANTHROPIC_MAX_TOKENS` | no | `1024`; integer from 1 through 32,000. |
| `ANTHROPIC_HTTP_TIMEOUT_SECONDS` | no | `30`; finite number from 0.1 through 300. |
| `MCP_MAX_TOOL_ROUNDS` | no | `8`; integer from 1 through 32. |

The API key and model are validated before any MCP child is started. `.env`
files remain ignored, and the application does not load them. The base URL is
intended only for an explicitly selected compatible endpoint or local test
server; tests normally inject an in-memory transport and open no socket.

## Dynamic tools and the sequential loop

At session start the manager attempts every enabled server and keeps those that
succeed. Failures appear on stderr while `/servers` shows partial availability.
The global registry is converted in deterministic order. Each Anthropic tool
contains only:

```json
{
  "name": "pharmacy__check_stock",
  "description": "The description discovered through tools/list.",
  "input_schema": {"type": "object"}
}
```

MCP `outputSchema`, annotations, server metadata, and internal names are not sent
as extra Anthropic fields. The host still retains annotations locally for policy
decisions. Pharmacy, Git, and Filesystem tool names are never hardcoded into the
conversion.

When Claude returns `tool_use`, the host stores the complete assistant content,
including adjacent text. It executes every requested tool sequentially, builds
one correlated `tool_result` per `tool_use_id`, preserves response order, places
all results in one user message, and calls Anthropic again. Sequential execution
is deliberate: Pharmacy shares mutable inventory, while Filesystem and Git
operations may depend on earlier calls. The loop ends at final text or a safe
limit; it does not promise parallel execution.

`structuredContent` is serialized first when present. Otherwise text from MCP
`content` is used. `isError: true`, JSON-RPC failures, unknown tools, local
policy failures, and unexpected tool exceptions become distinguishable error
tool-results rather than invented successes. Binary/media blocks and string
`data` or `blob` fields are replaced by an omission marker.

Safety limits are 8 tool rounds by default, 16 calls in one assistant response,
12,000 characters per result sent to Claude, 8,000 characters per user input,
2,000,000 bytes per HTTP response, and 48 in-memory messages. Use
`chat --history-max-messages N` to change the last value. When old context must
be removed, complete turns are discarded; an assistant `tool_use` is never
separated from its following user `tool_result`. If an active exchange cannot
fit safely, the turn ends with a clear error. Reaching a tool limit produces an
error result for every unexecuted request, performs no further tool/API call,
and leaves consistent local history.

## Conversation commands

Run `python -m pharmacy_mcp.host.cli chat`, then enter normal text or:

- `/help` — show the session commands;
- `/tools` — show currently registered namespaced tools;
- `/servers` — show each configured server's ready, stopped, or failed state;
- `/clear` — erase all in-memory conversation history;
- `/exit` — close every server and exit.

Blank lines are ignored. EOF and `Ctrl+C` close the session safely. Final model
text goes to stdout; API/server diagnostics and controlled errors go to stderr.
Conversation messages never persist to disk. For example, after asking who Alan
Turing was, a following question asking his birth date sends the original user
question and complete assistant answer before the new question. `/clear`
removes that context.

## Mutation confirmation

Read-only operations run automatically only when current policy classifies them
as read-only. `pharmacy__create_order` always requires confirmation because it
creates an order and changes stock. Configured Git mutations and Filesystem
tools without unambiguous read-only annotations also require confirmation.

Before each mutable call the terminal displays the server, original tool name,
a bounded/redacted argument summary, expected effect, and:

```text
¿Autorizar esta operación? [s/N]
```

Only `s`, `sí`, `y`, or `yes` (case-insensitive) authorizes that single call.
Any other input rejects it, sends an error `tool_result` back to Claude, and
never calls the MCP server. The technical `--allow-mutation` option remains for
one manual `call-tool`; it never silently authorizes a live chat session.
Configured repository and path boundaries remain mandatory even after approval.

## Logs, credentials, and errors

The existing JSONL log remains `runtime/mcp-host.jsonl` by default and can be
changed with the global `--log-file` option. MCP wire traffic keeps category
`mcp`. Chat records bounded metadata under `llm`, `mcp`, `policy`, and `host`:
turn start/end, message/tool counts, round, stop reason, request-ID presence,
authorization/rejection, and safe error type. It does not record API request
bodies, authorization headers, API keys, full user/model text, or duplicate full
tool results. Existing recursive redaction and write-content omission still
apply.

The HTTP client accepts only a bounded body, closes success and error responses,
and reports invalid JSON, missing response fields, 401 authentication, 403
permission, 429 rate/spending limits, 5xx availability failures, timeouts, DNS
or connection failures, and request IDs without exposing response bodies or
credentials. It intentionally does not retry because an automatic retry could
duplicate cost or side effects.

Anthropic API use may incur charges and is subject to account-specific rate and
spending limits. There is no guarantee of free credits or that any particular
model is enabled. Check the current model access, pricing, limits, and balance in
the account before running a live session.

## Medical boundary

Pharmacy uses a small academic simulated dataset. The system prompt tells the
model not to diagnose, not to replace a professional, and to prioritize urgent
care for red flags rather than an OTC purchase. A simulated interaction check is
not exhaustive, and a result with no recorded interaction does not prove that a
medicine is safe. Prescription identifiers are format-only academic data.

## Voluntary live smoke test

Choose a disposable Git repository/directory, confirm that the external server
packages are available, and deliberately set your own values. Do not paste the
key into source files, screenshots, documentation, or support logs.

```powershell
$env:PYTHONPATH = "src"
$env:ANTHROPIC_API_KEY = "..."
$env:ANTHROPIC_MODEL = "model-available-to-your-account"
$env:MCP_GIT_REPOSITORY_PATH = (Resolve-Path "path/to/disposable-repository").Path
$env:MCP_FILESYSTEM_ROOT = $env:MCP_GIT_REPOSITORY_PATH
python -m pharmacy_mcp.host.cli chat
```

This manual test is optional and can consume paid API usage. Automated tests use
fake Anthropic responses and never need a real credential.

## Troubleshooting

- A missing-key or missing-model error occurs before server startup; set both
  required variables in the current terminal.
- A 401 usually means authentication failed; a 403 means the account cannot use
  the requested resource; a 429 means a rate or spending limit was reached.
- For a 5xx, timeout, or connection error, note the safe request ID when present
  and retry manually only after deciding that another paid request is wanted.
- If one MCP server fails, use `/servers` and `/tools`; the remaining ready
  servers are still usable.
- If a path is rejected, use an absolute path inside the dedicated configured
  root. Authorization never permits an escape.
- If history is too small for one tool exchange, restart with a larger
  `--history-max-messages` value; the host refuses unsafe partial trimming.

## References

- [Anthropic API overview](https://platform.claude.com/docs/en/api/overview)
- [Messages API](https://platform.claude.com/docs/en/api/http/messages/create)
- [Client tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
- [Parallel tool results format](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)
- [Anthropic API errors](https://platform.claude.com/docs/en/api/errors)
