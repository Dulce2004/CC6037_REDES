FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8080 \
    PHARMACY_MCP_DATABASE_PATH=/tmp/pharmacy/pharmacy.sqlite3

WORKDIR /app

RUN groupadd --system --gid 10001 pharmacy \
    && useradd --uid 10001 --gid pharmacy --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin pharmacy \
    && install -d -o pharmacy -g pharmacy /tmp/pharmacy

COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/__init__.py /app/src/pharmacy_mcp/__init__.py
COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/jsonrpc /app/src/pharmacy_mcp/jsonrpc
COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/pharmacy /app/src/pharmacy_mcp/pharmacy
COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/server /app/src/pharmacy_mcp/server

USER 10001:10001

EXPOSE 8080

CMD ["python", "-B", "-m", "pharmacy_mcp.server.http"]
