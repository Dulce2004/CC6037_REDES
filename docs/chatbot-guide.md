# Gemini and Anthropic Chat with MCP Tools

## Scope

The terminal `chat` command connects one selected LLM provider to the existing
manual MCP host. Gemini Developer API is the default and recommended provider;
Anthropic Messages remains an optional alternative. Both integrations use REST
through Python's standard library. There is no Google or Anthropic SDK, agent
framework, streaming LLM transport, or persistent conversation memory. The
same chat is available in the terminal and through a loopback-only local web
interface. Local and remote MCP servers are selected only by the existing host
configuration.

```text
terminal or local-browser input
  -> ChatOrchestrator (shared history, limits, confirmations, and tool loop)
      -> selected REST adapter: Gemini (default) or Anthropic
      <- normalized text and tool_use blocks
      -> MCPServerManager -> <server>__<tool> -> local stdio child
      <- MCP result -> bounded tool_result -> selected REST adapter
  <- final model text
```

The JSON-RPC implementation, stdio clients, namespaced registry, Pharmacy
server, Git/Filesystem policies, and mutation confirmation rules are shared and
unchanged. No provider-specific condition exists inside an MCP tool.

## Provider selection

`LLM_PROVIDER` accepts only `gemini` or `anthropic`, ignoring surrounding
whitespace and letter case. It defaults to `gemini`. Provider selection happens
before any MCP child starts and cannot change during a session.

| Provider | Required variables | Model behavior |
| --- | --- | --- |
| Gemini | `GEMINI_API_KEY` | `GEMINI_MODEL` defaults to the project choice `gemini-3.5-flash-lite`. |
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | No model is silently selected; use an identifier available to the account. |

Gemini configuration never requires or reads Anthropic credentials. Anthropic
configuration never requires a Gemini credential.

## Gemini REST configuration

The manual client sends a non-streaming request to:

```text
POST https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent
```

Headers are `Content-Type: application/json`, `Accept: application/json`, and
`x-goog-api-key`. The credential appears only in that header: it is absent from
the URL, JSON body, logs, object representations, and safe errors.

| Variable | Required | Default and validation |
| --- | --- | --- |
| `LLM_PROVIDER` | no | `gemini`; only `gemini` or `anthropic`. |
| `GEMINI_API_KEY` | for Gemini | No default; must be non-empty. |
| `GEMINI_MODEL` | no | `gemini-3.5-flash-lite`. A single optional `models/` prefix is removed. Additional slashes, traversal, query strings, fragments, and incompatible characters are rejected. |
| `GEMINI_BASE_URL` | no | `https://generativelanguage.googleapis.com`. HTTPS is mandatory except for explicit localhost simulators. Credentials, query strings, and fragments are rejected. |
| `GEMINI_MAX_OUTPUT_TOKENS` | no | `1024`; integer from 1 through 32,000. |
| `GEMINI_HTTP_TIMEOUT_SECONDS` | no | `30`; finite number from 0.1 through 300. |
| `GEMINI_MAX_RETRIES` | no | `0`; integer from 0 through 3. |
| `MCP_MAX_TOOL_ROUNDS` | no | `8`; integer from 1 through 32. Shared by both providers. |

The request body contains `contents`, a versioned `systemInstruction`,
`generationConfig.maxOutputTokens`, and a `tools` array only when MCP tools are
available. The response is limited to 2,000,000 bytes and every HTTP stream is
closed. The injectable transport used by tests opens no network connection.

Retries are disabled by default. If explicitly enabled, only connection errors,
timeouts, 408, 429, and selected 5xx responses are retried. `Retry-After` is
honored within a 30-second cap; otherwise bounded exponential backoff is used.
The client never retries 400, 401, 403, or 404. A timeout error warns that the
request may already have been processed.

## Anthropic alternative

Select Anthropic explicitly with `LLM_PROVIDER=anthropic`. Its existing client
continues to use `POST https://api.anthropic.com/v1/messages`,
`anthropic-version: 2023-06-01`, `x-api-key`, and a configured model.

| Variable | Required | Default |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | none |
| `ANTHROPIC_MODEL` | yes | none |
| `ANTHROPIC_BASE_URL` | no | `https://api.anthropic.com` |
| `ANTHROPIC_MAX_TOKENS` | no | `1024` |
| `ANTHROPIC_HTTP_TIMEOUT_SECONDS` | no | `30` |

Private Gemini metadata is stripped when producing an Anthropic request.

## Gemini content and function adaptation

The shared history uses `user` and `assistant` roles. Gemini receives them as
`user` and `model`, with every content value represented as a `parts` array.

| Internal block | Gemini part |
| --- | --- |
| Text | `{"text":"..."}` |
| `tool_use` | `{"functionCall":{"id":"...","name":"...","args":{}}}` |
| Successful `tool_result` | `{"functionResponse":{"id":"...","name":"...","response":{"result":"..."}}}` |
| Error `tool_result` | `{"functionResponse":{"id":"...","name":"...","response":{"error":"..."}}}` |

The host correlates each `tool_use_id` with its earlier function name before it
constructs `functionResponse`. Missing correlation fails locally before an HTTP
request. Provider IDs are preserved exactly. If Gemini omits an ID, the client
creates a deterministic session-local ID such as `gemini-call-000001`; timestamp
alone is never used. Duplicate IDs in one response are rejected.

MCP tools are discovered dynamically and converted in stable registry order:

```json
{
  "functionDeclarations": [
    {
      "name": "pharmacy__check_stock",
      "description": "Description returned by tools/list.",
      "parametersJsonSchema": {"type": "object"}
    }
  ]
}
```

The `server__tool` namespace and schema are copied without mutation. MCP
annotations, `outputSchema`, execution fields, and internal server metadata are
not sent as Gemini function declaration fields. Invalid or duplicate tools fail
clearly. If no tools are available, the `tools` member is omitted.

## Thought signatures

Gemini may return a `thoughtSignature` beside a `functionCall`. The adapter
stores it as private metadata on that normalized tool block and returns it
unchanged beside the same call when reconstructing `contents`. It is never
invented, shown as assistant text, logged, included in mutation prompts, or sent
to Anthropic. Defensive history copies and whole-turn trimming retain it.

Thought-only parts are not displayed as normal text. A response containing only
thought parts is treated as an empty response. This manual preservation is
important because the Gemini API is stateless and REST callers must return
thought signatures themselves.

## Shared tool loop and conversation

The orchestrator sends the complete retained history, stores the complete
normalized model response, executes requested MCP tools sequentially, appends
all correlated results in one user message, and asks the same provider for the
next response. Function calls continue the loop even when Gemini's
`finishReason` is `STOP`; the presence of calls, not the finish reason alone,
controls execution.

Pharmacy, Git, and Filesystem continue to support successful results,
`isError`, JSON-RPC failures, policy rejection, unknown tools, multiple calls,
and mutation confirmation. Limits are 8 tool rounds by default, 16 calls per
model response, 12,000 characters per tool result, 8,000 characters per user
input, and 48 retained messages. Binary/media results are replaced with a safe
marker. Whole completed turns are discarded first; a function call is never
separated from its result.

## Terminal commands

Run from the repository root after setting the selected provider configuration:

```powershell
$env:PYTHONPATH = "src"
python -B -m pharmacy_mcp.host.cli chat
```

Startup prints only the selected provider, normalized model, available MCP
servers, and a short help hint. During the session:

- `/help` — show commands;
- `/provider` — show the current provider and model without credentials;
- `/tools` — list registered namespaced MCP tools;
- `/servers` — show server state;
- `/clear` — remove all in-memory conversation context;
- `/exit` — close all servers and exit.

`/provider gemini` or any other attempt to switch provider is rejected locally.
Blank input is ignored. EOF and `Ctrl+C` close the session safely.

## Local web interface

The web interface is a presentation layer over the same `ChatOrchestrator`,
provider adapters, `ConversationHistory`, `MCPServerManager`, namespaced tools,
policies, and redacted protocol logger used by the terminal. It does not contain
a second tool loop or Pharmacy business logic.

```text
browser on 127.0.0.1
  -> bounded same-origin JSON API
  -> one in-memory ChatOrchestrator and ConversationHistory per browser cookie
  -> selected Gemini or Anthropic REST adapter
  -> shared MCPServerManager
  -> configured local Pharmacy / Git / Filesystem and optional remote Pharmacy
```

From the repository root, set the same provider variables described above and
the roots required by the committed host configuration. Use a dedicated
disposable directory for Git and Filesystem demonstrations:

```powershell
$env:PYTHONPATH = "src"
$env:LLM_PROVIDER = "gemini"
$env:GEMINI_API_KEY = "set-in-this-process-only"
$env:MCP_GIT_REPOSITORY_PATH = (Resolve-Path "path/to/disposable-repository").Path
$env:MCP_FILESYSTEM_ROOT = $env:MCP_GIT_REPOSITORY_PATH
python -B -m pharmacy_mcp.host.web
```

Open `http://127.0.0.1:8081/`. The default listener cannot bind to a non-loopback
address. Useful local options are `--port`, `--config`, `--log-file`,
`--history-max-messages`, `--confirmation-timeout-seconds`, and
`--session-idle-seconds`. `Ctrl+C` closes the HTTP listener and all managed MCP
servers. The default web log is `runtime/mcp-web.jsonl`.

The committed configuration enables local Pharmacy, Git, and Filesystem and
keeps `pharmacy-remote` disabled. To use the remote server, supply an explicitly
reviewed alternate config that enables it plus `PHARMACY_REMOTE_MCP_URL` and
`PHARMACY_MCP_HTTP_TOKEN`. The remote token remains in the backend process and
is never included in browser state. Do not place provider keys or MCP tokens in
the URL, web form, browser storage, or repository.

### Local API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Serve the self-contained local interface. |
| `GET` | `/static/styles.css`, `/static/app.js` | Serve local static assets; no CDN is used. |
| `GET` | `/api/status` | Return provider/model, sanitized MCP server status, limits, and this session's visible conversation. |
| `GET` | `/api/chat` | Poll this browser session while a turn or confirmation is pending. |
| `POST` | `/api/chat` | Start one turn with `{"message":"..."}`. Returns `202`; the browser polls for completion. |
| `POST` | `/api/confirm` | Accept or reject one exact pending mutation. Omitting `accept` means rejection. |
| `POST` | `/api/clear` | Clear only this browser's in-memory display and provider context. |

Each browser receives a cryptographically random `HttpOnly`, `SameSite=Strict`
session cookie. The identifier is not available to JavaScript and conversation
state remains only in the backend process. Sessions are independent, bounded,
and expire after inactivity. Restarting the process clears them.

### Web confirmation flow

When the shared orchestrator reaches a mutable tool, its worker pauses before
`manager.invoke_tool`. The backend creates a short-lived, single-use pending
confirmation containing only server, tool, expected effect, and the existing
sanitized argument summary. The browser displays a modal whose focused/default
action is **Reject**. Escape, omission of `accept`, timeout, closing the host, or
an invalid identifier never authorizes the call. Only a subsequent
`{"confirmation_id":"...","accept":true}` for that exact pending operation
returns `sí` to the orchestrator and sets `allow_mutation=True` for that one
invocation. Multiple mutations produce multiple confirmations; no session-wide
authorization exists.

### Web security and limits

- Provider keys, MCP Bearer tokens, environment variables, private config,
  process IDs, internal objects, traces, and full tool results are absent from
  HTML and API responses.
- The browser renders all server-supplied text with `textContent`, stores
  nothing in `localStorage` or `sessionStorage`, and loads no remote assets.
- Host validation limits requests to the active `127.0.0.1`/`localhost`
  listener. Mutating requests reject cross-origin browser traffic and no CORS
  allowlist or wildcard header is emitted.
- CSP, frame denial, MIME sniffing protection, no-referrer, same-origin resource
  policy, permissions policy, and `Cache-Control: no-store` are sent on every
  response.
- JSON requests are limited to 16,384 bytes, user messages to 8,000 characters,
  visible messages and tool summaries are bounded, and API responses to 262,144
  bytes. Only `application/json` encoded as UTF-8 is accepted; duplicate keys,
  non-finite numbers, unknown fields, chunked bodies, and invalid UTF-8 fail
  locally.
- One browser session can process only one turn at a time. The process supports
  at most 64 in-memory sessions by default. Provider, tool-round, tool-call,
  history, tool-result, MCP, and HTTP limits remain independently enforced.

Errors are intentionally concise and contain no traceback. The provider may be
configured while one or more MCP servers show `error`; ready servers remain
available. A connection banner, processing state, tool activity messages, and
provider/server status make those conditions visible without exposing private
configuration.

### Reproducible web demonstration

1. Use a disposable Git/Filesystem root and start the command above.
2. Open `http://127.0.0.1:8081/` and verify the provider and ready MCP servers.
3. Ask “Consulta el stock de MED-ANA-001 en zona-5”. Observe the read-only tool
   activity and final answer.
4. Ask for a fictitious order. Verify the individual modal, inspect its sanitized
   summary, and choose **Reject** first; no order is created.
5. Ask again and choose **Authorize once** only if the simulated operation is
   expected. A later mutation must ask again.
6. Select **Limpiar** and verify that the visible conversation and retained LLM
   context are empty for that browser only.
7. Stop with `Ctrl+C` and verify the managed MCP children close.

This voluntary demonstration makes real requests to the selected provider and
can consume quota. Gemini model availability and free/paid quota can change;
review the active account limits before running it. Automated tests inject fake
clients/transports and make no Gemini or Anthropic request. Never enter real
patient names, clinical records, prescription data, credentials, or other
sensitive personal information in this academic interface.

## Mutation confirmation

Read-only operations run automatically. `pharmacy__create_order`, configured
Git mutations, and Filesystem tools without an unambiguous read-only annotation
require confirmation individually. The terminal shows the server, original tool
name, bounded redacted argument summary, expected effect, and:

```text
¿Autorizar esta operación? [s/N]
```

Only `s`, `sí`, `y`, or `yes` authorizes that one call. Rejection sends an error
tool result to the model and never reaches the MCP server. Repository and
Filesystem boundaries remain mandatory after approval.

## Logs, errors, privacy, and quota

The configurable JSONL log records metadata under `llm`, `mcp`, `policy`, and
`host`. Gemini events include provider, model, attempt, HTTP status, finish
reason, candidate count, function-call count, timeout/error classification,
request start, and request finish. It never records API keys, complete headers,
conversation bodies, full responses, sensitive arguments, tool-result bodies,
or thought signatures. MCP protocol logging is unchanged.

Gemini errors cover malformed or empty candidates/content/parts, prompt blocks,
safety finish reasons, malformed calls, invalid arguments, invalid UTF-8/JSON,
oversized responses, 400, 401, 403, 404, 408, 429, 500, 502, 503, 504, timeout,
DNS, and connection failures. Only bounded, sanitized messages and request IDs
are exposed.

Free-tier access depends on current model availability and account/project
quota; it is not permanently guaranteed. Google's current pricing information
states that free-tier content may be used to improve products, while applicable
paid-tier handling differs. Review current pricing, terms, privacy controls, and
active rate limits before live use. Do not enter real patient names, medical
records, prescriptions, credentials, or other sensitive personal information in
this academic chatbot.

## Medical boundary

Pharmacy contains controlled simulated academic data. The system prompt tells
the selected model not to diagnose, replace a healthcare professional, recommend
prescription products, or treat a missing simulated interaction as proof of
safety. Urgent red flags must take precedence over an OTC purchase.

## Voluntary smoke tests

Live tests are optional, may consume quota or incur cost, and are never run by
the automated suite. Use a disposable repository/directory and set your own
credential only in the terminal environment.

Gemini, the default:

```powershell
$env:PYTHONPATH = "src"
$env:LLM_PROVIDER = "gemini"
$env:GEMINI_API_KEY = "..."
$env:GEMINI_MODEL = "gemini-3.5-flash-lite"
$env:MCP_GIT_REPOSITORY_PATH = (Resolve-Path "path/to/disposable-repository").Path
$env:MCP_FILESYSTEM_ROOT = $env:MCP_GIT_REPOSITORY_PATH
python -B -m pharmacy_mcp.host.cli chat
```

Anthropic alternative:

```powershell
$env:LLM_PROVIDER = "anthropic"
$env:ANTHROPIC_API_KEY = "..."
$env:ANTHROPIC_MODEL = "model-available-to-your-account"
python -B -m pharmacy_mcp.host.cli chat
```

Never place a real key in source, screenshots, documentation, shell history
shared with others, or support logs.

## Troubleshooting

- **400:** validate the normalized model name, schema, reconstructed function
  responses, and preserved thought signatures.
- **401/403:** verify the selected provider, credential, project permissions,
  and model access without printing the key.
- **404:** confirm that `GEMINI_MODEL` is available to the project; the project
  default is not a guarantee of permanent API availability.
- **429:** inspect current quota and rate limits. Automatic retries are disabled
  unless `GEMINI_MAX_RETRIES` is deliberately greater than zero.
- **Timeout/5xx:** the request may have reached the provider. Review before a
  manual retry or enabling bounded retries.
- **One MCP server unavailable:** use `/servers` and `/tools`; other ready
  servers remain usable.
- **Path rejected:** use an absolute path inside the dedicated configured root.
- **History too small:** restart with `chat --history-max-messages N`; unsafe
  partial tool exchanges are never trimmed.

## References

- [Gemini REST text generation](https://ai.google.dev/gemini-api/docs/generate-content/text-generation)
- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thinking)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini API rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini API terms](https://ai.google.dev/gemini-api/terms)
- [Anthropic API overview](https://platform.claude.com/docs/en/api/overview)
- [Anthropic Messages API](https://platform.claude.com/docs/en/api/http/messages/create)
