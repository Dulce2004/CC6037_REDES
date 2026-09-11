# Pharmacy HTTP Container and Cloud Run Preparation

## Scope

This guide prepares the existing manual Pharmacy MCP Streamable HTTP server for
a local Linux container and a later academic Google Cloud Run demonstration. It
does not deploy a service, enable a Google Cloud API, create a registry, create a
secret, or make a billable request. The image contains only the Pharmacy HTTP
runtime and Python's standard library. It does not contain the terminal host,
Gemini, Anthropic, Git MCP, Filesystem MCP, Node.js, npm, npx, or uvx.

The project targets Python 3.12. Its runtime code has no third-party Python
dependencies, so the image uses the official `python:3.12-slim` base directly
and does not run a package installer.

## Image contents

The Dockerfile copies only these paths:

- `src/pharmacy_mcp/__init__.py`;
- `src/pharmacy_mcp/jsonrpc`;
- `src/pharmacy_mcp/pharmacy`, including its four required JSON data files;
- `src/pharmacy_mcp/server`, including the HTTP entry point.

The process runs as the dedicated numeric user and group `10001:10001`. Its
working directory is `/app`, `PYTHONPATH` is `/app/src`, and SQLite is written to
`/tmp/pharmacy/pharmacy.sqlite3`. No credentials are present in an image layer.

The Dockerfile intentionally has no `HEALTHCHECK` directive. It would add no
value for Cloud Run, which can call the existing HTTP endpoint as a platform
startup or liveness probe. This also avoids installing `curl`; `/health` remains
available using only the server's standard-library implementation.

## Build locally

Run from the repository root:

```powershell
docker build -t pharmacy-mcp-http:local .
```

The reduced build context excludes Git metadata, documentation, tests, local
runtime state, databases, logs, virtual environments, editor files, JavaScript
artifacts, `.env` files, and common credential formats. The Pharmacy JSON data
under `src/pharmacy_mcp/pharmacy/data` is deliberately retained.

## Run locally with a generated test token

The following PowerShell creates a random token in memory without printing it,
publishes the container only on loopback, and gives SQLite a disposable tmpfs.
The token is inherited from the current process environment instead of being
written literally in the `docker run` arguments.

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token = [Convert]::ToBase64String($bytes)
$env:PHARMACY_MCP_HTTP_TOKEN = $token

docker run --detach --name pharmacy-mcp-http-local `
  --publish 127.0.0.1:8080:8080 `
  --env HOST=0.0.0.0 `
  --env PORT=8080 `
  --env PHARMACY_MCP_HTTP_TOKEN `
  --env PHARMACY_MCP_DATABASE_PATH=/tmp/pharmacy/pharmacy.sqlite3 `
  --tmpfs /tmp/pharmacy:rw,noexec,nosuid,size=64m `
  pharmacy-mcp-http:local
```

Do not use `--network host` and do not mount the course repository. Verify only
safe metadata:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8080/health"
docker inspect --format '{{.Config.User}}' pharmacy-mcp-http-local
```

The health response is exactly `{"status":"ok"}`. It does not create a session
and contains no database path, token, session identifier, patient information,
or external-service state. A POST to `/mcp` without the Bearer token must return
HTTP `401`. Follow the initialize, `notifications/initialized`, `tools/list`,
`check_stock`, and DELETE exchange in the
[Streamable HTTP guide](streamable-http-guide.md), using
`$env:PHARMACY_MCP_HTTP_TOKEN` without displaying it. `tools/list` must return
the seven existing Pharmacy tools.

Stop and remove only the resources created by this example:

```powershell
docker rm --force pharmacy-mcp-http-local
docker image rm pharmacy-mcp-http:local
Remove-Item Env:PHARMACY_MCP_HTTP_TOKEN
$token = $null
```

The SQLite file disappears with the tmpfs/container. This is intentional for
the demonstration and must not be described as durable storage.

## Runtime variables

| Variable | Container value or source | Purpose |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | Required so Cloud Run can reach the ingress process. |
| `PORT` | `8080`, overridable at runtime | Port read by the existing server; Cloud Run supplies `PORT`. |
| `PHARMACY_MCP_HTTP_TOKEN` | Runtime secret, required | Application-level Bearer authentication. Never place it in the image. |
| `PHARMACY_MCP_DATABASE_PATH` | `/tmp/pharmacy/pharmacy.sqlite3` | Ephemeral SQLite state. |
| `PHARMACY_MCP_ALLOWED_ORIGINS` | Explicit comma-separated origins when needed | Exact Origin allowlist; wildcards are not accepted. |
| `PHARMACY_MCP_HTTP_MAX_REQUEST_BYTES` | Existing default `1000000` | Maximum POST body. |
| `PHARMACY_MCP_HTTP_REQUEST_TIMEOUT_SECONDS` | Existing default `30` | Per-connection request timeout. |
| `PHARMACY_MCP_HTTP_MAX_SESSIONS` | Existing default `100` | In-memory session bound. |
| `PHARMACY_MCP_HTTP_SESSION_TTL_SECONDS` | Existing default `1800` | Inactivity expiry in seconds. |

The image does not set `PHARMACY_MCP_HTTP_TOKEN`. Consequently, launching its
public `0.0.0.0` bind without injecting a token is rejected by the server before
it starts listening. Do not enable
`PHARMACY_MCP_HTTP_ALLOW_INSECURE_NO_AUTH` in a container or cloud environment.

## Container security boundary

The build context does not include `.git`, `.env` files, private-key formats,
the local runtime directory, or the host and LLM clients. The process is not
root, and the only intended writable state is the disposable database directory
under `/tmp`. Supply the Bearer token only at runtime.

The existing HTTP diagnostic messages do not include `Authorization`, complete
session IDs, request bodies, or medical inputs. Keep application debug logging
disabled and do not copy arbitrary server requests into platform logs. The
`/health` response is intentionally constant and contains no sensitive state.

## Future Cloud Run demonstration profile

No command in this section was executed. Use placeholders when preparing the
later deployment, review the current Google Cloud documentation, and configure
these values deliberately:

| Setting | Academic demonstration recommendation |
| --- | --- |
| Billing | Request-based billing |
| Minimum instances | `0` |
| Maximum instances | `1` |
| Concurrency | Limited to `1` request per instance for the demonstration |
| CPU | `1` vCPU |
| Memory | `512 MiB` |
| Request timeout | A limited value such as `60` seconds |
| Container port | `8080` |
| Transport | HTTPS service URL ending in `/mcp` |
| Health probe | HTTP `/health` |
| MCP authentication | Inject `PHARMACY_MCP_HTTP_TOKEN` from Secret Manager at runtime |
| Origin | Exact allowlist if a browser-facing origin is later introduced |

Cloud Run requires the ingress container to listen on `0.0.0.0` and on the
injected `PORT`. Cloud Run terminates TLS before forwarding traffic to the
container, so clients use the service's HTTPS URL. This server uses the
`Authorization` header for its own Bearer token. Combining that application
token with Cloud Run IAM authentication would require a separately designed
authentication boundary and is outside this increment.

The service must have a billing account even when usage might fit within a free
tier. Charges can still occur. Configure a billing budget and alert thresholds
before deployment; budgets report or alert on spend and are not a guaranteed
hard spending cap. Request-based billing, zero minimum instances, and one
maximum instance reduce exposure but do not guarantee zero cost.

## State limitations

Both MCP sessions and lifecycle state are held in one process. SQLite resides
on Cloud Run's writable in-memory filesystem. Data written there does not
survive instance shutdown, replacement, a new revision, or migration to another
instance. Orders and inventory changes can therefore disappear.

Maximum instances `1` is necessary for this academic design, but it is not a
durability guarantee: the only instance can still restart, and Cloud Run may
briefly exceed a configured maximum during exceptional scaling behavior. This
architecture is acceptable only for a controlled course demonstration and is
not suitable for production.

A production design would externalize session/state concerns and use Cloud SQL
or another managed transactional store. That work, stronger identity controls,
deployment automation, and a browser interface are explicitly out of scope.

After the evaluation, delete the Cloud Run service and every container image or
registry artifact created for the demonstration, then confirm that billing no
longer reports active resources.

## Official references

- [Cloud Run container runtime contract](https://cloud.google.com/run/docs/container-contract)
- [Cloud Run health checks](https://cloud.google.com/run/docs/configuring/healthchecks)
- [Cloud Run billing settings](https://cloud.google.com/run/docs/configuring/billing-settings)
- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Cloud Billing budgets and alerts](https://cloud.google.com/billing/docs/how-to/budgets)
