# MCP Startup Debug Summary

## What Happened

`chrome-devtools-mcp-canpoint` kept timing out during Codex MCP startup even
after lazy Chrome startup was added. Local smoke tests passed for several
versions, but Codex still reported that the `chrome-devtools` MCP client timed
out.

## Why It Took So Long

The early tests reproduced only part of Codex startup behavior. They verified
that `initialize`, `tools/list`, and other startup list requests could be
answered without launching Chrome, but they used the wrong stdio framing
assumption.

The wrapper originally read and wrote MCP messages using `Content-Length`
headers. Codex was launching the wrapper successfully, but its actual stdio MCP
messages were newline-delimited JSON. Because the wrapper waited for a
`Content-Length` header, it never read the incoming `initialize` request. From
the outside this looked like a startup timeout, not a protocol parse failure.

Several secondary issues also obscured the root cause:

- The config was sometimes changed back to `uvx`, which could run a cached or
  PyPI-installed version instead of the fixed local venv version.
- The earlier lazy implementation still started the downstream official MCP
  process too early.
- `tools/list` initially tried to query the official package dynamically, which
  could still touch `npx` during startup.
- The debug log only showed that the wrapper process started; until stdio
  framing was suspected, there was no per-message log because no message was
  successfully parsed.

## Final Root Cause

The final blocker was MCP stdio framing mismatch:

- Codex sent newline-delimited JSON.
- The wrapper only accepted `Content-Length` framed messages.
- Result: wrapper process started, but never parsed `initialize`, so Codex
  waited until startup timeout.

## Fix In 0.1.9

Version `0.1.9` fixes the startup path by:

- Supporting newline-delimited JSON input.
- Keeping compatibility with `Content-Length` framed input.
- Returning newline-delimited JSON responses.
- Serving startup `tools/list` from a bundled fallback tool list.
- Avoiding Chrome, official MCP, and `npx` during startup.
- Starting Chrome and downstream official MCP only on real browser-backed tool
  calls.

## Verification

The fixed version was validated with:

- `49` unit tests passing.
- Package build and `twine check` passing.
- Newline JSON startup smoke test returning:
  - `initialize`
  - `tools/list`
  - `resources/list`
  - `prompts/list`
- `tools/list` returning `29` tools.
- Debug log confirming `STARTED_DOWNSTREAM False` during startup.
- Live Chrome DevTools MCP test successfully opening Baidu and executing
  `evaluate_script`.

## Lessons

For MCP wrappers, test the exact client transport framing, not only the logical
JSON-RPC methods. A process that starts but never logs `initialize` usually
means the wrapper is blocked before message parsing, often because of stdio
framing or buffering assumptions.

When debugging Codex MCP startup:

1. Pin the config to an absolute executable path before testing.
2. Log process startup and every parsed MCP method.
3. Verify whether `initialize` is parsed.
4. Avoid network, `npx`, browser startup, or downstream subprocess startup
   before tool discovery completes.
5. Smoke test both newline-delimited JSON and `Content-Length` framing.
