# Changelog

## 0.5.1

Added:
- Memory notes: the conductor and the sub-orchestrator may Write and Edit Claude's memory notes
  (`~/.claude/projects/<project>/memory/<name>.md`, also `MEMORY.md`) in every mode, for any project.
  The gate logs ALLOW-MEMORY. A symlink, a `..` part, a subfolder under `memory/`, a file that is not
  `.md`, a hard link, a path in other letter case, and a path in a gate folder do not get this rule.
  Bash writes keep the normal rules.

Changed:
- Ask once, then delegate: when the gate blocks a hands-on call of the main window, the message
  tells Claude to ask the user in one line ("Reply /direct and I'll do it myself.") if the job is
  small, and then to dispatch a worker at once without waiting for the answer. There is no timeout
  and no auto-allow. The `/orchestrate` skill and the sub-orchestrator agent say the same.
- The deny message also says: never use a worker to change the gate, its config, or its OFF file.
  If the user replies `/direct` after a worker started, check or stop that worker first, so the job
  is not done twice. The `/direct` skill says the same.
- Known limits: the gate does not judge memory note content. Check the ALLOW-MEMORY rows in the log.
- The lock steps use the tag `v0.5.1`.

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
