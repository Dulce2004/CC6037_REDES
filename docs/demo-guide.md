# Local Pharmacy MCP Stdio Demonstration Guide

This guide demonstrates the implemented local MCP server directly over its
newline-delimited stdio transport. It does not require an MCP SDK, HTTP server,
LLM, API key, or network connection.

## Preparation

Open a terminal in the repository root. The project declares Python 3.12 and
uses only the Python standard library. `requirements.txt` contains no third-party
packages, so dependency installation is unnecessary. If desired, the following
command is safe but has nothing external to install:

```bash
python -m pip install -r requirements.txt
```

Run the complete test suite before the demonstration:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B -m unittest discover -s tests -v
```

PowerShell equivalent:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONPATH = "src"
python -B -m unittest discover -s tests -v
```

The run should end with `OK`.

## Start the server

From a Bash-compatible shell, run exactly:

```bash
PYTHONPATH=src python -m pharmacy_mcp.server.stdio
```

From PowerShell, use:

```powershell
$env:PYTHONPATH = "src"
python -m pharmacy_mcp.server.stdio
```

The process persists simulated orders and stock in
`runtime/pharmacy.sqlite3`. For a fresh isolated demonstration, set
`PHARMACY_MCP_DATABASE_PATH` to a new file name inside `runtime/` before
starting the process. The directory is ignored by Git, and the source catalog
and inventory JSON files are never modified.

The process waits silently for input. Type or paste each request below as one
physical line and press Enter. Do not paste the formatted multi-line examples
from the technical specification into stdin: this transport uses one complete
JSON object per line.

## Step 1: initialize

Input:

```json
{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Manual Demo Client","version":"1.0.0"}},"id":1}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"protocolVersion":"2025-11-25","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"Pharmacy MCP Server","version":"0.1.0"}},"id":1}
```

The server has moved from `UNINITIALIZED` to `INITIALIZING`. Tool methods are not
available yet.

## Step 2: complete the handshake

Input:

```json
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
```

Expected stdout response: **none**.

This message has no `id`, so it is a notification. The absence of output is the
correct JSON-RPC behavior. The server is now `READY`.

## Step 3: list the registered tools

Input:

```json
{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}
```

The response with ID `2` contains seven definitions in this order:

```json
["assess_symptoms","search_medications","get_medication_details","check_interactions","check_stock","create_order","get_order_status"]
```

Each definition includes `name`, `description`, and `inputSchema`. The exact
schemas are reproduced in the [server specification](mcp-server-specification.md#tools).

## Step 4: assess natural-language symptoms

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"assess_symptoms","arguments":{"symptoms":"Tengo fiebre y dolor de garganta desde ayer","age":24,"duration_days":1}},"id":3}
```

Inspect `result.structuredContent`. It reports a simulated `mild` assessment,
the `respiratory` category, the recognized identifiers `fever` and
`sore_throat`, no urgent red flags, and
`medication_purchase_recommended: false`. The text result states that this is
not a diagnosis or medical advice.

For the urgent path, send `"Tengo tos y dificultad para respirar"`. The result
starts with an urgent-care instruction and explicitly says not to select or
purchase medication from the assessment.

## Step 5: search the medication catalog

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"paracetamol","otc_only":true}},"id":4}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Found 1 medication(s): MED-ANA-001 - Acetaminofén 500 mg. Simulated catalog data; not medical advice."}],"structuredContent":{"query":"paracetamol","otc_only":true,"count":1,"medications":[{"sku":"MED-ANA-001","name":"Acetaminofén 500 mg","active_ingredient":"acetaminofén","therapeutic_category":"analgesic_antipyretic","requires_prescription":false,"price":{"amount":"18.95","currency":"GTQ"}}]}},"id":4}
```

The result contains both human-readable `content` and machine-readable
`structuredContent`. Search is case-insensitive and accent-insensitive, and can
match names, aliases, active ingredients, therapeutic categories, and SKUs.

## Step 6: retrieve complete medication details

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-ANA-001"}},"id":5}
```

Inspect `result.structuredContent.medication`. It contains exactly these catalog
fields:

```json
{
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
```

The price amount is a string, not a floating-point value. The displayed dosage
and contraindications are simulated catalog information, not medical advice.

## Step 7: check simulated interactions and allergies

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_interactions","arguments":{"medication_sku":"MED-ANA-002","current_medications":["MED-GAS-001"],"allergies":["AINEs"]}},"id":6}
```

The controlled dataset produces two alerts: one simulated medication pair and
one allergy-term match. Inspect these safety fields:

```json
{"alert_count":2,"highest_severity":"high","exhaustive":false,"safety_established":false}
```

The result is not a guarantee of safety. It directs the user to professional
review and states that the check is simulated and non-exhaustive.

## Step 8: check branch inventory

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001"}},"id":7}
```

Because `branch_id` is omitted, `result.structuredContent.stock` contains all
three branches:

```json
[
  {"branch_id":"zona-5","branch_name":"Zona 5","quantity":25,"available":true},
  {"branch_id":"zona-15","branch_name":"Zona 15","quantity":12,"available":true},
  {"branch_id":"mixco","branch_name":"Mixco","quantity":0,"available":false}
]
```

For one branch only, send `"branch_id":"zona-5"` with the SKU. This tool does
not mutate stock itself, but it reads the same SQLite state changed by an order.

## Step 9: create an atomic simulated order

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"create_order","arguments":{"branch_id":"zona-5","items":[{"sku":"MED-ANA-001","quantity":2}]}},"id":8}
```

The result has status `created`, total `GTQ 37.90`, and a generated `ORD-...`
identifier. Copy that identifier for the next step. The quantity is committed
only if every order line can be fulfilled.

For a prescription-only catalog item such as `MED-RX-001`, include a simulated
reference such as `"prescription_id":"RX-DEMO-001"`. Only the `RX-...` format
is checked; this is not real prescription validation or purchase authorization.

## Step 10: retrieve the simulated order

Replace `ORD-COPIED-FROM-STEP-9` with the exact generated identifier:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_order_status","arguments":{"order_id":"ORD-COPIED-FROM-STEP-9"}},"id":9}
```

The result repeats the immutable item, price, prescription-scope, timestamp, and
current `created` status fields. It never returns the prescription identifier.

## Step 11: observe the committed stock

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001","branch_id":"zona-5"}},"id":10}
```

On a fresh database, the returned quantity is now `23`, proving that
`check_stock` and `create_order` share one state.

## Step 12: observe a controlled lookup error

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001","branch_id":"zona-10"}},"id":11}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Unknown branch: 'zona-10'."}],"isError":true},"id":11}
```

The response preserves request ID `11` and is successful at the JSON-RPC layer.
The `isError: true` member reports that the well-formed tool execution could not
complete its domain lookup.

## Step 13: terminate with EOF

Close the server's stdin:

- On Linux or macOS, press `Ctrl+D` on an empty input line.
- In a Windows console, press `Ctrl+Z`, then Enter.
- A parent MCP client can close the child process's stdin programmatically.

The server exits with code `0` and does not write a shutdown response. EOF is the
implemented clean-termination mechanism.

## Optional: run the complete exchange non-interactively

The following Bash command sends the lifecycle, the five consultation tools,
creates one OTC order, observes the changed stock, and then produces EOF:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Scripted Demo Client","version":"1.0.0"}},"id":1}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' \
  '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"assess_symptoms","arguments":{"symptoms":"Tengo estornudos y congestión nasal","age":24,"duration_days":1}},"id":3}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"loratadina"}},"id":4}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-ANT-001"}},"id":5}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_interactions","arguments":{"medication_sku":"MED-ANT-001","current_medications":[],"allergies":[]}},"id":6}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANT-001","branch_id":"mixco"}},"id":7}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"create_order","arguments":{"branch_id":"mixco","items":[{"sku":"MED-ANT-001","quantity":1}]}},"id":8}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANT-001","branch_id":"mixco"}},"id":9}' \
  | PYTHONPATH=src python -m pharmacy_mcp.server.stdio
```

There should be nine response lines: initialization, tool listing, and seven
tool results. The initialized notification deliberately has no response.

PowerShell equivalent:

```powershell
$env:PYTHONPATH = "src"
@(
  '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Scripted Demo Client","version":"1.0.0"}},"id":1}'
  '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
  '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"assess_symptoms","arguments":{"symptoms":"Tengo estornudos y congestión nasal","age":24,"duration_days":1}},"id":3}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"loratadina"}},"id":4}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-ANT-001"}},"id":5}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_interactions","arguments":{"medication_sku":"MED-ANT-001","current_medications":[],"allergies":[]}},"id":6}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANT-001","branch_id":"mixco"}},"id":7}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"create_order","arguments":{"branch_id":"mixco","items":[{"sku":"MED-ANT-001","quantity":1}]}},"id":8}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANT-001","branch_id":"mixco"}},"id":9}'
) | python -m pharmacy_mcp.server.stdio
```

## Troubleshooting

### `No module named pharmacy_mcp`

Run the command from the repository root and set `PYTHONPATH` to `src` as shown
above.

### Parse errors for valid-looking JSON

Verify that each message occupies one physical line. A blank line or a
pretty-printed multi-line object is not valid framing for this implementation.

### `-32002 Server not initialized`

Send `initialize`, wait for its response, and then send
`notifications/initialized` without an `id` before calling tools.

### No output after a notification

That is expected. JSON-RPC notifications have no `id` and the server must not
respond to them.

### Protect stdout

When integrating the process with another client, treat every stdout line as a
protocol message. Send human-readable logging and diagnostics to stderr only.

## Further reading

- [Local server specification](mcp-server-specification.md)
- [Project README](../README.md)
