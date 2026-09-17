# Versioned official slim runtime: the server uses only Python's standard library.
FROM python:3.12-slim

# Cloud Run supplies PORT at runtime. SQLite deliberately lives under /tmp because
# container storage is ephemeral; this runtime does not coordinate multiple instances.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8080 \
    PHARMACY_MCP_DATABASE_PATH=/tmp/pharmacy/pharmacy.sqlite3

WORKDIR /app

# A fixed unprivileged identity owns only the writable runtime directory. It has no
# login shell or home directory and does not receive source-tree write permission.
RUN groupadd --system --gid 10001 pharmacy \
    && useradd --uid 10001 --gid pharmacy --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin pharmacy \
    && install -d -o pharmacy -g pharmacy /tmp/pharmacy

# Copy only the protocol, domain and HTTP-server runtime. Host/provider clients,
# tests, documentation, local state and build-time secrets are excluded explicitly.
COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/__init__.py /app/src/pharmacy_mcp/__init__.py
COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/jsonrpc /app/src/pharmacy_mcp/jsonrpc
COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/pharmacy /app/src/pharmacy_mcp/pharmacy
COPY --chown=pharmacy:pharmacy src/pharmacy_mcp/server /app/src/pharmacy_mcp/server

USER 10001:10001

# EXPOSE documents the default; the server still honors the runtime PORT variable.
EXPOSE 8080

# Exec-form entrypoint preserves signal delivery and avoids shell interpolation.
CMD ["python", "-B", "-m", "pharmacy_mcp.server.http"]
