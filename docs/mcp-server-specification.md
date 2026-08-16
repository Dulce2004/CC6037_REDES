# Especificación del servidor MCP local

## 1. Información general

| Campo | Valor |
| --- | --- |
| Nombre | Pharmacy MCP Server |
| Versión del servidor | 0.1.0 |
| Versión MCP declarada | 2025-11-25 |
| Lenguaje | Python 3.12 |
| Caso de uso | Clasificación educativa de síntomas controlados |
| Transporte actual | Llamadas locales en memoria |

El servidor implementa manualmente un subconjunto educativo de MCP sobre estructuras JSON-RPC 2.0 propias. Su propósito es demostrar el registro, descubrimiento y ejecución de herramientas sin FastMCP ni SDKs MCP.

Actualmente el cliente entrega un objeto `Request` directamente a `PharmacyMCPServer.process_request()` y recibe un `Response` o `ErrorResponse`. No existen endpoints HTTP, sockets ni comunicación remota. Por tanto, este componente todavía no es el servidor remoto previsto para una entrega posterior.

## 2. Operación `initialize`

- **Método:** `initialize`
- **Parámetros:** objeto JSON. La implementación acepta `{}`. Si se incluye `protocolVersion`, debe ser un string.
- **Resultado:** versión declarada, capacidades e información del servidor.
- **Capacidad anunciada:** herramientas disponibles, sin notificaciones de cambio de lista (`listChanged: false`).

Request:

```json
{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "result": {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {"listChanged": false}},
    "serverInfo": {"name": "Pharmacy MCP Server", "version": "0.1.0"}
  },
  "id": 1
}
```

## 3. Operación `tools/list`

- **Método:** `tools/list`
- **Parámetros:** objeto JSON; normalmente `{}`.
- **Resultado:** objeto con el arreglo `tools`.
- **Herramienta disponible:** `classify_symptoms`.

Request:

```json
{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}
```

Response abreviado:

```json
{
  "jsonrpc": "2.0",
  "result": {
    "tools": [{
      "name": "classify_symptoms",
      "description": "Classifies controlled symptom identifiers into educational categories.",
      "inputSchema": {
        "type": "object",
        "properties": {"symptoms": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
        "required": ["symptoms"],
        "additionalProperties": false
      }
    }]
  },
  "id": 2
}
```

La respuesta real también incluye, dentro de `items.enum`, los nueve identificadores admitidos que se enumeran en la sección de la herramienta.

## 4. Operación `tools/call`

- **Método:** `tools/call`.
- **Parámetros:** objeto con `name` y `arguments`.
- **`name`:** string no vacío que identifica una herramienta registrada.
- **`arguments`:** objeto enviado al handler de la herramienta.
- **Resultado exitoso:** objeto con una lista `content` de bloques de texto.
- **Resultado fallido:** `ErrorResponse` JSON-RPC con código y mensaje.

Request válido:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "classify_symptoms",
    "arguments": {"symptoms": ["fever", "cough", "sore_throat"]}
  },
  "id": 3
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [{
      "type": "text",
      "text": "Classification: respiratory. Matched symptoms: fever, cough, sore_throat. Symptoms match the respiratory category. Educational use only; not a medical diagnosis."
    }]
  },
  "id": 3
}
```

## 5. Herramienta `classify_symptoms`

| Campo | Definición |
| --- | --- |
| Nombre | `classify_symptoms` |
| Entrada | `symptoms` |
| Tipo | Array no vacío de strings |
| Salida | Bloque de texto con clasificación y coincidencias |

Síntomas permitidos:

- `fever`
- `cough`
- `sore_throat`
- `sneezing`
- `nasal_congestion`
- `itchy_eyes`
- `nausea`
- `diarrhea`
- `abdominal_pain`

El handler MCP valida los argumentos, llama a la lógica de `pharmacy/` y transforma el resultado en contenido de texto. No contiene las reglas de clasificación.

## 6. Categorías y reglas

La entrada se normaliza eliminando espacios exteriores y convirtiendo los strings a minúsculas. Los síntomas duplicados cuentan una sola vez.

Cada categoría requiere al menos dos síntomas distintos de su grupo:

- **`respiratory`:** `fever`, `cough`, `sore_throat`.
- **`allergy`:** `sneezing`, `nasal_congestion`, `itchy_eyes`.
- **`gastrointestinal`:** `nausea`, `diarrhea`, `abdominal_pain`.
- **`unclassified`:** ninguna categoría alcanza dos coincidencias, o varias categorías empatan con el mayor número de coincidencias.

Estas reglas son deterministas, limitadas y educativas. No constituyen un diagnóstico médico.

## 7. Validaciones

La implementación rechaza:

- ausencia de `symptoms` en `arguments`;
- `symptoms` que no sea un array, incluido `null`;
- lista vacía;
- elementos que no sean strings;
- strings vacíos después de eliminar espacios;
- identificadores con formato diferente de palabras minúsculas separadas por guion bajo;
- síntomas que no estén en el catálogo;
- `params` que no sea un objeto;
- ausencia o tipo incorrecto de `name`;
- `arguments` que no sea un objeto.

## 8. Errores JSON-RPC

| Código | Mensaje estándar | Uso actual |
| --- | --- | --- |
| `-32700` | Parse error | Texto JSON malformado al usar el deserializador manual. |
| `-32600` | Invalid Request | Mensaje JSON-RPC inválido o tipo de solicitud incorrecto. |
| `-32601` | Method not found | El método solicitado no existe en el dispatch del servidor. |
| `-32602` | Invalid params | Parámetros inválidos, herramienta inexistente o síntomas inválidos/desconocidos. |
| `-32603` | Internal error | Excepción inesperada del handler o resultado no representable como JSON. |

El cliente convierte un `ErrorResponse` en un `ClientError` controlado y muestra `Error <código>: <mensaje>` sin traceback.

## 9. Ejemplos completos

### 9.1 Solicitud y clasificación válidas

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "classify_symptoms",
    "arguments": {"symptoms": ["nausea", "diarrhea", "abdominal_pain"]}
  },
  "id": 4
}
```

```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [{
      "type": "text",
      "text": "Classification: gastrointestinal. Matched symptoms: nausea, diarrhea, abdominal_pain. Symptoms match the gastrointestinal category. Educational use only; not a medical diagnosis."
    }]
  },
  "id": 4
}
```

### 9.2 Caso `unclassified`

Entrada de la herramienta:

```json
{"symptoms":["fever","itchy_eyes"]}
```

Contenido devuelto:

```text
Classification: unclassified. Matched symptoms: none. No supported category matches the provided symptoms. Educational use only; not a medical diagnosis.
```

### 9.3 Caso inválido y respuesta de error

Request:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "classify_symptoms",
    "arguments": {"symptoms": ["fever", "magic_symptom"]}
  },
  "id": 6
}
```

ErrorResponse:

```json
{
  "jsonrpc": "2.0",
  "error": {"code": -32602, "message": "Unknown symptom: 'magic_symptom'."},
  "id": 6
}
```
