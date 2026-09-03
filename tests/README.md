# Pruebas

Este directorio contiene las pruebas unitarias de los componentes implementados.

Desde la raíz del proyecto se ejecutan con:

```bash
python -m unittest discover -s tests -v
```

Las pruebas cubren por separado la capa JSON-RPC, el núcleo local del servidor MCP, la evaluación determinista de síntomas, las reglas simuladas de interacciones y alergias, el catálogo, el inventario, el cliente local, el transporte stdio y los flujos completos cliente-servidor. `classify_symptoms` se prueba únicamente como motor interno; la tool pública es `assess_symptoms`. No prueban transporte de red porque todavía no existe.
