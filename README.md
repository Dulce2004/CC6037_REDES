# PharmaMCP — Proyecto 1 de Redes

Base inicial para un proyecto educativo de redes que implementará manualmente un protocolo basado en MCP mediante mensajes JSON-RPC. El caso de uso será un sistema de farmacia capaz de clasificar casos a partir de síntomas.

## Curso

**CC3067 Redes**

## Objetivo general

Diseñar e implementar de forma incremental una aplicación de línea de comandos que permita estudiar la comunicación entre un cliente y un servidor, siguiendo un protocolo basado en MCP construido manualmente sobre JSON-RPC.

## Caso de uso

El dominio del proyecto será un sistema de farmacia basado en síntomas. En etapas posteriores, la lógica de dominio recibirá información de un caso y permitirá clasificarlo de acuerdo con las reglas definidas para la tarea.

> **Aviso:** este sistema tendrá fines exclusivamente educativos. No sustituye el diagnóstico, la evaluación ni la orientación de un profesional de la salud.

## Alcance futuro

En iteraciones posteriores se incorporarán, de acuerdo con la especificación del proyecto:

- un servidor MCP implementado manualmente;
- un cliente MCP para terminal;
- uso de la capa manual JSON-RPC para comunicar los componentes;
- lógica de clasificación del caso de uso de farmacia;
- pruebas unitarias y de integración.

No se utilizarán FastMCP ni SDKs o bibliotecas que implementen MCP. La intención académica es construir y comprender el protocolo directamente.

## Tecnologías iniciales

- Python 3;
- biblioteca estándar de Python;
- JSON como formato de intercambio de la capa JSON-RPC;
- terminal o línea de comandos;
- Git para control de versiones.

En esta etapa no se requieren dependencias externas.

## Estructura del proyecto

```text
.
├── src/
│   └── pharmacy_mcp/
│       ├── client/       # Futuro cliente de terminal
│       ├── jsonrpc/      # Mensajes y manejo manual de JSON-RPC
│       ├── pharmacy/     # Futura lógica del caso de uso
│       └── server/       # Futuro servidor MCP manual
├── tests/                # Futuras pruebas automatizadas
├── .gitignore
├── README.md
└── requirements.txt
```

## JSON-RPC Layer

JSON-RPC 2.0 será el mecanismo de intercambio de mensajes que utilizará posteriormente el protocolo entre cliente y servidor. Esta capa fue implementada manualmente con el módulo `json` de la biblioteca estándar; no utiliza FastMCP, SDKs de MCP ni bibliotecas externas de JSON-RPC.

Actualmente soporta:

- solicitudes (`Request`), incluyendo solicitudes sin `id` para representar notificaciones;
- respuestas exitosas (`Response`);
- respuestas de error (`ErrorResponse`) y su contenido (`ErrorObject`);
- validación básica de versión, método, parámetros, identificadores y respuestas;
- serialización de objetos a JSON y deserialización de JSON a objetos;
- excepciones asociadas con los códigos estándar `-32700`, `-32600`, `-32601`, `-32602` y `-32603`.

Las pruebas se ejecutan desde la raíz del repositorio:

```bash
python -m unittest discover -s tests -v
```

Esta capa todavía no contiene transporte, sockets, HTTP, despacho de métodos ni comportamiento específico de MCP.

## Project Status

**Segunda etapa de desarrollo.** El repositorio ya contiene una capa manual y probada de mensajes JSON-RPC 2.0. Todavía no incluye servidor MCP, cliente MCP, transporte, conexión con un LLM ni clasificación de síntomas.

## Preparación del entorno

Cuando se agreguen dependencias en etapas futuras, se podrá preparar un entorno local con:

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

Actualmente `requirements.txt` no declara paquetes externos.
