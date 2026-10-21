#!/bin/bash
# Lock the conductor gate: install a root-owned copy that runs from the managed
# settings. Then a Claude session cannot turn the gate off or edit its rules.
# Run it yourself in a plain Terminal window, not from Claude (it asks on the tty).
# Run it from a fresh git clone at the release tag, not from the plugin cache:
#   git clone --depth 1 --branch v0.5.0 https://github.com/calebgrimes/fable-ladder ~/fable-ladder-lock
#   sudo bash ~/fable-ladder-lock/gate/lock/install.sh
# macOS is tested. Linux (/etc/claude-code) is written but not tested.
set -euo pipefail
# The source: the gate folder of the plugin (one level up from this script).
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd /
umask 022
# A Claude session can edit the plugin cache. The files that become root-owned must
# come from a clone that you made yourself, at the release tag.
case "$SRC" in
  */.claude/plugins/*) echo "STOP: $SRC is in the Claude plugin cache. Run install.sh from a fresh git clone at the release tag (see the README, Lock it)."; exit 1 ;;
esac

if [ "$(id -u)" != 0 ]; then echo "Run this script with sudo."; exit 1; fi
if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = root ]; then echo "Run with sudo from your own account, not as root."; exit 1; fi

case "$(uname -s)" in
  Darwin) CC="/Library/Application Support/ClaudeCode"; GROUP=wheel ;;
  Linux) CC="/etc/claude-code"; GROUP=root; echo "NOTE: Linux is not tested." ;;
  *) echo "STOP: only macOS and Linux are supported."; exit 1 ;;
esac
ROOT="$CC/conductor-gate"
STAMP="$(date +%Y%m%d-%H%M%S)"

# The Python for the hook. It must be 3.9 or newer and owned by root, so that
# no user process can replace it.
CLT_PY="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"
if [ -x "$CLT_PY" ]; then PYBIN="$CLT_PY"
else
  PYBIN="$(sudo -u "$SUDO_USER" sh -c 'command -v python3' || true)"
  [ -n "$PYBIN" ] && PYBIN="$("$PYBIN" -I -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true)"
fi
if [ -z "$PYBIN" ] || [ ! -x "$PYBIN" ]; then
  echo "STOP: no python3 found. On macOS run: xcode-select --install"; exit 1
fi
if ! "$PYBIN" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "STOP: $PYBIN is older than Python 3.9."; exit 1
fi
if [ "$(stat -c %u "$PYBIN" 2>/dev/null || stat -f %u "$PYBIN")" != 0 ]; then
  echo "STOP: $PYBIN is not owned by root. Use a system Python (on macOS: xcode-select --install)."; exit 1
fi
echo "Python for the hook: $PYBIN ($("$PYBIN" -I -c 'import sys; print(sys.version.split()[0])'))"

FILES="conductor-gate.py config.json lock/gatectl lock/uninstall.sh"

# Copy the source ONCE into a root-owned temp folder in /tmp. Refuse symlinks.
# Every later step reads only this copy, so a change to the source after this
# point has no effect.
STAGE="$(mktemp -d /tmp/gate-lock.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
chmod 700 "$STAGE"
mkdir "$STAGE/lock"
for f in $FILES; do
  if [ -L "$SRC/$f" ] || [ ! -f "$SRC/$f" ]; then echo "STOP: $SRC/$f is a symlink or not a plain file."; exit 1; fi
  sudo -u "$SUDO_USER" cat "$SRC/$f" > "$STAGE/$f"
done
# Write the Python path into the helper scripts.
for f in lock/gatectl lock/uninstall.sh; do
  "$PYBIN" -I - "$STAGE/$f" "$PYBIN" <<'PY'
import sys
path, py = sys.argv[1], sys.argv[2]
text = open(path).read()
if text.count('PYBIN="@PYBIN@"') != 1:
    raise SystemExit("STOP: no PYBIN line in " + path)
open(path, "w").write(text.replace('PYBIN="@PYBIN@"', 'PYBIN="%s"' % py))
PY
done
# The managed settings: one PreToolUse hook. An empty environment (env -i),
# the root-owned Python, isolated mode (-I), and the locked gate file.
"$PYBIN" -I - "$STAGE/managed-settings.json" "$PYBIN" "$ROOT/conductor-gate.py" <<'PY'
import json, sys
out, py, gate = sys.argv[1:4]
cmd = '/usr/bin/env -i %s -I "%s"' % (py, gate)
d = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": 10}]}]}}
open(out, "w").write(json.dumps(d, indent=2) + "\n")
PY
"$PYBIN" -I -m json.tool "$STAGE/config.json" >/dev/null || { echo "STOP: config.json is not valid JSON."; exit 1; }
"$PYBIN" -I -c "import ast, sys; ast.parse(open(sys.argv[1]).read())" "$STAGE/conductor-gate.py" || { echo "STOP: the gate does not parse."; exit 1; }

# Do not merge with managed settings from someone else.
UPGRADE=0
if [ -e "$CC/managed-settings.json" ]; then
  if "$PYBIN" -I - "$CC/managed-settings.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = list(d.keys()) == ["hooks"] and list(d["hooks"].keys()) == ["PreToolUse"]
if ok:
    cmds = [h.get("command", "") for e in d["hooks"]["PreToolUse"] for h in e.get("hooks", [])]
    ok = len(cmds) == 1 and "conductor-gate.py" in cmds[0]
sys.exit(0 if ok else 1)
PY
  then UPGRADE=1; echo "A locked gate exists. This run replaces it and keeps a backup."
  else echo "STOP: $CC/managed-settings.json holds other settings. This script does not merge."; exit 1
  fi
fi

echo "Source: $SRC"
echo "---- config.json ----"; cat "$STAGE/config.json"
echo "---- gatectl (runs as root when you use sudo gatectl) ----"; cat "$STAGE/lock/gatectl"
echo "---- uninstall.sh (runs as root from gatectl uninstall) ----"; cat "$STAGE/lock/uninstall.sh"
echo "---- hook command ----"; "$PYBIN" -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["hooks"]["PreToolUse"][0]["hooks"][0]["command"])' "$STAGE/managed-settings.json"
echo "---- sha256 of each file that this script copies to $ROOT ----"
echo "(gatectl and uninstall.sh: after the Python path is written in. The gate and config.json"
echo "are unchanged, so you can compare them with the files in your clone.)"
SUMS="$(cd "$STAGE" && shasum -a 256 $FILES managed-settings.json)"
echo "$SUMS"
printf 'Install these exact files? (y/N) '
read -r ANSWER </dev/tty
if [ "$ANSWER" != y ]; then echo "Cancelled. Nothing changed."; exit 1; fi
AFTER="$(cd "$STAGE" && shasum -a 256 $FILES managed-settings.json)"
if [ "$SUMS" != "$AFTER" ]; then echo "STOP: a file changed after you read it."; exit 1; fi

if [ "$UPGRADE" = 1 ]; then
  mkdir -p "$ROOT/backups/$STAMP"
  for f in conductor-gate.py config.json gatectl uninstall.sh; do
    if [ -f "$ROOT/$f" ] && [ ! -L "$ROOT/$f" ]; then cp -p "$ROOT/$f" "$ROOT/backups/$STAMP/$f"; fi
  done
  cp -p "$CC/managed-settings.json" "$ROOT/backups/$STAMP/managed-settings.json"
fi
mkdir -p "$ROOT/backups"
install -m 755 -o root -g "$GROUP" "$STAGE/conductor-gate.py" "$ROOT/conductor-gate.py"
install -m 644 -o root -g "$GROUP" "$STAGE/config.json" "$ROOT/config.json"
install -m 755 -o root -g "$GROUP" "$STAGE/lock/gatectl" "$ROOT/gatectl"
install -m 755 -o root -g "$GROUP" "$STAGE/lock/uninstall.sh" "$ROOT/uninstall.sh"
chown -R root:"$GROUP" "$ROOT"
chmod 755 "$CC" "$ROOT"
find "$ROOT/backups" -type d -exec chmod 700 {} +
find "$ROOT/backups" -type f -exec chmod 600 {} +
# Last step: turn on the managed hook.
install -m 644 -o root -g "$GROUP" "$STAGE/managed-settings.json" "$CC/managed-settings.json"

echo "LOCKED"
echo "Restart your Claude sessions. Then type /conduct status in a new window."
echo "Turn it off: sudo \"$ROOT/gatectl\" off. Remove it: sudo \"$ROOT/gatectl\" uninstall."
sudo -k
