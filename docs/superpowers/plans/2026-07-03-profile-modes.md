# Profile Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional Chrome profile discovery, inherit, and copy modes so users can access bookmarks and login state when they explicitly opt in.

**Architecture:** Keep isolated temporary profiles as the default. Add helpers that find the default Chrome user data directory, resolve source profile paths, copy safe profile data into session directories, and optionally include sensitive data only with an explicit flag.

**Tech Stack:** Python 3.12 standard library, unittest.

## Global Constraints

- Default mode remains isolated.
- `inherit` uses the source user data directory directly and does not delete it.
- `copy` creates a generated session directory and deletes it after exit unless `--keep-profile` is set.
- Sensitive data such as cookies and saved passwords is copied only with `--include-sensitive-profile-data`.
- Do not commit or push changes.

---

### Task 1: CLI Profile Modes

**Files:**
- Modify: `src/chrome_devtools_mcp_canpoint/cli.py`

**Interfaces:**
- Add: `--profile-mode isolated|inherit|copy`
- Add: `--source-user-data-dir`
- Add: `--source-profile`
- Add: `--include-sensitive-profile-data`

- [ ] Add profile path discovery helpers.
- [ ] Add copy filters for cache, lock, and sensitive files.
- [ ] Wire profile mode selection into `main()`.

### Task 2: Tests and Docs

**Files:**
- Modify: `tests/test_main.py`
- Modify: `README.md`

- [ ] Test default Chrome user data path detection.
- [ ] Test inherit mode path selection.
- [ ] Test copy mode safe filtering and sensitive opt-in.
- [ ] Document profile modes and security caveats.

### Task 3: Verification

- [ ] Run unit tests.
- [ ] Build wheel.
- [ ] Verify console script help includes new options.
