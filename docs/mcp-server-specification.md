# Local Pharmacy MCP Server Specification

## Overview

This document specifies the behavior implemented by the local PharmaMCP server.
It is an academic, deterministic pharmacy example built manually on JSON-RPC 2.0
and a limited subset of Model Context Protocol (MCP) revision `2025-11-25`.

The project intentionally does not use FastMCP, an MCP SDK, or an external
JSON-RPC library. Python data classes validate protocol messages, a stateful
server dispatches MCP methods, and a small stdio adapter connects that server to
a client process.

The implementation is not a complete MCP server. The only advertised server
primitive is tools, and the only currently registered tool is
`classify_symptoms`.

### Server identity and capabilities

| Property | Implemented value |
| --- | --- |
| Protocol version | `2025-11-25` |
| Server name | `Pharmacy MCP Server` |
| Server version | `0.1.0` |
| Server capability | `{"tools":{"listChanged":false}}` |
| Registered tool | `classify_symptoms` |
| External dependencies | None |
| MCP SDK | None |

`listChanged: false` means this server does not emit tool-list change
notifications.

## Transport

### Stdio framing

The process entry point is:

```bash
PYTHONPATH=src python -m pharmacy_mcp.server.stdio
```

PowerShell equivalent:

```powershell
$env:PYTHONPATH = "src"
python -m pharmacy_mcp.server.stdio
```

The transport has the following behavior:

- Standard input (`stdin`) carries client-to-server messages.
- Standard output (`stdout`) carries server-to-client JSON-RPC responses only.
- Standard error (`stderr`) is reserved for unexpected transport diagnostics.
- All three process streams are configured for UTF-8.
- Each physical input line must contain exactly one complete JSON object.
- Each serialized response is followed by one LF newline and immediately
  flushed.
- A notification produces no stdout line.
- EOF on stdin ends the loop cleanly with process exit code `0`.
- An unexpected exception in the transport loop writes a short diagnostic to
  stderr and returns exit code `1`.

This project therefore uses NDJSON (newline-delimited JSON) framing for stdio.
JSON may contain escaped `\n` characters inside string values, but an individual
protocol message must not be pretty-printed across physical lines. Blank lines
are not ignored; they produce a JSON-RPC parse error.

One `PharmacyMCPServer` instance serves the complete lifetime of the process, so
its lifecycle state is preserved between input lines.

### Transport-level limitations

- Input is processed sequentially, one line at a time.
- JSON-RPC batch arrays are not supported.
- Multi-line JSON documents are not supported.
- The server does not send unsolicited requests or notifications.
- There is no MCP shutdown method. The client terminates the local process by
  closing stdin.
- There is no HTTP, socket, URL, port, TLS, or remote transport.
- The existing direct in-memory client remains separate from this adapter.

## Lifecycle

The expected session sequence is:

1. The client sends an `initialize` request with an `id`.
2. The server validates the exact protocol version and returns its identity and
   capabilities.
3. The client sends `notifications/initialized` without an `id`.
4. The server enters `READY` and writes no response for the notification.
5. The client may send `tools/list` and `tools/call` requests.
6. The client closes stdin when finished; EOF terminates the process.

### Implemented states

| State | How it is entered | Implemented behavior |
| --- | --- | --- |
| `UNINITIALIZED` | Initial state | A valid `initialize` request is accepted. `tools/list` and `tools/call` return `-32002`. An early `notifications/initialized` notification is ignored. |
| `INITIALIZING` | Successful `initialize` | `notifications/initialized` transitions the server to `READY`. Tool requests still return `-32002`. A second `initialize` returns `-32600`. |
| `READY` | Valid initialized notification after initialization | `tools/list` and `tools/call` are available. A repeated initialized notification is ignored. A new `initialize` returns `-32600`. |

State changes apply to the active process only and are not persisted after EOF.
An unsupported protocol version leaves the server in `UNINITIALIZED`.

All notifications are response-free. If a server handler detects an error while
processing a message without an `id`, that error is suppressed rather than being
written to stdout.

## JSON-RPC messages

### Requests

A request contains:

```json
{
  "jsonrpc": "2.0",
  "method": "method/name",
  "params": {},
  "id": 1
}
```

- `jsonrpc` is required and must be exactly `"2.0"`.
- `method` is required and must be a string.
- `params` is optional at the message layer and may be an object or array.
  Every implemented MCP method, however, requires object parameters when the
  member is present. Omitted parameters are treated as an empty object.
- `id` may be a string, finite number, or JSON `null`. Booleans are rejected as
  IDs.

### Successful responses

A successful response contains the same request `id`:

```json
{
  "jsonrpc": "2.0",
  "result": {},
  "id": 1
}
```

### Error responses

An error response replaces `result` with an error object:

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Invalid params"
  },
  "id": 1
}
```

The response classes can represent optional error `data`, but the current server
does not add a `data` member to its generated errors.

### Notifications and `id: null`

A notification is distinguished by the complete absence of the `id` member:

```json
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
```

The server never responds to a notification. By contrast, an explicit
`"id": null` is treated by this implementation as a request, not a
notification, and receives a response whose `id` is also `null`.

Clients should use unique, non-null IDs for ordinary requests so responses can be
correlated unambiguously. The server preserves the original ID in both success
and method-level error responses. Parse errors and invalid messages for which no
valid request ID is available use `"id": null`.

## MCP methods

| Method | Message type | Effective state | Success result |
| --- | --- | --- | --- |
| `initialize` | Request with `id` | `UNINITIALIZED` | Protocol version, capabilities, and server information |
| `notifications/initialized` | Notification without `id` | Transitions only from `INITIALIZING` | No response |
| `tools/list` | Request with `id` | `READY` | All registered tool definitions |
| `tools/call` | Request with `id` | `READY` | Tool result content |

Unknown methods return `-32601` for requests. Unknown-method notifications do
not produce a response.

### `initialize`

`initialize` must be the first successful request. Its parameters must be an
object containing:

- `protocolVersion`: the non-empty string `2025-11-25`.
- `capabilities`: an object. The server validates but does not otherwise use the
  client capability entries.
- `clientInfo`: an object containing non-empty string fields `name` and
  `version`.

Valid input line:

```json
{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Manual Demo Client","version":"1.0.0"}},"id":1}
```

Successful response line:

```json
{"jsonrpc":"2.0","result":{"protocolVersion":"2025-11-25","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"Pharmacy MCP Server","version":"0.1.0"}},"id":1}
```

Implemented error behavior is:

- If `initialize` is sent without an ID, it is a notification. The handler
  rejects that form internally, but notification error suppression means no
  response is written and the state remains `UNINITIALIZED`.
- A repeated `initialize` request with an ID returns `-32600`.
- Invalid params, required fields, or protocol versions return `-32602` for a
  request with an ID.

### `notifications/initialized`

This method must be sent as a notification, without an `id`. Params may be
omitted or must be an object.

```json
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
```

The server writes no response. When its state is `INITIALIZING`, it transitions
to `READY`. In `UNINITIALIZED` or `READY`, the valid notification is accepted as
a no-op. If an `id` is supplied, the message is a request and receives `-32600`.

### `tools/list`

`tools/list` requires `READY`. Params may be omitted or must be an object. The
current implementation returns all registered tools in one response; it does not
implement pagination or `nextCursor`.

Request line:

```json
{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}
```

Response, formatted for readability:

```json
{
  "jsonrpc": "2.0",
  "result": {
    "tools": [
      {
        "name": "classify_symptoms",
        "description": "Classifies controlled symptom identifiers into educational categories.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "symptoms": {
              "type": "array",
              "items": {
                "type": "string",
                "enum": [
                  "abdominal_pain",
                  "cough",
                  "diarrhea",
                  "fever",
                  "itchy_eyes",
                  "nasal_congestion",
                  "nausea",
                  "sneezing",
                  "sore_throat"
                ]
              },
              "minItems": 1
            }
          },
          "required": ["symptoms"],
          "additionalProperties": false
        }
      }
    ]
  },
  "id": 2
}
```

Possible method errors include `-32002` before `READY` and `-32602` when params
is not an object.

### `tools/call`

`tools/call` requires `READY` and object params:

- `name`: a non-empty string that exactly matches a registered tool.
- `arguments`: an object; if omitted, it defaults to `{}`.

The server checks the tool schema's `required` list before invoking the handler.
An unknown tool, invalid `arguments`, or missing required argument returns
`-32602`.

Request line:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"classify_symptoms","arguments":{"symptoms":["fever","cough"]}},"id":3}
```

Successful response line:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Classification: respiratory. Matched symptoms: fever, cough. Symptoms match the respiratory category. Educational use only; not a medical diagnosis."}]},"id":3}
```

Possible method errors include `-32002`, `-32602`, and `-32603`.

## Tools

### `classify_symptoms`

**Published description:** `Classifies controlled symptom identifiers into
educational categories.`

**Exact published `inputSchema`:**

```json
{
  "type": "object",
  "properties": {
    "symptoms": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
          "abdominal_pain",
          "cough",
          "diarrhea",
          "fever",
          "itchy_eyes",
          "nasal_congestion",
          "nausea",
          "sneezing",
          "sore_throat"
        ]
      },
      "minItems": 1
    }
  },
  "required": ["symptoms"],
  "additionalProperties": false
}
```

#### Runtime validation and classification

- `symptoms` is required and must be a non-empty array.
- Every item must be a string.
- Each item is trimmed and converted to lowercase.
- Normalized identifiers must contain lowercase words separated only by single
  underscores.
- Every normalized identifier must belong to the published enum.
- Duplicates are removed while preserving first-occurrence order.
- At least two distinct matching symptoms are required for a category.
- Supported categories are:
  - `respiratory`: `fever`, `cough`, `sore_throat`.
  - `allergy`: `sneezing`, `nasal_congestion`, `itchy_eyes`.
  - `gastrointestinal`: `nausea`, `diarrhea`, `abdominal_pain`.
- If no category reaches two matches, or the highest score is tied, the result is
  `unclassified`.

The project does not run a complete JSON Schema validator. In particular,
although the published schema declares `additionalProperties: false`, the
current hand-written handler does not reject additional argument properties.

#### Result structure

The tool returns an MCP result with exactly one text content item:

```json
{
  "content": [
    {
      "type": "text",
      "text": "Classification: <category>. Matched symptoms: <list or none>. <explanation> Educational use only; not a medical diagnosis."
    }
  ]
}
```

The result does not currently include `structuredContent`, `outputSchema`, or an
explicit `isError` member.

#### Valid example

Arguments:

```json
{"symptoms":["sneezing","nasal_congestion","itchy_eyes"]}
```

Result:

```json
{"content":[{"type":"text","text":"Classification: allergy. Matched symptoms: sneezing, nasal_congestion, itchy_eyes. Symptoms match the allergy category. Educational use only; not a medical diagnosis."}]}
```

#### Invalid example

Arguments:

```json
{"symptoms":["fever","magic_symptom"]}
```

JSON-RPC error when used in a `tools/call` request with ID `4`:

```json
{"jsonrpc":"2.0","error":{"code":-32602,"message":"Unknown symptom: 'magic_symptom'."},"id":4}
```

Other invalid cases include a missing or empty array, non-string elements, empty
identifiers, invalid identifier formatting, and unsupported identifiers. These
conditions return `-32602` for a request.

## Errors

| Code | Name | When it occurs |
| ---: | --- | --- |
| `-32700` | Parse error | A physical input line is not valid JSON, is blank, or contains an invalid JSON numeric constant. |
| `-32600` | Invalid Request | A JSON value is not a valid JSON-RPC message object, the version or ID is invalid, an incoming message is a response rather than a request, initialization is repeated, or an initialization method uses the wrong request/notification form. |
| `-32601` | Method not found | A request names a method not registered by the server. |
| `-32602` | Invalid params | Method params, initialization fields, tool name, tool arguments, or symptom values fail validation. |
| `-32603` | Internal error | An unexpected method/tool handler failure occurs or a success response cannot be constructed. |
| `-32002` | Server not initialized | `tools/list` or `tools/call` is requested before the server reaches `READY`. |

Error messages provide a concise reason but should not be parsed as a stable API.
Clients should branch on the numeric code. Unexpected transport failures are not
JSON-RPC errors: they are reported on stderr and cause process exit code `1`.

## Security and safety

- The server and all pharmacy data are simulated for academic use.
- Symptom classification is deterministic educational output, not medical
  advice, diagnosis, triage, or a treatment recommendation.
- A user should seek qualified medical care for health concerns and urgent help
  for emergencies.
- All client messages and tool arguments are untrusted input. The server performs
  type and domain validation, but callers must not infer broader sanitization or
  authorization guarantees.
- Stdout is reserved for valid JSON-RPC protocol output. Debug prints, logs, and
  human-readable prompts must not be written there.
- The local stdio transport has no authentication or authorization layer. Access
  is controlled only by the operating-system process relationship and the
  permissions of the user launching the server.
- The implementation does not contact an LLM, external API, database, or remote
  service and does not require credentials.

## Compatibility and limitations

- The server accepts only MCP protocol version `2025-11-25` during
  initialization.
- The available process transport is local stdio with UTF-8 NDJSON framing. The
  in-memory Python client remains available, but there is no HTTP transport.
- Requests are processed synchronously and sequentially; concurrent execution is
  not implemented.
- Only the tools capability is declared. Resources, prompts, roots, sampling,
  elicitation, tasks, subscriptions, cancellation, progress, and protocol
  logging are not implemented.
- The tool list is static, `listChanged` is false, and tool-list change
  notifications are not emitted.
- `tools/list` does not implement pagination.
- Tool argument and domain validation failures are returned as JSON-RPC
  `-32602` errors; this subset does not return tool execution failures inside a
  result with `isError: true`.
- Runtime validation covers the checks documented above but is not a complete
  JSON Schema validator.
- JSON-RPC batches and multi-line JSON messages are unsupported.
- There is no LLM integration, natural-language interpretation, HTTP endpoint,
  remote server, or network authentication.
- The medication catalog and branch inventory exist only as domain modules; they
  are not MCP tools.

## Planned tools

The following names describe future pharmacy workflow goals only. They are not
registered or callable in the current server:

- `assess_symptoms`
- `search_medications`
- `get_medication_details`
- `check_interactions`
- `check_stock`
- `create_order`
- `get_order_status`

## References

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [MCP Specification, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP architecture, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/architecture)
- [MCP lifecycle, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP transports, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP tools, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
