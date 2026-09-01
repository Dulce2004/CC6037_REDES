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

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"tools":[{"name":"classify_symptoms","description":"Classifies controlled symptom identifiers into educational categories.","inputSchema":{"type":"object","properties":{"symptoms":{"type":"array","items":{"type":"string","enum":["abdominal_pain","cough","diarrhea","fever","itchy_eyes","nasal_congestion","nausea","sneezing","sore_throat"]},"minItems":1}},"required":["symptoms"],"additionalProperties":false}}]},"id":2}
```

Confirm that exactly one tool, `classify_symptoms`, is present. Medication search,
stock lookup, interaction checking, and ordering tools are not implemented yet.

## Step 4: invoke the tool successfully

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"classify_symptoms","arguments":{"symptoms":["fever","cough"]}},"id":3}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Classification: respiratory. Matched symptoms: fever, cough. Symptoms match the respiratory category. Educational use only; not a medical diagnosis."}]},"id":3}
```

The output is deterministic and educational. It is not a diagnosis or treatment
recommendation.

## Step 5: observe a validation error

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"classify_symptoms","arguments":{"symptoms":["fever","magic_symptom"]}},"id":4}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","error":{"code":-32602,"message":"Unknown symptom: 'magic_symptom'."},"id":4}
```

The response preserves request ID `4`, allowing the client to correlate the
error with its request.

## Step 6: observe an unclassified result

Input:

```json
{"jsonrpc":"2.0","method":"tools/call","params":{"name":"classify_symptoms","arguments":{"symptoms":["fever","itchy_eyes"]}},"id":5}
```

Expected stdout response:

```json
{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"Classification: unclassified. Matched symptoms: none. No supported category matches the provided symptoms. Educational use only; not a medical diagnosis."}]},"id":5}
```

No category has the two distinct matches required by the controlled rules.

## Step 7: terminate with EOF

Close the server's stdin:

- On Linux or macOS, press `Ctrl+D` on an empty input line.
- In a Windows console, press `Ctrl+Z`, then Enter.
- A parent MCP client can close the child process's stdin programmatically.

The server exits with code `0` and does not write a shutdown response. EOF is the
implemented clean-termination mechanism.

## Optional: run the complete exchange non-interactively

The following Bash command sends the lifecycle and one tool call, then closes the
pipe to produce EOF:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Scripted Demo Client","version":"1.0.0"}},"id":1}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' \
  '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}' \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"classify_symptoms","arguments":{"symptoms":["nausea","diarrhea"]}},"id":3}' \
  | PYTHONPATH=src python -m pharmacy_mcp.server.stdio
```

There should be three response lines: initialization, tool listing, and the tool
result. The initialized notification deliberately has no response.

PowerShell equivalent:

```powershell
$env:PYTHONPATH = "src"
@(
  '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Scripted Demo Client","version":"1.0.0"}},"id":1}'
  '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
  '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"classify_symptoms","arguments":{"symptoms":["nausea","diarrhea"]}},"id":3}'
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
