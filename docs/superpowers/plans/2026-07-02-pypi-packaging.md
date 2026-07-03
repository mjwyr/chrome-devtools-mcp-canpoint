# PyPI Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the Chrome DevTools MCP wrapper so users can run it with `uvx chrome-devtools-mcp-canpoint`.

**Architecture:** Move the runtime implementation into a `src/` Python package with a console-script entry point. Keep root `main.py` as a compatibility shim for existing local Codex config. Use standard `pyproject.toml` metadata and no runtime dependencies.

**Tech Stack:** Python 3.12 standard library, setuptools build backend, unittest.

## Global Constraints

- Use the project `.venv` for Python commands.
- Do not commit or push changes.
- Add new and modified project files to git staging.
- Keep the current local `main.py` command working.

---

### Task 1: Package Structure

**Files:**
- Create: `src/chrome_devtools_mcp_canpoint/__init__.py`
- Create: `src/chrome_devtools_mcp_canpoint/cli.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `chrome_devtools_mcp_canpoint.cli:main`
- Preserves: `python main.py -- ...`

- [ ] Move implementation from `main.py` into `src/chrome_devtools_mcp_canpoint/cli.py`.
- [ ] Replace `main.py` with a shim importing and calling `main()`.

### Task 2: Build Metadata

**Files:**
- Create: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Produces console command: `chrome-devtools-mcp-canpoint`

- [ ] Add project metadata, package discovery, Python version, license placeholder, and console script.
- [ ] Update README with `uvx` usage and Codex config example.

### Task 3: Verification

**Files:**
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: package module imports from `src/`

- [ ] Update tests to import `chrome_devtools_mcp_canpoint.cli`.
- [ ] Run unit tests.
- [ ] Build sdist/wheel.
- [ ] Install or run the console script locally and verify `--help`.
