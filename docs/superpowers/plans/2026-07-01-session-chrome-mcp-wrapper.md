# Session Chrome MCP Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python wrapper that gives each MCP server process its own Chrome instance with a random debugging port and temporary profile.

**Architecture:** The wrapper owns Chrome lifecycle, waits for DevTools readiness, then launches a downstream MCP command with environment variables describing the per-session browser. It bridges stdio between the client and downstream process until either side exits, then terminates both owned child processes and optionally removes the temporary profile.

**Tech Stack:** Python 3.12 standard library, `unittest`, Windows PowerShell for local execution.

## Global Constraints

- Use the project `.venv` for all Python execution.
- Do not commit or push changes.
- Add newly created files to git staging.
- Avoid third-party runtime dependencies.
- Keep Chrome ownership session-local: only close Chrome processes started by this wrapper.

---

### Task 1: Wrapper CLI and Lifecycle

**Files:**
- Modify: `main.py`
- Create: `README.md`

**Interfaces:**
- Produces: `python main.py -- <downstream command...>`
- Produces: environment variables `CHROME_DEVTOOLS_URL`, `CHROME_REMOTE_DEBUGGING_PORT`, `CHROME_USER_DATA_DIR`, `BROWSER_URL`

- [ ] Replace the PyCharm sample with a CLI that finds a free port, creates a temporary profile, starts Chrome, waits for `/json/version`, launches the downstream command, bridges stdio, and cleans up.
- [ ] Document configuration, examples, environment variables, and cleanup behavior.
- [ ] Verify help output with `.venv\\Scripts\\python.exe main.py --help`.

### Task 2: Focused Unit Tests

**Files:**
- Create: `tests/test_main.py`

**Interfaces:**
- Consumes: helper functions from `main.py`

- [ ] Test free-port endpoint formatting.
- [ ] Test downstream command environment injection.
- [ ] Test Chrome argument construction.
- [ ] Run `.venv\\Scripts\\python.exe -m unittest discover -v`.

### Task 3: Git Staging

**Files:**
- Stage: `main.py`, `README.md`, `tests/test_main.py`, `docs/superpowers/plans/2026-07-01-session-chrome-mcp-wrapper.md`

- [ ] Run `git status --short`.
- [ ] Run `git add` for new and modified project files.
- [ ] Confirm staged files with `git status --short`.
