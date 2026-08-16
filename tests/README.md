# Pruebas

Este directorio contiene las pruebas unitarias de los componentes implementados.

Desde la raíz del proyecto se ejecutan con:

```bash
python -m unittest discover -s tests -v
```

Las pruebas cubren por separado la capa JSON-RPC, el núcleo local del servidor MCP, la lógica determinista de farmacia, la integración de `classify_symptoms`, el cliente local y los flujos completos cliente-servidor. No prueban transporte de red porque todavía no existe.
