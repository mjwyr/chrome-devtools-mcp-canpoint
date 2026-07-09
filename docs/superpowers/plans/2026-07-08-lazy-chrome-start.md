# Lazy Chrome Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start Chrome only when a conversation actually calls a browser-backed MCP tool, and default visible Chrome startup to a quiet best-effort mode.

**Architecture:** Keep the downstream Chrome DevTools MCP server running for normal MCP handshake and tool discovery. Replace only the client-to-downstream stdin bridge with a framed JSON-RPC proxy that starts Chrome before forwarding trigger requests. Move profile materialization and Chrome process creation behind an idempotent session manager so eager mode can preserve current behavior and lazy mode can defer it.

**Tech Stack:** Python 3.10+ standard library, `unittest`, MCP stdio framing with `Content-Length` headers.

## Global Constraints

- Do not add third-party runtime dependencies.
- Do not commit or push changes; commit operations are user-owned.
- Add newly created files to git staging.
- Keep Chrome ownership session-local: only close Chrome processes started by this wrapper.
- Default launch mode is `lazy`.
- Default window mode is `quiet`, which is best-effort and does not guarantee the OS or Chrome will never focus a window.
- Preserve `--headless` as a backward-compatible alias for headless window mode.

---

## File Structure

- Modify `src/chrome_devtools_mcp_canpoint/cli.py`: add profile planning, Chrome session management, quiet startup options, MCP stdio parsing, lazy bridge, and CLI flags.
- Modify `tests/test_main.py`: add focused unit tests for lazy bridge behavior, window mode arguments, profile planning, and eager startup coordination.
- Modify `README.md`: document lazy startup, launch modes, quiet window mode, and headless compatibility.

---

### Task 1: Profile Planning Without Eager Materialization

**Files:**
- Modify: `src/chrome_devtools_mcp_canpoint/cli.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `ProfilePlan(user_data_dir: Path, generated_session_dir: bool, profile_directory: str | None, profile_mode: str, source_user_data_dir: Path | None, source_profile: str, include_sensitive_profile_data: bool)`
- Produces: `plan_user_data_dir(...) -> ProfilePlan`
- Produces: `materialize_profile(plan: ProfilePlan) -> ProfileSelection`

- [ ] Add `ProfilePlan` next to `ProfileSelection`.
- [ ] Add `resolve_source_user_data_dir_path(value: str | None) -> Path`, which returns the configured or default source path without requiring it to exist.
- [ ] Add `plan_user_data_dir(...)` that chooses paths without copying or validating existing Chrome profile data.
- [ ] Add `materialize_profile(plan)` that performs the existing validation/copy/create work when Chrome is actually needed.
- [ ] Keep the existing `select_user_data_dir(...)` eager behavior by implementing it as `materialize_profile(plan_user_data_dir(...))`.
- [ ] Add tests proving copy mode planning does not copy files until `materialize_profile(...)` runs.

### Task 2: Window Mode And Chrome Launch Helper

**Files:**
- Modify: `src/chrome_devtools_mcp_canpoint/cli.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `WINDOW_MODES = ("quiet", "visible", "headless")`
- Produces: `ChromeSessionConfig(..., window_mode: str = "visible")`
- Produces: `build_chrome_startupinfo(window_mode: str)`
- Produces: `start_chrome_process(config: ChromeSessionConfig) -> tuple[subprocess.Popen[bytes], object | None]`

- [ ] Add `WINDOW_MODES`.
- [ ] Extend `ChromeSessionConfig` with `window_mode`, keeping existing callers valid through a default value.
- [ ] Update `build_chrome_args(...)` so `quiet` adds `--start-minimized` unless headless is active.
- [ ] Add `build_chrome_startupinfo(...)` that returns Windows `STARTUPINFO` with `SW_SHOWMINNOACTIVE` for quiet mode and `None` otherwise.
- [ ] Add `start_chrome_process(...)` to centralize `subprocess.Popen(...)`, job creation, job assignment, and cleanup on launch failure.
- [ ] Add tests for quiet, visible, and headless argument behavior.

### Task 3: Managed Chrome Session

**Files:**
- Modify: `src/chrome_devtools_mcp_canpoint/cli.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `class ChromeSessionManager`
- Produces: `ChromeSessionManager.ensure_started() -> None`
- Produces: `ChromeSessionManager.cleanup() -> None`

- [ ] Add `ChromeSessionManager` with constructor parameters for Chrome path option, port, profile plan, keep-profile flag, devtools timeout, headless flag, window mode, and extra Chrome args.
- [ ] Implement `ensure_started()` as idempotent: materialize profile, resolve Chrome path, start Chrome, wait for DevTools, and mark the session started.
- [ ] If DevTools readiness fails, terminate the launched Chrome process, close its job object, and re-raise the original failure.
- [ ] Implement `cleanup()` so it is safe when Chrome was never started.
- [ ] Add tests proving `ensure_started()` starts once and `cleanup()` does not try to terminate a missing Chrome process.

### Task 4: Lazy MCP Stdio Bridge

**Files:**
- Modify: `src/chrome_devtools_mcp_canpoint/cli.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Produces: `MCP_LAZY_TRIGGER_METHODS = frozenset({"tools/call", "resources/read", "prompts/get"})`
- Produces: `encode_stdio_json(message: Mapping[str, object]) -> bytes`
- Produces: `read_stdio_message(source) -> tuple[bytes, bytes] | None`
- Produces: `json_rpc_startup_error(request_id: object, exc: Exception) -> bytes`
- Produces: `bridge_mcp_client_stream(source, target, ensure_chrome, error_target, log_target) -> None`

- [ ] Add stdio message reader that preserves original headers and payload for forwarding.
- [ ] Add JSON decoding helper to inspect the request method and id.
- [ ] Add JSON-RPC error encoder for lazy startup failures.
- [ ] Add lazy bridge loop: read framed message, call `ensure_chrome()` before trigger methods, forward the original message if startup succeeds, and return a JSON-RPC error if startup fails for a request with `id`.
- [ ] Add tests for non-trigger messages not starting Chrome, trigger messages starting Chrome before forwarding, and startup failure returning an error instead of forwarding the request.

### Task 5: CLI Wiring And Documentation

**Files:**
- Modify: `src/chrome_devtools_mcp_canpoint/cli.py`
- Modify: `README.md`
- Test: `tests/test_main.py`

**Interfaces:**
- Adds CLI flag: `--launch-mode lazy|eager`
- Adds CLI flag: `--window-mode quiet|visible|headless`
- Preserves CLI flag: `--headless`
- Updates: `run_downstream(command, env, ensure_chrome=None) -> int`

- [ ] Add parser flags for launch mode and window mode.
- [ ] Change `main(...)` to choose port and profile plan before starting downstream.
- [ ] In eager mode, call `ChromeSessionManager.ensure_started()` before `run_downstream(...)`.
- [ ] In lazy mode, pass `ChromeSessionManager.ensure_started` into `run_downstream(...)`.
- [ ] Update `run_downstream(...)` to use the lazy bridge only when `ensure_chrome` is provided.
- [ ] Register cleanup through the manager and keep signal handling behavior.
- [ ] Update README usage and option descriptions for lazy/eager and quiet/headless modes.
- [ ] Add tests proving eager mode calls startup before downstream execution and lazy mode passes startup into the downstream bridge.

### Task 6: Verification

**Files:**
- Run only; no planned source edits unless verification exposes a defect.

- [ ] Run `.\\.venv\\Scripts\\python.exe -m unittest discover -v` if the project virtual environment exists.
- [ ] If `.venv` is unavailable, run `python -m unittest discover -v`.
- [ ] Run `.\\.venv\\Scripts\\python.exe -m build` if build dependencies are installed.
- [ ] Run `git status --short` and confirm new files are staged and no commit was created.
