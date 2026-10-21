#!/bin/bash
# Remove the locked conductor gate. The plugin gate (if the plugin is on) takes
# over again at the next session.
# Run it yourself in a plain Terminal window:
#   sudo "/Library/Application Support/ClaudeCode/conductor-gate/gatectl" uninstall
set -euo pipefail
cd /
case "$(uname -s)" in
  Darwin) CC="/Library/Application Support/ClaudeCode" ;;
  *) CC="/etc/claude-code" ;;
esac
ROOT="$CC/conductor-gate"
# install.sh writes the Python path here.
PYBIN="@PYBIN@"

if [ "$(id -u)" != 0 ]; then echo "Run this script with sudo."; exit 1; fi

# Check the managed settings first. Nothing changes if they hold more than our hook.
if [ -f "$CC/managed-settings.json" ]; then
  if ! "$PYBIN" -I - "$CC/managed-settings.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = list(d.keys()) == ["hooks"] and list(d["hooks"].keys()) == ["PreToolUse"]
if ok:
    cmds = [h.get("command", "") for e in d["hooks"]["PreToolUse"] for h in e.get("hooks", [])]
    ok = len(cmds) == 1 and "conductor-gate.py" in cmds[0]
sys.exit(0 if ok else 1)
PY
  then
    echo "STOP: managed-settings.json holds more than our hook. Edit it by hand."; exit 1
  fi
  rm -f "$CC/managed-settings.json"
fi
rm -rf "$ROOT"
echo "UNLOCKED. Restart your Claude sessions."
sudo -k
