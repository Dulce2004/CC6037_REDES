# Guía rápida de demostración — Entrega 1

## Paso 1 — Iniciar la CLI

Desde PowerShell, en la raíz del proyecto:

```powershell
$env:PYTHONPATH = "src"
python -m pharmacy_mcp.client.cli
```

## Paso 2 — Inicializar el cliente

Seleccionar `1. Initialize`. Deben aparecer el nombre y la versión del servidor, junto con la versión de protocolo.

## Paso 3 — Listar las herramientas

Seleccionar `2. List tools` y confirmar que aparece `classify_symptoms`. La lista proviene del servidor mediante `tools/list`; no está escrita directamente en la CLI.

## Paso 4 — Probar `respiratory`

Seleccionar `3. Classify symptoms` e introducir:

```text
fever, cough, sore_throat
```

Resultado esperado: `Classification: respiratory`.

## Paso 5 — Probar `allergy`

Seleccionar `3` e introducir:

```text
sneezing, nasal_congestion, itchy_eyes
```

Resultado esperado: `Classification: allergy`.

## Paso 6 — Probar `gastrointestinal`

Seleccionar `3` e introducir:

```text
nausea, diarrhea, abdominal_pain
```

Resultado esperado: `Classification: gastrointestinal`.

## Paso 7 — Probar `unclassified`

Seleccionar `3` e introducir:

```text
fever, itchy_eyes
```

Resultado esperado: `Classification: unclassified`. El sistema no inventa una categoría.

## Paso 8 — Probar una entrada incorrecta

Seleccionar `3` e introducir:

```text
fever, magic_symptom
```

Resultado esperado:

```text
Error -32602: Unknown symptom: 'magic_symptom'.
```

## Paso 9 — Explicar el error

La CLI no corrige ni descarta `magic_symptom`. El cliente lo envía en un `Request` JSON-RPC, el servidor ejecuta `tools/call`, la herramienta consulta el dominio de farmacia y el servidor devuelve un `ErrorResponse` con código `-32602`.

```text
Usuario → CLI → Cliente → JSON-RPC → Servidor MCP
        → tools/call → classify_symptoms → Dominio de farmacia
        → Response/ErrorResponse → Cliente → Usuario
```

## Paso 10 — Salir

Seleccionar `4. Exit` y confirmar el mensaje `Goodbye.`.

