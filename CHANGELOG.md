# Changelog

## 0.5.0

Added:
- The conductor gate (`gate/conductor-gate.py`). It runs before every tool call as a plugin
  PreToolUse hook (`gate/run.sh`). Modes: guards (the default), enforce, warn, log, off.
- Money guards in every mode: an Agent call with no model gets sonnet, and every Fable call needs
  the user's approval.
- `/conduct on`, `/conduct off`, `/conduct status`, and `/direct <task>`.
- The `ladder:suborchestrator` agent.
- The gate marker: a status line entry and a button that fills `/ladder:conduct on` or `off`.
- A user config at `~/.claude/conductor-gate/config.json` (`enforce_projects`, `default_mode`, and
  every other key), and a user OFF file.
- The optional root lock in `gate/lock/` (macOS tested, Linux written but not tested).
- A double-gate guard: if the managed settings run a locked gate, the plugin gate exits at once.
- If `python3` is missing, or `gate/conductor-gate.py` is missing, the gate allows every call and
  shows one warning per session.
- The gate checks each key of the user config (type, mode name, regex). A bad key is skipped and
  logged, so a bad config never turns the money guards off.
- An installed copy (under `~/.claude/plugins`) also closes its `hooks/` folder, and the gate finds its
  folders in the `~/` and `$HOME/` forms and by a relative name in the plugin folder.
- The double-gate guard trusts only a managed settings file that root owns.
- `install.sh` (the lock) shows `gatectl` and `uninstall.sh` in full before it asks, and stops if it
  runs from the plugin cache. Run it from a fresh clone of the release tag.
- README: Uninstall.
- `tests/test-gate.sh`, the gate suite with synthetic fixtures and a temp HOME.

Changed:
- `/orchestrate`: in a window in enforce mode, send the planner packet as a file
  (`.orchestrate/packet.md`), because the gate blocks the pipe form.

## 0.4.0

- The ladder becomes an orchestrator plugin. It no longer switches the session model.
