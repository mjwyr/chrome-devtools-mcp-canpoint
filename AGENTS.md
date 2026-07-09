# AGENTS.md

## Project Overview

`chrome-devtools-mcp-canpoint` is a Python package that wraps downstream Chrome
DevTools MCP commands with a session-local Chrome launcher.

The wrapper selects a random remote debugging port, prepares a session-specific
Chrome user data directory, passes the browser endpoint to the downstream MCP
process, and cleans up the Chrome process/profile when the session exits. Chrome
starts lazily by default, so MCP startup and tool discovery can finish before a
browser is opened.

The package is implemented under `src/chrome_devtools_mcp_canpoint/` and exposes
the console script:

```powershell
chrome-devtools-mcp-canpoint
```

## Development Commands

Use the project virtual environment when available:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\chrome-devtools-mcp-canpoint.exe --help
```

Build package artifacts:

```powershell
.\.venv\Scripts\python.exe -m build --no-isolation
```

Check built distributions before publishing:

```powershell
.\.venv\Scripts\python.exe -m twine check dist/*
```

## Dependency Notes

Runtime code currently uses only the Python standard library. Do not add
third-party runtime dependencies unless they are required by the package itself.

Build and publishing tools such as `build`, `twine`, `setuptools`, and `wheel`
are development/publishing dependencies, not runtime dependencies.

## Versioning and PyPI Publishing

Every code functionality change must update the package version in
`pyproject.toml`.

PyPI does not allow replacing or re-uploading an existing package version. If a
feature or behavior changes but the version number stays the same, publishing
will fail with an upload error such as `HTTPError: 400 Bad Request`.

Before building for PyPI:

1. Update `project.version` in `pyproject.toml`.
2. Remove old build artifacts from `dist/`, `build/`, and generated
   `*.egg-info` directories.
3. Rebuild the package.
4. Run `twine check dist/*`.
5. Upload the new version.

Suggested cleanup command on PowerShell:

```powershell
Remove-Item -Recurse -Force dist, build, src\chrome_devtools_mcp_canpoint.egg-info
```

Then rebuild:

```powershell
.\.venv\Scripts\python.exe -m build --no-isolation
```

## Coding Guidelines

- Keep changes scoped to the requested behavior.
- Prefer the Python standard library where practical.
- Maintain Windows compatibility; this package is commonly used from
  PowerShell and with Windows Chrome installations.
- Keep tests in `tests/` updated when behavior changes.
- Do not commit generated build artifacts unless explicitly requested.
