"""Paquete principal del proyecto educativo PharmaMCP.

Agrupa las capas manuales JSON-RPC, MCP, dominio de farmacia, clientes y host sin
ocultar dependencias tras un SDK. Los subpaquetes mantienen contratos separados:
el dominio no conoce transportes y los transportes delegan la lógica al servidor.
Importar el paquete no inicia procesos, red, archivos ni bases de datos."""
