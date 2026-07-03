# chrome-devtools-mcp-canpoint

Session-local Chrome launcher for Chrome DevTools MCP workflows.

- Repository: https://github.com/mjwyr/chrome-devtools-mcp-canpoint.git
- Author: 若清风

This wrapper starts one dedicated Chrome process per MCP server process. Each
session gets its own random remote debugging port and a generated user data
directory under the current working directory, so multiple agent sessions do not
fight over port `9222` or a shared Chrome profile. On Windows it also resolves
common command shims such as `npx.cmd`, so downstream MCP commands can be passed
as normal argument lists.

## Usage

Run it directly with `uvx`:

```powershell
uvx chrome-devtools-mcp-canpoint -- <downstream-mcp-command>
```

Example:

```powershell
uvx chrome-devtools-mcp-canpoint -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

If Chrome is not installed in the default Windows location, pass it explicitly:

```powershell
uvx chrome-devtools-mcp-canpoint --chrome-path "D:\Apps\Chrome\chrome.exe" -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

## Codex MCP Config

After publishing to PyPI, configure Codex like this:

```toml
[mcp_servers.chrome-devtools]
command = "uvx"
args = [
  "chrome-devtools-mcp-canpoint",
  "--",
  "npx",
  "-y",
  "chrome-devtools-mcp@latest",
  "--browser-url={browser_url}",
  "--no-usage-statistics"
]
startup_timeout_sec = 60
```

## Claude Code MCP Config

Claude Code stores global MCP server entries in `~/.claude.json` under
`mcpServers`. A matching entry looks like this:

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "chrome-devtools-mcp-canpoint",
        "--",
        "npx",
        "-y",
        "chrome-devtools-mcp@latest",
        "--browser-url={browser_url}"
      ],
      "env": {}
    }
  }
}
```

Add it from PowerShell to avoid Git Bash/MSYS path conversion changing `/c`-style
arguments:

```powershell
claude mcp add chrome-devtools -- uvx chrome-devtools-mcp-canpoint -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

For older wrapper versions that do not resolve Windows command shims, use the
temporary workaround below. It should not be needed after this package version is
installed:

```powershell
claude mcp add chrome-devtools -- uvx chrome-devtools-mcp-canpoint -- cmd /c npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

## Session Profile Directory

By default, generated Chrome profiles are created under:

```text
<current-working-directory>/.chrome-mcp-sessions/<uuid>
```

Override the parent directory with `--session-root`:

```powershell
uvx chrome-devtools-mcp-canpoint --session-root .\.chrome-mcp-sessions -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

Use `--user-data-dir` only when you want an exact profile directory instead of a
generated per-session subdirectory.

## Chrome Profile Modes

The default profile mode is `isolated`. It creates a fresh project-local Chrome
profile for each MCP session and deletes it when the downstream MCP exits. This
keeps concurrent agent sessions from sharing ports, cookies, locks, or profile
state.

Use `inherit` when you explicitly want the MCP-controlled Chrome to use an
existing local Chrome user data directory directly:

```powershell
uvx chrome-devtools-mcp-canpoint --profile-mode inherit -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

By default, the source user data directory is detected from the local platform:

- Windows: `%LOCALAPPDATA%\Google\Chrome\User Data`
- macOS: `~/Library/Application Support/Google/Chrome`
- Linux: `~/.config/google-chrome`

Override detection or select a non-default profile with:

```powershell
uvx chrome-devtools-mcp-canpoint --profile-mode inherit --source-user-data-dir "C:\Users\me\AppData\Local\Google\Chrome\User Data" --source-profile "Profile 1" -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

Use `copy` when you want a generated session profile prefilled from an existing
Chrome profile:

```powershell
uvx chrome-devtools-mcp-canpoint --profile-mode copy --source-profile "Default" -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

Safe copy mode includes regular profile files such as bookmarks and preferences,
but excludes caches, lock files, cookies, sessions, web data, and saved-login
databases. To copy those sensitive databases too, opt in explicitly:

```powershell
uvx chrome-devtools-mcp-canpoint --profile-mode copy --include-sensitive-profile-data -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

Security notes:

- `inherit` exposes the real Chrome profile, including cookies, sessions, saved
  passwords, and browsing state, to the downstream MCP process.
- `inherit` can conflict with an already running Chrome instance that uses the
  same user data directory.
- `copy --include-sensitive-profile-data` copies sensitive databases into the
  temporary session profile, but Chrome encryption may still prevent copied
  passwords or cookies from being usable in the new session.
- Copied temporary profiles are deleted after exit unless `--keep-profile` is
  set.

## Environment Passed to the Downstream MCP

The downstream command receives these variables:

- `CHROME_DEVTOOLS_URL`: `http://127.0.0.1:<random-port>`
- `BROWSER_URL`: same value as `CHROME_DEVTOOLS_URL`
- `CHROME_REMOTE_DEBUGGING_PORT`: selected port
- `CHROME_USER_DATA_DIR`: session profile path

Configure the real Chrome MCP package to use one of these values as its browser
endpoint. This wrapper also expands placeholders in downstream command
arguments:

- `{browser_url}` or `{devtools_url}`: `http://127.0.0.1:<random-port>`
- `{port}`: selected port
- `{user_data_dir}`: generated profile path

## Cleanup

When the downstream MCP exits, this wrapper terminates only the Chrome process it
started. Temporary profiles are deleted by default.

Use `--keep-profile` when debugging:

```powershell
uvx chrome-devtools-mcp-canpoint --keep-profile -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url}
```

## Options

```powershell
uvx chrome-devtools-mcp-canpoint --help
```

Useful options:

- `--chrome-path`: path to Chrome; defaults to `CHROME_PATH` or common install paths
- `--profile-mode`: `isolated`, `inherit`, or `copy`; defaults to `isolated`
- `--source-user-data-dir`: Chrome user data root for `inherit` or `copy`
- `--source-profile`: Chrome profile directory name, default `Default`
- `--include-sensitive-profile-data`: include cookies, sessions, and saved-login databases in `copy` mode
- `--session-root`: parent directory for generated project-local profiles
- `--user-data-dir`: explicit profile directory instead of a generated one
- `--keep-profile`: leave the temporary profile on disk
- `--headless`: start Chrome with `--headless=new`
- `--chrome-arg`: pass extra arguments to Chrome, repeatable
- `--devtools-timeout`: seconds to wait for `/json/version`

## Development

Use the project virtual environment for local development:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\chrome-devtools-mcp-canpoint.exe --help
```

Build distributions:

```powershell
.\.venv\Scripts\python.exe -m build
```
