# PyPI Release Guide

This project publishes as `chrome-devtools-mcp-canpoint`.

## Prerequisites

1. Register accounts:
   - PyPI: https://pypi.org/account/register/
   - TestPyPI: https://test.pypi.org/account/register/
2. Enable 2FA on both accounts.
3. Create API tokens on both sites.
4. Use `__token__` as the username when uploading with `twine`.
5. Use the full token value, including the `pypi-` prefix, as the password.

## Install Release Tools

Run from the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -U build twine
```

## Clean Old Build Artifacts

```powershell
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force src\*.egg-info -ErrorAction SilentlyContinue
```

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

## Build Distributions

Preferred:

```powershell
.\.venv\Scripts\python.exe -m build --no-isolation
```

If Windows file-lock cleanup causes trouble, build separately:

```powershell
.\.venv\Scripts\python.exe -m build --wheel --no-isolation
.\.venv\Scripts\python.exe -m build --sdist --no-isolation
```

Expected files:

```text
dist/chrome_devtools_mcp_canpoint-0.1.0-py3-none-any.whl
dist/chrome_devtools_mcp_canpoint-0.1.0.tar.gz
```

## Validate Distributions

```powershell
.\.venv\Scripts\python.exe -m twine check dist/*
```

## Upload to TestPyPI

```powershell
.\.venv\Scripts\python.exe -m twine upload --repository testpypi dist/*
```

Test the package from TestPyPI:

```powershell
uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ chrome-devtools-mcp-canpoint --help
```

## Upload to PyPI

Only do this after TestPyPI works:

```powershell
.\.venv\Scripts\python.exe -m twine upload dist/*
```

## Verify Published Package

```powershell
uvx chrome-devtools-mcp-canpoint --help
```

Run with Chrome DevTools MCP:

```powershell
uvx chrome-devtools-mcp-canpoint -- npx -y chrome-devtools-mcp@latest --browser-url={browser_url} --no-usage-statistics
```

Codex config:

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

## Version Bump Checklist

Before publishing another release:

1. Update `version` in `pyproject.toml`.
2. Update `__version__` in `src/chrome_devtools_mcp_canpoint/__init__.py`.
3. Re-run tests.
4. Rebuild `dist/`.
5. Upload with `twine`.

PyPI does not allow replacing an existing version. If upload partially succeeds,
bump the version before retrying.
