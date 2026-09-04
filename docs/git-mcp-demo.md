# Safe Git MCP Demonstration

## Scope and safety boundary

This demonstration uses the official MIT-licensed
[`mcp-server-git`](https://github.com/modelcontextprotocol/servers/tree/main/src/git)
at the pinned PyPI version
[`2026.8.18`](https://pypi.org/project/mcp-server-git/2026.8.18/).
It never points the Git server at this project. It creates a dedicated local
repository under ignored `runtime/`, uses repository-local identity, performs no
remote operation, and asks for human confirmation before the demonstration
commit. Filesystem MCP is intentionally pending, so the README is edited outside
the Git server.

The host requests and observed the MCP revision `2025-11-25`. The external
server returned the same revision, so the normal lifecycle described by the
[MCP specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)
was used without a compatibility fallback.

The controlled verification observed `serverInfo.name` as `mcp-git` and
`serverInfo.version` as `1.29.1`, plus `experimental: {}` and
`tools.listChanged: false` capabilities. That server-reported implementation
version is distinct from the pinned PyPI distribution version `2026.8.18`; the
host preserves it instead of rewriting or inferring metadata.

## Prerequisites and cache preparation

Use Python 3.10 or newer, Git, and `uvx`. The course project targets Python 3.12.
Confirm them without modifying the project environment:

```powershell
python --version
git --version
uvx --version
uvx --from mcp-server-git==2026.8.18 mcp-server-git --help
```

The final command may access the network on its first run and populates uv's
user cache. Later runs can be forced to use the existing cache:

```powershell
$env:UV_OFFLINE = "1"
uvx --from mcp-server-git==2026.8.18 mcp-server-git --help
Remove-Item Env:UV_OFFLINE
```

Do not use `pip install`, do not add the external package to
`requirements.txt`, and do not configure a global Git identity.

## Create the dedicated repository

Run these commands from the project root. The guard refuses to reuse or
overwrite an existing demo directory.

```powershell
$projectRoot = (Get-Location).Path
$runtimeRoot = Join-Path $projectRoot "runtime"
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$demoBase = Join-Path $runtimeRoot "git-mcp-manual-demo"
$demoRepo = Join-Path $demoBase "repository"
$logPath = Join-Path $demoBase "mcp-host.jsonl"
$databasePath = Join-Path $demoBase "pharmacy.sqlite3"

if (Test-Path -LiteralPath $demoBase) {
    throw "Demo path already exists: $demoBase"
}

New-Item -ItemType Directory -Path $demoRepo | Out-Null
git -C $demoRepo init
git -C $demoRepo config --local user.name "Academic Demo"
git -C $demoRepo config --local user.email "student@example.invalid"

Set-Content -LiteralPath (Join-Path $demoRepo "README.md") -Encoding utf8 -Value @(
    "# Git MCP demonstration"
    ""
    "This repository is disposable and local only."
)
git -C $demoRepo add README.md
git -C $demoRepo commit -m "chore: initialize demo repository"

$env:PYTHONPATH = "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PHARMACY_MCP_DATABASE_PATH = $databasePath
$env:MCP_GIT_REPOSITORY_PATH = (Resolve-Path $demoRepo).Path
$repoArguments = @{repo_path = $env:MCP_GIT_REPOSITORY_PATH} | ConvertTo-Json -Compress
```

The setup commit exists only to make the subsequent unstaged diff visible. Both
identity values were written with `--local` inside the disposable repository.

## Discover both servers and inspect Git

`list-tools` starts Pharmacy and Git together, completes both handshakes,
discovers each tool list, writes the JSONL exchanges, and then closes both child
processes by closing stdin:

```powershell
python -m pharmacy_mcp.host.cli --log-file $logPath list-servers
python -m pharmacy_mcp.host.cli --log-file $logPath list-tools
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_status --arguments $repoArguments
```

The global registry should contain seven `pharmacy__...` tools and the Git tools
actually returned by `tools/list`. At minimum, verify
`git__git_status`, `git__git_diff_unstaged`, `git__git_diff_staged`,
`git__git_add`, `git__git_commit`, and `git__git_log`.

Modify the tracked README outside Git MCP, then inspect it through the read-only
tool:

```powershell
Add-Content -LiteralPath (Join-Path $demoRepo "README.md") -Encoding utf8 -Value @(
    ""
    "Filesystem MCP will be integrated in the next phase."
)
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_diff_unstaged --arguments $repoArguments
```

## Verify mutation blocking, then authorize deliberately

First call `git_add` without authorization. The command must fail locally. Its
JSONL entry has `direction: "local"` and
`message_type: "mutation_rejected"`; there is no outbound `tools/call` for this
attempt.

```powershell
$addArguments = @{
    repo_path = $env:MCP_GIT_REPOSITORY_PATH
    files = @("README.md")
} | ConvertTo-Json -Compress

python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_add --arguments $addArguments
```

Review the absolute repository path and file list. Then explicitly authorize
the one staging call and inspect the staged diff:

```powershell
python -m pharmacy_mcp.host.cli --log-file $logPath --allow-mutation call-tool git__git_add --arguments $addArguments
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_diff_staged --arguments $repoArguments
```

Pause for human confirmation before committing:

```powershell
$confirmation = Read-Host "Type COMMIT to create 'docs: add demo readme' in $demoRepo"
if ($confirmation -ne "COMMIT") {
    throw "Commit cancelled by user"
}

$commitArguments = @{
    repo_path = $env:MCP_GIT_REPOSITORY_PATH
    message = "docs: add demo readme"
} | ConvertTo-Json -Compress

python -m pharmacy_mcp.host.cli --log-file $logPath --allow-mutation call-tool git__git_commit --arguments $commitArguments
$logArguments = @{
    repo_path = $env:MCP_GIT_REPOSITORY_PATH
    max_count = 5
} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_log --arguments $logArguments
git -C $demoRepo rev-parse HEAD
git -C $demoRepo log -1 --oneline
git -C $demoRepo config --local --get user.name
git -C $demoRepo config --local --get user.email
```

The authorized calls add `mutation_authorized` local events before their normal
outbound requests. The canonical `repo_path` must exactly match
`MCP_GIT_REPOSITORY_PATH`; `..`, siblings, missing locations, and link escapes
are rejected even with `--allow-mutation`.

## Inspect the protocol log and finish

```powershell
$entries = Get-Content -LiteralPath $logPath | ForEach-Object { $_ | ConvertFrom-Json }
$entries | Group-Object server, direction, message_type | Select-Object Count, Name
$entries | Where-Object { $_.server -eq "git" -and $_.method -eq "tools/call" }
$entries | Where-Object { $_.direction -eq "local" }
```

The log should contain separate `server: "pharmacy"` and `server: "git"`
entries for initialization, the initialized notification, tool discovery, tool
calls, responses, and local policy decisions. It never records the complete
process environment. Each CLI command closes the child processes it starts.

There is deliberately no automatic cleanup command in this guide. Inspect
`$demoBase` and remove that exact dedicated directory manually only when you no
longer need it. Never substitute the project root, a user profile, or another
broad directory into a recursive deletion command.

## Troubleshooting

- Missing `MCP_GIT_REPOSITORY_PATH`: set it to an existing absolute Git
  repository before loading the default configuration.
- `uvx` cannot resolve the package: populate the cache once with network access,
  verify version `2026.8.18`, and retry; disable offline mode while populating.
- Repository rejected: pass the exact configured canonical root in `repo_path`,
  not a child, sibling, relative path, or path containing `..`.
- Mutation rejected: review the operation and put `--allow-mutation` before the
  subcommand. The flag does not relax the repository boundary.
- Git commit identity error: set `user.name` and `user.email` with `--local` in
  the demo repository; do not change global configuration.
- No unstaged text for an untracked file: Git's unstaged diff does not include
  untracked content. Establish a local setup commit or stage the file and inspect
  `git_diff_staged`.
