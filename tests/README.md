# Pruebas

Este directorio contiene las pruebas unitarias de los componentes implementados.

Desde la raíz del proyecto se ejecutan con:

```bash
python -m unittest discover -s tests -v
```

En esta etapa las pruebas cubren únicamente mensajes, validación, serialización y deserialización JSON-RPC. No prueban servidor, cliente, transporte, MCP ni lógica de farmacia porque esos componentes aún no existen.
