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

The response with ID `2` contains four definitions in this order:

```json
["classify_symptoms","search_medications","get_medication_details","check_stock"]
```

Each definition includes `name`, `description`, and `inputSchema`. The exact
schemas are reproduced in the [server specification](mcp-server-specification.md#tools).

## Step 4: search the medication catalog

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"paracetamol","otc_only":true}},"id":3}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Found 1 medication(s): MED-ANA-001 - Acetaminofén 500 mg. Simulated catalog data; not medical advice."}],"structuredContent":{"query":"paracetamol","otc_only":true,"count":1,"medications":[{"sku":"MED-ANA-001","name":"Acetaminofén 500 mg","active_ingredient":"acetaminofén","therapeutic_category":"analgesic_antipyretic","requires_prescription":false,"price":{"amount":"18.95","currency":"GTQ"}}]}},"id":3}
```

The result contains both human-readable `content` and machine-readable
`structuredContent`. Search is case-insensitive and accent-insensitive, and can
match names, aliases, active ingredients, therapeutic categories, and SKUs.

## Step 5: retrieve complete medication details

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-ANA-001"}},"id":4}
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

## Step 6: check branch inventory

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001"}},"id":5}
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

For one branch only, send `"branch_id":"zona-5"` with the SKU. This tool is
read-only and never decrements inventory.

## Step 7: confirm `classify_symptoms` still works

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"classify_symptoms","arguments":{"symptoms":["fever","cough"]}},"id":6}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Classification: respiratory. Matched symptoms: fever, cough. Symptoms match the respiratory category. Educational use only; not a medical diagnosis."}]},"id":6}
```

## Step 8: observe a controlled lookup error

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANA-001","branch_id":"zona-10"}},"id":7}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Unknown branch: 'zona-10'."}],"isError":true},"id":7}
```

The response preserves request ID `7` and is successful at the JSON-RPC layer.
The `isError: true` member reports that the well-formed tool execution could not
complete its domain lookup.

## Step 9: terminate with EOF

Close the server's stdin:

- On Linux or macOS, press `Ctrl+D` on an empty input line.
- In a Windows console, press `Ctrl+Z`, then Enter.
- A parent MCP client can close the child process's stdin programmatically.

The server exits with code `0` and does not write a shutdown response. EOF is the
implemented clean-termination mechanism.

## Optional: run the complete exchange non-interactively

The following Bash command sends the lifecycle and all three read-only query
tools, then closes the pipe to produce EOF:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Scripted Demo Client","version":"1.0.0"}},"id":1}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' \
  '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"loratadina"}},"id":3}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-ANT-001"}},"id":4}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANT-001","branch_id":"mixco"}},"id":5}' \
  | PYTHONPATH=src python -m pharmacy_mcp.server.stdio
```

There should be five response lines: initialization, tool listing, and three tool
results. The initialized notification deliberately has no response.

PowerShell equivalent:

```powershell
$env:PYTHONPATH = "src"
@(
  '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Scripted Demo Client","version":"1.0.0"}},"id":1}'
  '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
  '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_medications","arguments":{"query":"loratadina"}},"id":3}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_medication_details","arguments":{"sku":"MED-ANT-001"}},"id":4}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"check_stock","arguments":{"sku":"MED-ANT-001","branch_id":"mixco"}},"id":5}'
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
