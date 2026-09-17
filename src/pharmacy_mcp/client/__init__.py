"""Cliente MCP local y representación controlada de errores.

Expone la API en memoria utilizada por demostraciones y pruebas sin pasar por un
transporte. El cliente conserva el lifecycle y la correlación de IDs, mientras el
servidor sigue siendo dueño del estado MCP. Importar este paquete no crea sesiones
ni ejecuta herramientas."""

from .client import ClientError, PharmacyMCPClient

__all__ = ["ClientError", "PharmacyMCPClient"]
