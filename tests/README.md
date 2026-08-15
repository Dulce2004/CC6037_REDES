# Pruebas

Este directorio contiene las pruebas unitarias de los componentes implementados.

Desde la raíz del proyecto se ejecutan con:

```bash
python -m unittest discover -s tests -v
```

Las pruebas cubren la capa JSON-RPC y el núcleo local del servidor MCP: despacho de métodos, registro, listado y ejecución de herramientas ficticias. No prueban transporte, cliente ni lógica de farmacia porque esos componentes aún no existen.
