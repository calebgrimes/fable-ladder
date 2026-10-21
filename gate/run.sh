#!/bin/sh
# Start the conductor gate with python3.
# If the gate cannot run (no python3, or conductor-gate.py is missing), this
# script allows the tool call (exit 0) and shows one warning per session. So a
# broken install never blocks every tool call. In that case the gate is OFF.
here=$(cd "$(dirname "$0")" && pwd -P)
# Claude Code runs this script by the plugin path as it was added. That path can
# be a symlink. Pass it on, so the gate accepts both forms of the plugin folder.
raw=$(cd "$(dirname "$0")/.." && pwd -L)
CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$raw}"
export CLAUDE_PLUGIN_ROOT
py=$(command -v python3 2>/dev/null)
# On macOS, /usr/bin/python3 is only a stub until the Command Line Tools exist.
# Do not run the stub: it opens an install dialog on every call.
if [ "$py" = /usr/bin/python3 ] && [ "$(uname -s)" = Darwin ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  py=""
fi
# The gate needs Python 3.9 or newer. An older Python fails with an error on each
# call. Claude Code shows the error and still runs the call.
if [ -n "$py" ] && [ -f "$here/conductor-gate.py" ]; then
  exec "$py" -I "$here/conductor-gate.py" "$@"
fi
if [ -n "$py" ]; then
  kind=no-gate
  text='Conductor gate is OFF: the file gate/conductor-gate.py is missing. Tool calls are not checked. Install the ladder plugin again with /plugin, then restart Claude Code.'
else
  kind=no-python
  text='Conductor gate is OFF: python3 was not found. Tool calls are not checked. Install python3 (on macOS: xcode-select --install), then restart Claude Code.'
fi
# Read the session id from the hook input, and warn once per session.
sid=$(sed -n 's/.*"session_id" *: *"\([A-Za-z0-9_-]*\)".*/\1/p' | head -n 1)
dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/conductor-gate"
mark="$dir/$kind-${sid:-unknown}"
if [ ! -e "$mark" ]; then
  mkdir -p "$dir" 2>/dev/null && : > "$mark" 2>/dev/null
  printf '{"systemMessage": "%s"}\n' "$text"
fi
exit 0
