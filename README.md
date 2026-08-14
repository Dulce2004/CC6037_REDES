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
- comunicación y manejo de mensajes JSON-RPC;
- validación de solicitudes, respuestas y errores del protocolo;
- lógica de clasificación del caso de uso de farmacia;
- pruebas unitarias y de integración.

No se utilizarán FastMCP ni SDKs o bibliotecas que implementen MCP. La intención académica es construir y comprender el protocolo directamente.

## Tecnologías iniciales

- Python 3;
- biblioteca estándar de Python;
- JSON como formato de intercambio futuro;
- terminal o línea de comandos;
- Git para control de versiones.

En esta etapa no se requieren dependencias externas.

## Estructura del proyecto

```text
.
├── src/
│   └── pharmacy_mcp/
│       ├── client/       # Futuro cliente de terminal
│       ├── jsonrpc/      # Futuro manejo manual de JSON-RPC
│       ├── pharmacy/     # Futura lógica del caso de uso
│       └── server/       # Futuro servidor MCP manual
├── tests/                # Futuras pruebas automatizadas
├── .gitignore
├── README.md
└── requirements.txt
```

## Comunicación MCP y JSON-RPC

La comunicación entre cliente y servidor se desarrollará posteriormente mediante JSON-RPC. El servidor MCP será una implementación manual: no se delegará el protocolo a FastMCP, a un SDK de MCP ni a otra biblioteca equivalente.

## Project Status

**Etapa inicial de desarrollo.** El repositorio contiene únicamente la estructura, separación de responsabilidades y documentación base. Todavía no incluye servidor, cliente, comunicación JSON-RPC, conexión con un LLM ni clasificación de síntomas.

## Preparación del entorno

Cuando se agreguen dependencias en etapas futuras, se podrá preparar un entorno local con:

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

Actualmente `requirements.txt` no declara paquetes externos.
