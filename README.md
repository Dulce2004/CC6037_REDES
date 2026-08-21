# PharmaMCP

## Project Overview

PharmaMCP is an educational command-line project for **CC3067 Networks**. It demonstrates how a client and a local Model Context Protocol (MCP) server can exchange structured operations through a manually implemented JSON-RPC 2.0 layer.

The current delivery focuses on a local MCP server. It uses only the Python standard library and does not use FastMCP, an MCP SDK, or an external JSON-RPC library.

## Use Case

The project models a small pharmacy-oriented system that classifies controlled symptom identifiers into educational categories. The classification rules are explicit and deterministic; the system does not interpret natural language and does not recommend medication or treatment.

> **Medical disclaimer:** This project is for educational purposes only. Its output is not a medical diagnosis and does not replace evaluation or advice from a qualified healthcare professional.

## Architecture

The project separates protocol, server, client, and domain responsibilities:

```text
User
  -> Command-line interface
  -> Local MCP client
  -> JSON-RPC Request object
  -> Local MCP server
  -> classify_symptoms tool
  -> Pharmacy classification rules
  -> JSON-RPC Response or ErrorResponse
  -> User
```

The client passes validated JSON-RPC message objects directly to the server in memory. There is no network transport in this delivery.

## Features

- Manual JSON-RPC 2.0 request, response, error, serialization, and deserialization support.
- Local MCP server with `initialize`, `tools/list`, and `tools/call`.
- Tool registration and discovery.
- One registered tool: `classify_symptoms`.
- Deterministic respiratory, allergy, and gastrointestinal classification rules.
- Controlled handling of invalid requests, methods, parameters, and symptoms.
- Stateful local MCP client and interactive terminal interface.
- Automated unit and integration tests.

## Project Structure

```text
.
|-- docs/
|   |-- demo-guide.md
|   `-- Chatbot - Avance 1.pdf
|-- src/
|   `-- pharmacy_mcp/
|       |-- client/       # Local client and command-line interface
|       |-- jsonrpc/      # Manual JSON-RPC messages, errors, and conversion
|       |-- pharmacy/     # Controlled symptoms and classification rules
|       `-- server/       # Local MCP server and tool adapter
|-- tests/                # Unit and integration tests
|-- .gitignore
|-- README.md
`-- requirements.txt
```

## Requirements

- Python 3.12, which is the version used to verify this delivery.
- A terminal such as PowerShell or a Bash-compatible shell.

No external Python packages are required. `requirements.txt` intentionally contains no package dependencies.

## Installation

Clone or download the repository and open a terminal at its root. A virtual environment is optional because this delivery uses only the Python standard library.

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Bash-compatible shell:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Running

From the repository root, start the interactive client with:

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m pharmacy_mcp.client.cli
```

Bash-compatible shell:

```bash
PYTHONPATH=src python -m pharmacy_mcp.client.cli
```

The CLI provides four options: initialize the client, list server tools, classify symptoms, and exit. Initialize the client before listing or calling tools.

## Testing

Run the complete test suite from the repository root:

PowerShell:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

Bash-compatible shell:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The suite covers JSON-RPC messages, the local server, the pharmacy domain, tool integration, the client, and complete local client-server flows.

## MCP Operations

The local server implements this educational MCP subset:

- `initialize`: returns the declared protocol version, server information, and tool capabilities.
- `tools/list`: returns the public definitions of registered tools.
- `tools/call`: validates a tool name and arguments, then executes its handler.

The server declares protocol version `2025-11-25`, server name `Pharmacy MCP Server`, and server version `0.1.0`. See [the local server specification](docs/mcp-server-specification.md) for request, response, parameter, and error details.

## Pharmacy Tool

`classify_symptoms` requires a non-empty `symptoms` array containing controlled string identifiers. It trims surrounding whitespace, normalizes text to lowercase, removes duplicates, and rejects unsupported values.

Supported categories and symptoms are:

- `respiratory`: `fever`, `cough`, `sore_throat`.
- `allergy`: `sneezing`, `nasal_congestion`, `itchy_eyes`.
- `gastrointestinal`: `nausea`, `diarrhea`, `abdominal_pain`.

A category requires at least two distinct matching symptoms. If no category reaches that threshold, or if the best categories are tied, the result is `unclassified`.

## Error Handling

The manual JSON-RPC layer defines and supports these standard codes:

| Code | Name | Current use |
| ---: | --- | --- |
| `-32700` | Parse error | Malformed JSON passed to the deserializer. |
| `-32600` | Invalid Request | Invalid JSON-RPC structure or unsupported request type. |
| `-32601` | Method not found | Operation not registered by the server. |
| `-32602` | Invalid params | Invalid operation parameters, tool name, arguments, or symptoms. |
| `-32603` | Internal error | Unexpected handler or response-construction failure. |

The client displays server errors as `Error <code>: <message>` without exposing a traceback.

## Current Limitations

- Communication is local and occurs through in-memory Python objects.
- There are no network endpoints, URLs, ports, HTTP handlers, or sockets.
- The implementation is an educational MCP subset rather than a complete MCP lifecycle implementation.
- It does not provide full JSON Schema validation or MCP notification handling.
- It does not interpret natural-language symptoms or provide medical recommendations.

## Future Work

The following items are planned for later stages and are **not part of Delivery 1**:

- Integration with an LLM through an API.
- A remote MCP server.
- Wireshark communication analysis.

## Project Status

**Delivery 1 — Local MCP Server.** The manual JSON-RPC layer, local MCP server, `classify_symptoms` tool, local client, CLI, automated tests, server specification, and demonstration guide are implemented. The current transport is in memory, and no network endpoint exists.
