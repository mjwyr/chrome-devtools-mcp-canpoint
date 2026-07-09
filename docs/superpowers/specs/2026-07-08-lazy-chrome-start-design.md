# Lazy Chrome Start Design

## Goal

Avoid starting or showing a Chrome browser instance for every new MCP session.
The wrapper should start Chrome only when the current conversation actually
uses a Chrome DevTools MCP tool, while keeping normal MCP startup and tool
discovery working.

## Scope

- Default to lazy Chrome startup.
- Keep an eager launch mode for the previous behavior.
- Default visible Chrome sessions to a quiet startup mode that attempts to avoid
  stealing focus.
- Keep `--headless` as an explicit fully hidden mode.
- Preserve the existing lifecycle guarantee: only Chrome processes and generated
  profiles owned by this wrapper are cleaned up.

## Architecture

The wrapper remains the MCP stdio process that the client starts. It still
starts the downstream MCP command immediately so the client can complete
`initialize`, `notifications/initialized`, and `tools/list` without the wrapper
having to duplicate downstream tool metadata.

The wrapper changes only the client-to-downstream stdin bridge. Instead of
copying bytes blindly, it parses MCP stdio messages framed by `Content-Length`,
inspects JSON-RPC methods, and forwards the original messages unchanged.

In lazy mode, Chrome is launched before forwarding the first real use request.
The trigger methods are:

- `tools/call`
- `resources/read`
- `prompts/get`

For the current Chrome DevTools MCP use case, `tools/call` is the important
trigger. The other methods are included so the lazy proxy behaves correctly if a
future downstream server exposes browser-backed resources or prompts.

## Startup Data Flow

At wrapper startup:

1. Parse CLI options and normalize the downstream command.
2. Choose a random remote debugging port.
3. Choose the user data directory path and profile directory name.
4. Start the downstream MCP command with `CHROME_DEVTOOLS_URL`,
   `BROWSER_URL`, `CHROME_REMOTE_DEBUGGING_PORT`, and `CHROME_USER_DATA_DIR`.
5. Bridge downstream stdout back to the MCP client.
6. Bridge client stdin through the lazy MCP parser.

Chrome is not started during these steps when `--launch-mode lazy` is active.

At first trigger request:

1. Resolve the Chrome executable.
2. Materialize the selected profile:
   - `isolated`: create the generated user data directory.
   - `inherit`: validate the source user data directory/profile.
   - `copy`: copy the selected source profile into the generated session
     directory, applying the existing safe-copy filters.
3. Start Chrome with the selected port/profile/window mode.
4. Wait for `/json/version` on the DevTools endpoint.
5. Forward the original trigger request to downstream MCP.

## Window Modes

Add `--window-mode quiet|visible|headless`, defaulting to `quiet`.

- `quiet`: start Chrome headful for compatibility, add `--start-minimized`, and
  on Windows use `STARTUPINFO` with a non-activating minimized show state where
  available. This is best-effort; Chrome or the OS can still focus a window in
  some cases.
- `visible`: keep the previous headful behavior.
- `headless`: add `--headless=new`.

Keep `--headless` as a backward-compatible alias for `--window-mode headless`.

## Eager Mode

Add `--launch-mode lazy|eager`, defaulting to `lazy`.

`eager` preserves the old lifecycle: materialize the profile, start Chrome, wait
for DevTools, then run the downstream MCP bridge.

## Error Handling

Lazy Chrome startup can fail after the downstream MCP process has already
started. If startup fails while handling a JSON-RPC request with an `id`, the
wrapper should write a JSON-RPC error response for that request and avoid
forwarding it. If the message has no `id`, the wrapper should log the failure to
stderr and continue forwarding later messages when possible.

Cleanup remains idempotent and safe when Chrome was never started.

## Testing

Add focused unit coverage for:

- Window mode argument construction.
- Lazy mode not launching Chrome before non-trigger MCP messages.
- Lazy mode launching Chrome before a trigger MCP message.
- Startup failure returning a JSON-RPC error response for a request with `id`.
- Eager mode preserving current launch behavior.
- Existing helper behavior remaining unchanged.

Run the existing unittest suite after implementation.
