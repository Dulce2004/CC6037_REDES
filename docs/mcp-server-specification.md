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
primitive is tools. Five tools are registered: `assess_symptoms`,
`search_medications`, `get_medication_details`, `check_interactions`, and
`check_stock`.

### Server identity and capabilities

| Property | Implemented value |
| --- | --- |
| Protocol version | `2025-11-25` |
| Server name | `Pharmacy MCP Server` |
| Server version | `0.1.0` |
| Server capability | `{"tools":{"listChanged":false}}` |
| Registered tools | `assess_symptoms`, `search_medications`, `get_medication_details`, `check_interactions`, `check_stock` |
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
        "name": "assess_symptoms",
        "description": "Assesses natural-language symptoms using controlled simulated rules, including severity and urgent red flags.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "symptoms": {
              "type": "string",
              "minLength": 1,
              "maxLength": 1000
            },
            "age": {"type": "integer", "minimum": 0, "maximum": 120},
            "duration_days": {
              "type": "integer",
              "minimum": 0,
              "maximum": 365
            }
          },
          "required": ["symptoms"],
          "additionalProperties": false
        }
      },
      {
        "name": "search_medications",
        "description": "Searches the simulated medication catalog by text and optional OTC status.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": {"type": "string", "minLength": 1},
            "otc_only": {"type": "boolean", "default": false}
          },
          "required": ["query"],
          "additionalProperties": false
        }
      },
      {
        "name": "get_medication_details",
        "description": "Returns complete simulated catalog details for one medication SKU.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "sku": {
              "type": "string",
              "minLength": 1,
              "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
            }
          },
          "required": ["sku"],
          "additionalProperties": false
        }
      },
      {
        "name": "check_interactions",
        "description": "Checks a requested medication against current medications and allergies using controlled, non-exhaustive simulated rules.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "medication_sku": {
              "type": "string",
              "minLength": 1,
              "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
            },
            "current_medications": {
              "type": "array",
              "items": {
                "type": "string",
                "minLength": 1,
                "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
              },
              "maxItems": 20,
              "uniqueItems": true,
              "default": []
            },
            "allergies": {
              "type": "array",
              "items": {"type": "string", "minLength": 1, "maxLength": 200},
              "maxItems": 20,
              "uniqueItems": true,
              "default": []
            }
          },
          "required": ["medication_sku"],
          "additionalProperties": false
        }
      },
      {
        "name": "check_stock",
        "description": "Checks read-only inventory for one medication SKU at one or all branches.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "sku": {
              "type": "string",
              "minLength": 1,
              "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
            },
            "branch_id": {
              "type": "string",
              "enum": ["mixco", "zona-15", "zona-5"]
            }
          },
          "required": ["sku"],
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

For the pharmacy query tools, that protocol error is limited to malformed
calls, such as missing or additional fields, wrong JSON types, empty strings,
or invalid identifier syntax. A well-formed call that reaches the domain layer
but cannot find a medication or branch is a successful JSON-RPC response whose
tool result contains `isError: true`.

Request line:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"assess_symptoms","arguments":{"symptoms":"Tengo fiebre y tos","age":24,"duration_days":1}},"id":3}
```

Successful response line:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Severity: mild. Category: respiratory. Recognized symptoms: fever, cough. Red flags: none. Monitor symptoms and ask a qualified pharmacist for general information; seek medical care if symptoms worsen or persist. No medication purchase is recommended by this result. Academic simulated assessment; not a diagnosis, medical advice, or substitute for a qualified healthcare professional."}],"structuredContent":{"severity":"mild","category":"respiratory","recognized_symptoms":["fever","cough"],"matched_symptoms":["fever","cough"],"red_flags":[],"reasons":["short_duration_without_detected_red_flags"],"age":24,"duration_days":1,"recommended_action":"Monitor symptoms and ask a qualified pharmacist for general information; seek medical care if symptoms worsen or persist.","medication_purchase_recommended":false,"disclaimer":"Academic simulated assessment; not a diagnosis, medical advice, or substitute for a qualified healthcare professional."}},"id":3}
```

Possible method errors include `-32002`, `-32602`, and `-32603`.

## Tools

### `assess_symptoms`

**Published description:** `Assesses natural-language symptoms using controlled
simulated rules, including severity and urgent red flags.`

**Exact published `inputSchema`:**

```json
{
  "type": "object",
  "properties": {
    "symptoms": {
      "type": "string",
      "minLength": 1,
      "maxLength": 1000
    },
    "age": {"type": "integer", "minimum": 0, "maximum": 120},
    "duration_days": {
      "type": "integer",
      "minimum": 0,
      "maximum": 365
    }
  },
  "required": ["symptoms"],
  "additionalProperties": false
}
```

#### Runtime assessment behavior

- `symptoms` is required natural-language text. It is limited to 1000
  characters and must contain a letter or number.
- `age` and `duration_days` are optional non-boolean integers in the documented
  ranges. Explicit JSON `null` is invalid.
- A controlled, accent-insensitive Spanish and English phrase table extracts
  the same symptom identifiers used by the original internal classifier.
- The internal categories remain `respiratory`, `allergy`, and
  `gastrointestinal`; an assessment may also have a null category.
- Severity is exactly `mild`, `moderate`, or `urgent`.
- Controlled urgent red flags include difficulty breathing, chest pain,
  confusion, fainting, severe bleeding, blue lips, and face or throat swelling.
- Any detected urgent red flag takes priority over category and duration. The
  result immediately directs the user to urgent medical care and explicitly
  says not to select or purchase medication from that result.
- Age, prolonged duration, prolonged fever, or lack of a controlled category
  can raise a non-urgent result to `moderate`.
- This tool does not diagnose, recommend a product, or authorize a purchase.

#### Result structure

Every result contains readable `content` plus `structuredContent` with severity,
category, recognized symptoms, red flags, reasons, context, action, a false
`medication_purchase_recommended` flag, and a medical disclaimer.

Example arguments:

```json
{"symptoms":"Tengo fiebre y dolor de garganta desde ayer","age":24,"duration_days":1}
```

Relevant structured result:

```json
{
  "severity": "mild",
  "category": "respiratory",
  "recognized_symptoms": ["fever", "sore_throat"],
  "matched_symptoms": ["fever", "sore_throat"],
  "red_flags": [],
  "reasons": ["short_duration_without_detected_red_flags"],
  "age": 24,
  "duration_days": 1,
  "recommended_action": "Monitor symptoms and ask a qualified pharmacist for general information; seek medical care if symptoms worsen or persist.",
  "medication_purchase_recommended": false,
  "disclaimer": "Academic simulated assessment; not a diagnosis, medical advice, or substitute for a qualified healthcare professional."
}
```

`classify_symptoms` is retained only as an internal deterministic helper. It is
not listed by `tools/list`, and calling that old public name returns `-32602`
because no such tool is registered.

### `search_medications`

**Published description:** `Searches the simulated medication catalog by text
and optional OTC status.`

**Exact published `inputSchema`:**

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "minLength": 1
    },
    "otc_only": {
      "type": "boolean",
      "default": false
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

#### Runtime validation and search behavior

- `query` is required and must be a non-empty string containing searchable
  characters.
- Search is case-insensitive and accent-insensitive. Surrounding whitespace is
  removed, and underscores and hyphens are treated as word separators.
- A substring may match a medication SKU, name, alias, active ingredient, or
  therapeutic category.
- `otc_only` is optional, defaults to `false`, and must be a JSON boolean when
  supplied.
- When `otc_only` is `true`, medications whose `requires_prescription` field is
  true are excluded.
- Result order is the stable order of the validated catalog.
- No matches is a successful result with `count: 0` and an empty array.
- Unexpected arguments are rejected with `-32602`.

#### Result structure

The result contains one text item and machine-readable `structuredContent`:

```json
{
  "content": [
    {
      "type": "text",
      "text": "Found 1 medication(s): MED-ANA-001 - Acetaminofén 500 mg. Simulated catalog data; not medical advice."
    }
  ],
  "structuredContent": {
    "query": "paracetamol",
    "otc_only": false,
    "count": 1,
    "medications": [
      {
        "sku": "MED-ANA-001",
        "name": "Acetaminofén 500 mg",
        "active_ingredient": "acetaminofén",
        "therapeutic_category": "analgesic_antipyretic",
        "requires_prescription": false,
        "price": {"amount": "18.95", "currency": "GTQ"}
      }
    ]
  }
}
```

Money is serialized as a decimal string plus currency, never as a JSON float.

#### Valid example

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"500 mg","otc_only":true}},"id":5}
```

The controlled catalog returns only `MED-ANA-001`; the two matching antibiotic
products are excluded because they require a prescription.

#### Invalid example

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"fever","otc_only":"true"}},"id":6}
```

```json
{"jsonrpc":"2.0","error":{"code":-32602,"message":"'otc_only' must be a boolean."},"id":6}
```

### `get_medication_details`

**Published description:** `Returns complete simulated catalog details for one
medication SKU.`

**Exact published `inputSchema`:**

```json
{
  "type": "object",
  "properties": {
    "sku": {
      "type": "string",
      "minLength": 1,
      "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
    }
  },
  "required": ["sku"],
  "additionalProperties": false
}
```

#### Runtime validation and lookup behavior

- `sku` is required and must be a non-empty string.
- `sku` must contain letters or digits separated by single hyphens.
- Surrounding whitespace is removed and lowercase letters are converted to
  uppercase before lookup.
- If the normalized, well-formed SKU does not exist, the tool returns
  `isError: true` inside a successful JSON-RPC response.
- Unexpected arguments are rejected with `-32602`.

#### Result structure

The text item gives a short summary. `structuredContent.medication` contains all
real catalog fields:

```json
{
  "content": [
    {
      "type": "text",
      "text": "MED-ANA-001 - Acetaminofén 500 mg; active ingredient: acetaminofén; category: analgesic_antipyretic; price: GTQ 18.95; prescription required: no. Simulated catalog data; not medical advice."
    }
  ],
  "structuredContent": {
    "medication": {
      "sku": "MED-ANA-001",
      "name": "Acetaminofén 500 mg",
      "aliases": ["paracetamol 500 mg", "acetaminofen"],
      "active_ingredient": "acetaminofén",
      "therapeutic_category": "analgesic_antipyretic",
      "dosage_information": "Caja con 20 tabletas de 500 mg; dato de presentación del catálogo.",
      "contraindications": [
        "Alergia al acetaminofén",
        "Enfermedad hepática grave"
      ],
      "requires_prescription": false,
      "price": {"amount": "18.95", "currency": "GTQ"}
    }
  }
}
```

The Spanish strings above are simulated catalog values, not medical guidance.

#### Valid example

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-RX-001"}},"id":7}
```

The result reports `requires_prescription: true` for this catalog item. This
read-only tool does not validate a prescription or create an order.

#### Domain lookup failure example

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-MISSING"}},"id":8}
```

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Unknown medication SKU: 'MED-MISSING'."}],"isError":true},"id":8}
```

### `check_interactions`

**Published description:** `Checks a requested medication against current
medications and allergies using controlled, non-exhaustive simulated rules.`

**Exact published `inputSchema`:**

```json
{
  "type": "object",
  "properties": {
    "medication_sku": {
      "type": "string",
      "minLength": 1,
      "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
    },
    "current_medications": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1,
        "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
      },
      "maxItems": 20,
      "uniqueItems": true,
      "default": []
    },
    "allergies": {
      "type": "array",
      "items": {"type": "string", "minLength": 1, "maxLength": 200},
      "maxItems": 20,
      "uniqueItems": true,
      "default": []
    }
  },
  "required": ["medication_sku"],
  "additionalProperties": false
}
```

#### Runtime lookup behavior

- `medication_sku` identifies the requested catalog medication.
- `current_medications` and `allergies` are optional arrays that default to
  empty. They are limited to 20 unique values.
- Medication identifiers are normalized to uppercase and checked against the
  existing validated catalog.
- Interaction pairs and allergy terms come only from the validated,
  read-only `interactions.json` dataset.
- A well-formed unknown requested or current SKU returns a successful JSON-RPC
  response with `isError: true`. Malformed arguments return `-32602`.
- Allergy matching is accent-insensitive and limited to controlled terms for the
  requested SKU.
- A result with no alerts explicitly does not establish medication safety.
- Prescription items are identified, but the tool never recommends or
  authorizes them.
- Every result states that the check is simulated, non-exhaustive, and not a
  replacement for professional review.

#### Example with two simulated alerts

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_interactions","arguments":{"medication_sku":"MED-ANA-002","current_medications":["MED-GAS-001"],"allergies":["AINEs"]}},"id":9}
```

The successful result contains `alert_count: 2`, `highest_severity: "high"`,
one `medication_interaction`, one `allergy_alert`, `exhaustive: false`,
`safety_established: false`, and the safety disclaimer. Alert severities describe
only the controlled academic dataset and are not clinical conclusions.

### `check_stock`

**Published description:** `Checks read-only inventory for one medication SKU at
one or all branches.`

**Exact published `inputSchema`:**

```json
{
  "type": "object",
  "properties": {
    "sku": {
      "type": "string",
      "minLength": 1,
      "pattern": "^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
    },
    "branch_id": {
      "type": "string",
      "enum": ["mixco", "zona-15", "zona-5"]
    }
  },
  "required": ["sku"],
  "additionalProperties": false
}
```

#### Runtime validation and inventory behavior

- `sku` is required, trimmed, converted to uppercase, and validated through the
  inventory repository.
- `branch_id` is optional. Its published schema enumerates `zona-5`, `zona-15`,
  and `mixco`. At runtime, non-empty identifiers use letters or digits separated
  by single hyphens; case and surrounding whitespace are normalized before
  lookup.
- When `branch_id` is present, one branch record is returned.
- When `branch_id` is omitted, the existing
  `get_stock_across_branches` repository API returns all three records in catalog
  order: Zona 5, Zona 15, and Mixco.
- `available` is true exactly when `quantity` is greater than zero.
- Unknown branches and SKUs reported by `InventoryLookupError` produce a
  successful JSON-RPC response with `isError: true` in the tool result.
- The tool never changes inventory. Repeated calls return the same controlled
  data.
- Unexpected arguments are rejected with `-32602`.

#### Result structure

```json
{
  "content": [
    {
      "type": "text",
      "text": "Stock for MED-ANA-001 - Acetaminofén 500 mg: Zona 5 (zona-5): 25. Simulated read-only inventory."
    }
  ],
  "structuredContent": {
    "sku": "MED-ANA-001",
    "medication_name": "Acetaminofén 500 mg",
    "stock": [
      {
        "branch_id": "zona-5",
        "branch_name": "Zona 5",
        "quantity": 25,
        "available": true
      }
    ]
  }
}
```

#### Valid branch-level example

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001","branch_id":"zona-5"}},"id":9}
```

Omit `branch_id` to receive the same SKU's inventory in every branch.

#### Domain lookup failure example

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001","branch_id":"zona-10"}},"id":10}
```

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Unknown branch: 'zona-10'."}],"isError":true},"id":10}
```

## Errors

| Code | Name | When it occurs |
| ---: | --- | --- |
| `-32700` | Parse error | A physical input line is not valid JSON, is blank, or contains an invalid JSON numeric constant. |
| `-32600` | Invalid Request | A JSON value is not a valid JSON-RPC message object, the version or ID is invalid, an incoming message is a response rather than a request, initialization is repeated, or an initialization method uses the wrong request/notification form. |
| `-32601` | Method not found | A request names a method not registered by the server. |
| `-32602` | Invalid params | Method params, initialization fields, tool name, or the shape, types, required fields, additional fields, empty strings, and identifier syntax of tool arguments fail validation. |
| `-32603` | Internal error | An unexpected method/tool handler failure occurs or a success response cannot be constructed. |
| `-32002` | Server not initialized | `tools/list` or `tools/call` is requested before the server reaches `READY`. |

JSON-RPC error messages provide a concise reason but should not be parsed as a
stable API; clients should branch on the numeric code. For a successful
`tools/call` response, clients should inspect the optional tool-result `isError`
member. Unexpected transport failures are not JSON-RPC errors: they are reported
on stderr and cause process exit code `1`.

## Security and safety

- The server and all pharmacy data are simulated for academic use.
- Symptom assessment is deterministic educational output, not medical advice,
  diagnosis, or a treatment recommendation. Urgent red flags always prioritize
  seeking immediate care over medication or purchase information.
- Interaction and allergy results are based on a small simulated dataset. They
  are not exhaustive and cannot establish that a medication is safe.
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
- Malformed tool calls return JSON-RPC `-32602` errors. Well-formed medication
  or branch lookups that fail in the domain repositories return successful
  JSON-RPC responses whose tool result has `isError: true`.
- Runtime validation covers the checks documented above but is not a complete
  JSON Schema validator.
- JSON-RPC batches and multi-line JSON messages are unsupported.
- Natural-language assessment uses only controlled phrase matching; there is no
  LLM interpretation, HTTP endpoint, remote server, or network authentication.
- All five tools are read-only. No tool changes inventory or creates an order.

## Planned tools

The following names describe future pharmacy workflow goals only. They are not
registered or callable in the current server:

- `create_order`
- `get_order_status`

## References

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
- [MCP Specification, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP architecture, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/architecture)
- [MCP lifecycle, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
- [MCP transports, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP tools, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
