"""Interfaz interactiva de terminal para el cliente MCP local."""

from __future__ import annotations

from pharmacy_mcp.client.client import ClientError, PharmacyMCPClient
from pharmacy_mcp.jsonrpc.messages import JsonValue
from pharmacy_mcp.server import PharmacyMCPServer

MENU = """
========================================
Pharmacy MCP Client
========================================
1. Initialize
2. List tools
3. Assess symptoms
4. Exit
"""


def main() -> None:
    """Ejecuta el menú local hasta que el usuario decide salir."""

    client = PharmacyMCPClient(PharmacyMCPServer())
    while True:
        print(MENU)
        try:
            option = input("Select an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return

        if option == "1":
            _initialize(client)
        elif option == "2":
            _list_tools(client)
        elif option == "3":
            _assess_symptoms(client)
        elif option == "4":
            print("Goodbye.")
            return
        else:
            print("Invalid option. Select 1, 2, 3, or 4.")


def _initialize(client: PharmacyMCPClient) -> None:
    response = client.initialize()
    if isinstance(response, ClientError):
        print(response)
        return

    server_info = client.server_info or {}
    print(
        "Initialized: "
        f"{server_info.get('name', 'Unknown server')} "
        f"{server_info.get('version', '')} "
        f"(protocol {client.protocol_version})."
    )


def _list_tools(client: PharmacyMCPClient) -> None:
    tools = client.list_tools()
    if isinstance(tools, ClientError):
        print(tools)
        return
    if not tools:
        print("No tools available.")
        return

    print("Available tools:")
    for tool in tools:
        print(f"- {tool.get('name')}: {tool.get('description', '')}")


def _assess_symptoms(client: PharmacyMCPClient) -> None:
    try:
        symptoms = input("Describe the symptoms: ").strip()
        age_text = input("Age in years (optional): ").strip()
        duration_text = input("Duration in days (optional): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAssessment cancelled.")
        return

    arguments: dict[str, JsonValue] = {"symptoms": symptoms}
    try:
        if age_text:
            arguments["age"] = int(age_text)
        if duration_text:
            arguments["duration_days"] = int(duration_text)
    except ValueError:
        print("Age and duration must be whole numbers when supplied.")
        return

    result = client.call_tool("assess_symptoms", arguments)
    if isinstance(result, ClientError):
        print(result)
        return
    _print_tool_result(result)


def _print_tool_result(result: JsonValue) -> None:
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        print("The tool returned an unsupported result.")
        return

    for content in result["content"]:
        if isinstance(content, dict) and content.get("type") == "text":
            text = content.get("text")
            if isinstance(text, str):
                print(text)


if __name__ == "__main__":
    main()
