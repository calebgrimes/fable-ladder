"""Show whether the conductor gate is active, and the mode of a session.

Usage: python3 gate_status.py [session_id]

The script reads the gate log (~/.claude/conductor-gate/log.jsonl). It changes nothing.
The gate writes a MODE row when the mode of a session changes, so a session that
made at least one tool call under the gate has a MODE row.
"""
import json
import os
import sys

LABELS = {
    "enforce": "◆ CONDUCTOR (enforce): the main window dispatches work to sub-agents",
    "warn": "◈ WARN: the gate only warns",
    "guards": "◇ DIRECT (guards): the main window works hands-on; money guards are on",
    "log": "○ LOG: headless run, the gate only logs",
    "off": "○ OFF: the gate allows every call in this folder",
}


def claude_dir():
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    if d and os.path.isabs(d):
        return d
    return os.path.join(os.path.expanduser("~"), ".claude")


def tail_rows(path, limit=2 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read()
    except OSError:
        return []
    rows = []
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main():
    sid = sys.argv[1] if len(sys.argv) > 1 else ""
    gdir = os.path.join(claude_dir(), "conductor-gate")
    log = os.path.join(gdir, "log.jsonl")
    if os.path.exists(os.path.join(gdir, "OFF")):
        print("○ GATE OFF: the file %s exists. Delete it to turn the gate on." % os.path.join(gdir, "OFF"))
        return 0
    rows = tail_rows(log)
    if sid:
        mine = [r for r in rows if r.get("session_id") == sid]
    else:
        mine = rows
    modes = [r for r in mine if r.get("decision") == "MODE"]
    if not modes:
        if sid:
            print("✖ NOT SEEN: the gate has no row for this session in %s." % log)
            print("Check that the ladder plugin is on (/plugin), and that python3 works (python3 --version).")
        else:
            print("✖ NOT SEEN: the gate log %s has no MODE row." % log)
        return 1
    last = modes[-1]
    mode = last.get("mode") or "?"
    print("✔ ACTIVE: the gate checks the tool calls of %s." % ("this session" if sid else "the last session"))
    print("Mode: " + LABELS.get(mode, mode))
    print("Set by: " + str(last.get("detail", "")).replace("mode set by: ", ""))
    denies = [r for r in mine if r.get("decision") in ("DENY", "WOULD-DENY")]
    print("Blocked calls in this log tail: %d" % len(denies))
    return 0


if __name__ == "__main__":
    sys.exit(main())
