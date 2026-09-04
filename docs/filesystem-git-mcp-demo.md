# Combined Filesystem and Git MCP Demonstration

## Scope and safety boundary

This demonstration connects the manual host to three local stdio processes:
Pharmacy, the official `mcp-server-git==2026.8.18`, and the official
`@modelcontextprotocol/server-filesystem@2026.8.31`. Filesystem and Git receive
the same newly created disposable directory. That directory is both the sole
Filesystem root and the sole Git repository; the course repository is never
passed to either external server.

The workflow lists the allowed directory, rejects an unauthorized write,
creates and reads a README through Filesystem after explicit authorization,
then inspects, stages, commits, and reads history through Git. No remote is
configured and no network Git operation is performed. The demonstration commit
is `docs: add filesystem MCP demo` and uses repository-local `.invalid` identity.

## Prerequisites and package cache

Use Python 3.12, Git, `uvx`, Node.js, npm, and npx. Inspect them without adding
dependencies to this project:

```powershell
python --version
git --version
uvx --version
node --version
npm --version
npx --version
npm view @modelcontextprotocol/server-filesystem version
```

At the time of integration, npm reported `2026.8.31`; the configuration pins
that exact version. The first host start may populate npm's user cache and uv's
user cache. It must not create `package.json`, a lock file, or `node_modules` in
this repository. After one successful online discovery, offline reuse can be
checked with `$env:npm_config_offline = "true"` and `$env:UV_OFFLINE = "1"`
before repeating `list-tools`. Remove those two environment variables if the
caches have not yet been populated.

## Create one dedicated root and repository

Run from the course project root. This setup refuses to overwrite an existing
demo and does not change global Git identity:

```powershell
$projectRoot = (Get-Location).Path
$runtimeRoot = Join-Path $projectRoot "runtime"
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$demoBase = Join-Path $runtimeRoot "filesystem-git-mcp-manual-demo"
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

$env:PYTHONPATH = "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PHARMACY_MCP_DATABASE_PATH = $databasePath
$env:MCP_GIT_REPOSITORY_PATH = (Resolve-Path $demoRepo).Path
$env:MCP_FILESYSTEM_ROOT = $env:MCP_GIT_REPOSITORY_PATH
```

The host rejects a Filesystem system root, the whole user home, and the exact
course repository root. Its path policy also rejects relative paths, lexical
`..`, siblings, and symlink or junction escapes.

## Discover all three servers

This command starts Pharmacy, Git, and Filesystem together, performs each
`initialize` / `notifications/initialized` exchange, discovers every tool, and
closes all three processes on completion:

```powershell
python -m pharmacy_mcp.host.cli --log-file $logPath list-tools
```

Verify seven `pharmacy__...` names, dynamically returned `git__...` names, and
the 14 pinned `filesystem__...` names. The Filesystem server should include
`filesystem__list_allowed_directories`, `filesystem__list_directory`,
`filesystem__write_file`, and `filesystem__read_text_file`.

Optionally confirm Pharmacy remains usable:

```powershell
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool pharmacy__check_stock --arguments '{"sku":"MED-ANA-001","branch_id":"zona-5"}'
```

## Inspect the root and test the mutation gate

```powershell
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool filesystem__list_allowed_directories
$listArguments = @{path = $env:MCP_FILESYSTEM_ROOT} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool filesystem__list_directory --arguments $listArguments

$readme = Join-Path $env:MCP_FILESYSTEM_ROOT "README.md"
$readmeText = @"
# Filesystem MCP demo

Created by the official Filesystem MCP server.
"@
$writeArguments = @{path = $readme; content = $readmeText} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool filesystem__write_file --arguments $writeArguments
```

The last command must fail locally because it omitted `--allow-mutation`.
`README.md` must not exist, and there must be no outbound `tools/call` for that
attempt. Review the absolute path and content, then authorize only this call:

```powershell
python -m pharmacy_mcp.host.cli --log-file $logPath --allow-mutation call-tool filesystem__write_file --arguments $writeArguments
$readArguments = @{path = $readme} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool filesystem__read_text_file --arguments $readArguments
```

The request sent to Filesystem contains the original content. Its JSONL copy
uses `[WRITE CONTENT OMITTED]`; local policy records contain only tool names and
the checked path count.

## Inspect and commit through Git MCP

```powershell
$repoArguments = @{repo_path = $env:MCP_GIT_REPOSITORY_PATH} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_status --arguments $repoArguments
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_diff_unstaged --arguments $repoArguments

$addArguments = @{
    repo_path = $env:MCP_GIT_REPOSITORY_PATH
    files = @("README.md")
} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_add --arguments $addArguments
```

The unapproved `git_add` must fail locally. Review the root and file list, then
authorize staging and inspect the staged diff:

```powershell
python -m pharmacy_mcp.host.cli --log-file $logPath --allow-mutation call-tool git__git_add --arguments $addArguments
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_diff_staged --arguments $repoArguments
```

Pause for human confirmation before the disposable commit:

```powershell
$confirmation = Read-Host "Type COMMIT to create the demo commit in $demoRepo"
if ($confirmation -ne "COMMIT") {
    throw "Commit cancelled by user"
}

$commitArguments = @{
    repo_path = $env:MCP_GIT_REPOSITORY_PATH
    message = "docs: add filesystem MCP demo"
} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath --allow-mutation call-tool git__git_commit --arguments $commitArguments
$historyArguments = @{repo_path = $env:MCP_GIT_REPOSITORY_PATH; max_count = 1} | ConvertTo-Json -Compress
python -m pharmacy_mcp.host.cli --log-file $logPath call-tool git__git_log --arguments $historyArguments
git -C $demoRepo status --short
git -C $demoRepo log -1 --oneline
git -C $demoRepo config --local --get user.name
git -C $demoRepo config --local --get user.email
```

The status should be clean, history should contain
`docs: add filesystem MCP demo`, and the README content should match the value
read through Filesystem.

## Inspect logs and clean up safely

```powershell
$entries = Get-Content -LiteralPath $logPath | ForEach-Object { $_ | ConvertFrom-Json }
$entries | Group-Object server, direction, message_type | Select-Object Count, Name
$entries | Where-Object { $_.direction -eq "local" }
$entries | Where-Object { $_.server -eq "filesystem" -and $_.method -eq "tools/call" }
```

The log should contain `pharmacy`, `git`, and `filesystem` handshake/tool
traffic. It is valid JSONL, redacts sensitive keys first, limits each string to
4,096 characters and each payload to 16,384 characters, omits binary fields,
and marks truncation explicitly. It never modifies the wire message.

There is intentionally no automatic deletion command in this manual guide.
Inspect `$demoBase`, verify it is the dedicated disposable directory, and remove
only that exact directory when finished. Never substitute the project root,
user home, drive root, or an unresolved variable into recursive cleanup.

The automated integration test performs the same workflow under a unique
ignored `runtime/` directory, verifies all three child processes close, confirms
the main repository HEAD/status are unchanged, and then removes only its own
generated directory.

## Troubleshooting

- Missing root variables: both `MCP_FILESYSTEM_ROOT` and
  `MCP_GIT_REPOSITORY_PATH` must name existing absolute directories before the
  strict default configuration can load.
- Root rejected: use a dedicated directory, not a drive root, whole user home,
  the course repository, a relative path, or a path containing `..`.
- npx cannot resolve the package: remove offline mode, allow one run to populate
  the npm user cache, then retry. The pinned server treats command-line values as
  allowed directories and does not expose a conventional `--help` mode; when
  probed with `--help`, it starts stdio using that value as a directory.
- Mutation rejected: place `--allow-mutation` before `call-tool`. The flag
  authorizes only that CLI invocation and never permits a path outside the root.
- Git commit identity error: repeat the two `git config --local` commands in the
  disposable repository; do not configure a global identity.
- Empty unstaged diff for a new untracked file: inspect `git_status`, authorize
  `git_add`, and inspect `git_diff_staged`, which contains the README content.

## References

- [Official Filesystem MCP server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
- [`@modelcontextprotocol/server-filesystem` on npm](https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem)
- [Official Git MCP server](https://github.com/modelcontextprotocol/servers/tree/main/src/git)
- [MCP lifecycle, revision 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)

Both official servers are distributed under the MIT license in the upstream MCP
servers repository. They remain external processes; the student implementation
is the host client, lifecycle, JSON-RPC framing, routing, logging, and policy
layer that integrates them.
