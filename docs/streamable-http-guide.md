# Local MCP Streamable HTTP Guide

## Scope

This increment exposes the existing seven-tool Pharmacy server through a manual,
non-streaming subset of MCP Streamable HTTP revision `2025-11-25`. It reuses the
same JSON-RPC parser, lifecycle, tool registry, domain handlers, and SQLite store
as the in-memory and stdio paths. It adds no MCP SDK, web framework, third-party
HTTP client, Docker image, cloud deployment, or browser interface.

The transports serve different development scenarios:

```text
local host -> StdioMCPClient -> local Pharmacy child -> NDJSON stdin/stdout
local host -> HTTPMCPClient  -> Pharmacy /mcp       -> one POST per message
```

The committed host configuration keeps `pharmacy` enabled over stdio and adds
`pharmacy-remote` over HTTP in a disabled state. A disabled remote definition
does not contact a URL and does not affect normal startup.

## Start the server on loopback

From the repository root in PowerShell:

```powershell
$env:PYTHONPATH = "src"
$env:HOST = "127.0.0.1"
$env:PORT = "8080"
$env:PHARMACY_MCP_HTTP_TOKEN = "replace-with-a-local-test-token"
$env:PHARMACY_MCP_ALLOWED_ORIGINS = "https://ui.example.test"
$env:PHARMACY_MCP_DATABASE_PATH = "runtime/http-pharmacy.sqlite3"
python -B -m pharmacy_mcp.server.http
```

Diagnostics go to stderr. The process does not write protocol data or secrets to
stdout. `Ctrl+C` closes the listener, every live session, and its SQLite
connection. The default bind is `127.0.0.1:8080`; `PORT` is read as an integer so
a future container platform can supply it.

The example token and Origin above are placeholders. Do not place real tokens in
versioned files, shell history, screenshots, or protocol logs.

## Runtime variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Listener interface. |
| `PORT` | `8080` | Listener port; `0` selects an ephemeral port in tests. |
| `PHARMACY_MCP_HTTP_TOKEN` | unset | Bearer token. When set, every `/mcp` request must authenticate. |
| `PHARMACY_MCP_ALLOWED_ORIGINS` | empty | Comma-separated exact HTTP(S) origins accepted when an `Origin` header is present. No wildcard is accepted. |
| `PHARMACY_MCP_HTTP_MAX_REQUEST_BYTES` | `1000000` | Maximum POST body size, from 1 KiB through 32 MB. |
| `PHARMACY_MCP_HTTP_REQUEST_TIMEOUT_SECONDS` | `30` | Per-connection socket timeout, from 0.1 through 300 seconds. |
| `PHARMACY_MCP_HTTP_MAX_SESSIONS` | `100` | Maximum active sessions, from 1 through 10,000. |
| `PHARMACY_MCP_HTTP_SESSION_TTL_SECONDS` | `1800` | Idle lifetime, from 0.1 through 86,400 seconds. |
| `PHARMACY_MCP_DATABASE_PATH` | `runtime/pharmacy.sqlite3` | SQLite state shared by sessions in this server configuration. Source JSON is never modified. |
| `PHARMACY_MCP_HTTP_ALLOW_INSECURE_NO_AUTH` | `false` | Explicitly unsafe test-only override for a non-loopback bind without a token. |

Without a token, the server starts only on a loopback address. A non-loopback
bind requires a token unless the clearly named unsafe override is `true`; that
override is only for an isolated test environment and must not be used for a
demonstration exposed to a network.

## Endpoints and HTTP contract

`POST /mcp` carries exactly one UTF-8 JSON-RPC object:

- `Content-Type` must be `application/json`, optionally with UTF-8 charset.
- `Accept` must list both `application/json` and `text/event-stream`, as required
  by the Streamable HTTP client contract. This implementation always selects
  JSON and never emits SSE.
- A JSON-RPC request with an `id` returns HTTP `200`,
  `Content-Type: application/json`, and one JSON-RPC response object.
- An accepted notification returns HTTP `202` and a zero-length body.
- Invalid JSON returns a JSON-RPC `-32700 Parse error` response with HTTP `200`.
- An invalid JSON-RPC message or batch array returns `-32600 Invalid Request`
  with HTTP `200`.
- Valid JSON-RPC method and parameter errors remain JSON-RPC errors; they are not
  converted into unrelated HTTP statuses.

`GET /mcp` returns HTTP `405`, a JSON body, and
`Allow: POST, GET, DELETE`. There is no SSE stream in this increment.
`DELETE /mcp` terminates the identified session and normally returns `204` with
no body. Other methods on `/mcp` return `405` and no HTML.

`GET /health` returns HTTP `200` with `{"status":"ok"}`. It is a plain health
check, does not create or inspect an MCP session, and does not process JSON-RPC.

Transport validation uses HTTP errors where the HTTP envelope itself is invalid:
missing session or version (`400`), missing/incorrect Bearer token (`401`),
rejected Origin (`403`), unknown/deleted/expired session (`404`), unsupported
media type (`415`), unacceptable response media (`406`), excessive request
(`413`), timeout (`408`), or session capacity (`503`). Error bodies and server
diagnostics never echo authorization, session IDs, origins, or request content.

## Session and lifecycle sequence

An `initialize` request without `MCP-Session-Id` creates a new independent
`PharmacyMCPServer`. Only a successful initialize result is registered. The
server returns a cryptographically random opaque ID in `MCP-Session-Id`.

Example request body:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"manual-http-demo","version":"1.0"}}}
```

Every later POST and DELETE must send both:

```text
MCP-Session-Id: <opaque value returned by initialize>
MCP-Protocol-Version: 2025-11-25
```

The client then sends the response-free notification:

```json
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
```

Only after that notification may it call `tools/list` or `tools/call`. A second
initialize in the same session is rejected by the existing lifecycle with
JSON-RPC `-32600`. Lifecycle state cannot leak between sessions. Calls within
one session are locked in order; separate sessions can run concurrently, while
SQLite transactions prevent negative stock and overselling.

Sessions expire lazily after the configured idle period. The registry is
thread-safe and bounded. Session IDs never enter diagnostics, JSONL protocol
payloads, `repr`, or safe client exceptions.

## Reproducible PowerShell exchange

With the server running as shown earlier:

```powershell
$endpoint = "http://127.0.0.1:8080/mcp"
$headers = @{
  Accept = "application/json, text/event-stream"
  Authorization = "Bearer $env:PHARMACY_MCP_HTTP_TOKEN"
}
$initialize = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"manual-http-demo","version":"1.0"}}}'
$response = Invoke-WebRequest -Method Post -Uri $endpoint -Headers $headers -ContentType "application/json; charset=utf-8" -Body $initialize
$session = $response.Headers["MCP-Session-Id"]
$headers["MCP-Session-Id"] = $session
$headers["MCP-Protocol-Version"] = "2025-11-25"

$initialized = '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
Invoke-WebRequest -Method Post -Uri $endpoint -Headers $headers -ContentType "application/json; charset=utf-8" -Body $initialized

$list = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers -ContentType "application/json; charset=utf-8" -Body $list

$stock = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001","branch_id":"zona-5"}}}'
Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers -ContentType "application/json; charset=utf-8" -Body $stock

Invoke-WebRequest -Method Delete -Uri $endpoint -Headers $headers
```

Do not print or persist `$session`. A request without `Origin` is valid for a
non-browser client. If an `Origin` is sent, it must exactly match one configured
entry; the server never enables wildcard CORS.

## Host HTTP client and remote configuration

`HTTPMCPClient` uses `urllib`, sends one POST per message, correlates JSON-RPC
IDs, performs initialize plus initialized automatically, captures the session,
and sends DELETE during cleanup. It accepts JSON responses. If a server selects
`text/event-stream`, it fails explicitly because SSE parsing is outside this
increment. Response size and timeout come from configuration. HTTP status errors
are mapped to safe messages without returning server bodies, tokens, or session
identifiers. Requests, including mutations, are never automatically retried.

The committed disabled definition is equivalent to:

```json
{
  "name": "pharmacy-remote",
  "transport": "http",
  "url": "${PHARMACY_REMOTE_MCP_URL}",
  "token_env": "PHARMACY_MCP_HTTP_TOKEN",
  "timeout_seconds": 10,
  "max_request_bytes": 1000000,
  "max_response_bytes": 2000000,
  "mutable_tools": ["create_order"],
  "enabled": false
}
```

Both variable names must appear in the configuration's declared `variables`
array. The loader does not expand arbitrary environment variables. Copy the
configuration to a private local file and set `enabled` to `true` when testing a
running endpoint. For loopback:

```powershell
$env:PHARMACY_REMOTE_MCP_URL = "http://127.0.0.1:8080/mcp"
$env:PHARMACY_MCP_HTTP_TOKEN = "replace-with-the-same-local-test-token"
$env:PYTHONPATH = "src"
python -B -m pharmacy_mcp.host.cli --config path/to/private-config.json list-tools --server pharmacy-remote
```

Plain HTTP is accepted only for loopback URLs. A remote URL must use HTTPS. The
manager dynamically discovers the same seven definitions and registers them as
`pharmacy-remote__<tool>`. When local and HTTP Pharmacy are both enabled, they
remain separate and produce 14 namespaced tools. `create_order` requires the
same per-call mutation authorization policy as the local tool. `start_available`
keeps healthy stdio or HTTP clients available if another endpoint fails, and
cleanup closes each process or HTTP session independently.

The durable JSONL log records MCP payloads and safe HTTP status metadata with
`"transport":"http"`. Authorization and session headers are never passed to
the logger. Credential fields, prescription references, symptoms, allergies,
and current-medication inputs are redacted, and all payloads remain bounded.

## Persistence and deployment limitations

This is a local academic transport, not a production architecture. A future
Cloud Run container filesystem will be ephemeral. SQLite therefore cannot offer
durable persistence across restarts and cannot coordinate inventory across
multiple service instances. A classroom Cloud Run demonstration must be limited
to a maximum of one instance and may still lose its local database when that
instance is replaced.

A production system would require Cloud SQL or another managed transactional
database, stronger identity and authorization, TLS at the service boundary,
central session/state design, observability, and operational controls. Those
items are deliberately outside this increment.

Future work is limited to separate increments for Docker, a single-instance
Cloud Run demonstration, Wireshark capture and analysis, and a web interface.

## Reference

- [MCP Streamable HTTP transport, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
