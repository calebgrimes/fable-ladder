# Fable ladder

A Claude Code plugin that turns a session into an orchestrator. Fable or Opus conducts: it decides and
dispatches. Opus plans in a clean side call, Sonnet builds, Haiku does the bulk mechanical work, one
worker at a time, with the board and handover kept in files so the conducting window stays small
enough to run for days. The plugin never switches your session's model; you choose that.

Version 0.5.0 added the **conductor gate**: a PreToolUse hook that checks each tool call of the main
window. In a project you mark for it, the main window must dispatch work to sub-agents instead of doing
it hands-on. In every window it keeps two money guards on.

Tier choice is a forecast corrected by evidence. Before dispatch the conductor scores each node and
starts at the cheapest tier the score allows. A worker that stalled, failed its check or claimed work
it did not show is re-dispatched one tier up with its report attached; one that passed easily marks
that kind of work as cheaper for next time.

Adapted from [codejunkie99/fable-orchestrator](https://github.com/codejunkie99/fable-orchestrator),
which plans with Claude Fable inside OpenAI Codex and builds with GPT and DeepSeek workers. This
version keeps its best idea (planning in a separate, tool-less Claude call) and moves everything into
Claude Code with Claude models only.

## What it ships

| Piece | What it does |
|---|---|
| `/orchestrate` | The conductor skill for multi-stage builds. Opus drafts and judges the plan, Sonnet implements by default, Haiku handles loops and bulk work, state lives in files. |
| Delegation policy | A short section added to every system prompt, in every session: delegate with an explicit `model` on every Agent call, the lowest tier that will do the node well; scale up or down on the worker's report; a session on Fable or Opus conducts rather than builds. |
| Self-updater | Every 21 days (a `/config` row) the plugin compares its installed version with the one on this repository's `main` branch, twenty seconds into a session and never on the prompt's path. The default is **notify**; set **apply** to have it pulled in (a git checkout fast-forwards and refuses rather than overwrite local edits; a marketplace copy runs `claude plugin update`). The running session keeps the old code until `/reload-plugins`. |
| `/ladder` | Reports the installed version and the last update check. `/ladder update` checks now and applies. |
| Conductor gate | `gate/conductor-gate.py`, run before every tool call by `gate/run.sh`. It sets the mode of the window (see below), keeps the money guards on, and logs to `~/.claude/conductor-gate/log.jsonl`. |
| `/conduct on`, `/conduct off`, `/conduct status` | Switch the conductor rule for one window, or check that the gate is active and which mode the window is in. |
| `/direct <task>` | Lets the main window do one task hands-on, for this turn only, while the conductor rule is on. |
| `ladder:suborchestrator` agent | The sub-orchestrator that the gate tells the main window to use. It splits a goal, dispatches workers, verifies, and reports back. |
| Gate marker | A status line entry (`◆ CONDUCTOR`, `◇ DIRECT`, or `○ GATE ?`) and one button above the prompt that fills `/ladder:conduct on` or `/ladder:conduct off`. |
| `gate/lock/` | Optional. Install a root-owned copy of the gate that a Claude session cannot turn off. |

## Requirements

| Need | Check it | If it is missing |
|---|---|---|
| Claude Code with plugin support (tested on 2.1.306) | `claude --version` | Update Claude Code. |
| `python3`, version 3.9 or newer, for the gate | `python3 --version` | On macOS run `xcode-select --install` (the Command Line Tools Python is fine). On Linux install `python3` from your package manager. |
| macOS or Linux | | The gate is tested on macOS. Linux is written but not tested. |

Windows: not supported yet.

If `python3` is missing, the gate is off. Every tool call runs, and Claude Code shows one warning per
session: "Conductor gate is OFF: python3 was not found." The ladder skills still work.

## Install

1. In Claude Code, type these two lines:

   ```text
   /plugin marketplace add calebgrimes/fable-ladder
   /plugin install ladder@fable-ladder
   ```

   Choose the user scope when Claude Code asks.
2. Quit Claude Code and start it again. The hooks load at the start of a session.
3. Check the gate in the new window:

   ```text
   /conduct status
   ```

   The answer starts with `✔ ACTIVE` and names the mode (`◇ DIRECT (guards)` in a new folder). If it
   says `✖ NOT SEEN`, check `/plugin` (the plugin is on) and `python3 --version`.

The same two steps from a shell: `claude plugin marketplace add calebgrimes/fable-ladder`, then
`claude plugin install ladder@fable-ladder --scope user`.

From a clone, the folder itself is the marketplace, so edits apply with `/reload-plugins`:

```bash
claude plugin marketplace add /path/to/fable-ladder
claude plugin install ladder@fable-ladder --scope user
```

## Use

For a build:

```text
/orchestrate build the export feature
/orchestrate path/to/plan.md
/orchestrate migrate these 40 files; parallel
/orchestrate resume
```

Each worker's tier is a forecast, then corrected by evidence. Before dispatch the conductor scores the
node on five questions (how much is left to judgment, reasoning depth, blast radius, context, novelty)
and starts at the cheapest tier the score allows. Every report ends with the worker's own `Tier:` and
`Confidence:` lines. A worker that stalled on understanding, failed its check or claimed work it did
not show is re-dispatched one rung up with its report attached, at most twice, never to Fable. A worker
that passed easily marks that kind of work as cheaper, and the next one starts a rung lower.
`.orchestrate/tier-log.md` keeps the outcomes, so the next build in the same project starts smarter.
The always-on delegation policy carries a one-line version of the same rule into every session.

Planner overrides: `ORCH_MODEL` (default `claude-opus-5-5`, falls back to `opus`), `ORCH_EFFORT`
(default `medium`).

## The conductor gate

### Modes

| Mode | Marker | What the main window can do |
|---|---|---|
| guards (the default) | `◇ DIRECT` | Hands-on work. Only the money guards and the gate's own files are checked. |
| enforce | `◆ CONDUCTOR` | Coordination only: dispatch with the Agent tool, read and write notes in `~/.claude/plans`, the project memory folder and `{cwd}/.orchestrate`, write Claude's memory notes, run `date`, and a short list of safe commands. Hands-on calls are blocked with a message that tells Claude to ask once, then delegate (see below). |
| warn | `○ GATE warn` | Like enforce, but the gate only warns. |
| log | `○ GATE log` | Headless runs (`claude -p`). The gate only logs what it would block. An explicit Fable model is still blocked. |
| off | `○ GATE off` | The gate allows every call in that folder. |

How the gate picks the mode of a window, first match wins:

1. The OFF file exists: the gate is off.
2. The last `/conduct on` or `/conduct off` line that you typed in this session.
3. The folder is in `enforce_projects` (or in `projects`) of your config: enforce.
4. A headless run: log.
5. `default_mode` (guards).

A sub-orchestrator (`ladder:suborchestrator`) is always enforce, whatever the window mode. `/conduct`
and guards do not change it. Workers (any other sub-agent) are not checked.

### Ask once, then delegate

When the gate blocks a hands-on call in conductor mode (enforce, or after `/conduct on`), the message
tells Claude what to do:

1. If the job is small, ask you in one line: "Reply /direct and I'll do it myself."
2. Dispatch a worker at once with the Agent tool. Do not wait for your answer.

If you reply `/direct`, the main window does the next task itself. If a worker already started on
that job, Claude checks or stops the worker first, so the job is not done twice. There is no timeout,
and the gate never allows the call by itself. The message also says: never use a worker to change the
gate, its config, or its OFF file.

### Memory notes

The conductor and the sub-orchestrator may write and edit Claude's memory notes in every mode, for any
project: `~/.claude/projects/<project>/memory/<name>.md`, including `MEMORY.md`. The gate logs these
calls as `ALLOW-MEMORY`. The rule does not apply to a path with a symlink or a `..` part, a subfolder
under `memory/`, a file that is not `.md`, a path in a gate folder, or a Bash command. These get the
normal rules.

### /conduct and /direct

- `/conduct on` turns the conductor rule on for this window. `/conduct off` turns it off. The switch
  lasts for the session and survives a compaction. `/clear` starts a new session, so the folder default
  applies again. If a plugin of yours has another `conduct` skill, type `/ladder:conduct on`.
- `/conduct status` shows `✔ ACTIVE` or `✖ NOT SEEN`, the mode, and how many calls the gate blocked.
- `/direct fix the typo in notes.md` lets the main window do that one task itself. The permission ends
  at your next message, or at a task report. It works only in an interactive window.
- If you type `/conduct` while a turn runs, it arrives as plain text and does not count. Type it again
  after the turn ends.

### Money guards (on in every mode)

- An Agent call with no `model` gets `sonnet`. The gate fills it in, so no worker runs on an expensive
  session model by accident. The gate sets a model only when the Agent call has none. But it also
  overrides the model in an agent's own definition file: an agent file that says `model: haiku` runs on
  sonnet, unless the Agent call itself sets `model`. The filled-in call is allowed at once, so an "ask"
  permission rule for Agent does not prompt for it.
- A Fable worker, a Fable fork, or a Workflow that names fable needs your approval each time (the gate
  asks). A headless run has no one to ask, so the gate denies it.
- A Bash command that selects a fable model (`--model fable`, `claude-fable-5`, `ANTHROPIC_MODEL=...`)
  also needs your approval.

### Your config

The plugin ships `gate/config.json`. To change it, create `~/.claude/conductor-gate/config.json`. Each
key you set replaces the shipped value. `projects` entries are added to the shipped ones. Example:

```json
{
  "enforce_projects": ["/path/to/your/project"],
  "default_mode": "guards"
}
```

A folder in `enforce_projects`, and every folder under it, starts in enforce.

The gate checks your file on each tool call. If the file is not valid JSON, the gate ignores the whole
file. If a key has an unknown name, the wrong type, an unknown mode, or a regex that does not compile,
the gate ignores that key and uses the shipped value. In both cases it writes one `USER-CONFIG-ERR` row
per tool call to the log, with each problem. The money guards stay on.

### Log

The gate writes one JSON row per decision to `~/.claude/conductor-gate/log.jsonl` (file mode 600). It
never follows a symlink. When the log is larger than 5 MB, the gate moves it to `log.jsonl.1` and
starts a new one, so the log never takes more than about 10 MB. The gate also keeps a small bookmark per
session in `~/.claude/conductor-gate/sessions/`.

### Turn it off

- This window: type `/conduct off`. The window goes to guards, so the money guards stay on.
- All windows: in a Terminal, run
  `mkdir -p ~/.claude/conductor-gate && touch ~/.claude/conductor-gate/OFF`.
  Delete the file to turn the gate on again.
- The plugin: turn `ladder` off in `/plugin`, or remove it (see Uninstall).

In every mode, the gate stops Claude from changing the gate's own files: `~/.claude/conductor-gate`, the
plugin `gate/` folder, the plugin `hooks/` folder (for a copy installed under `~/.claude/plugins`), and
the managed settings folder. For a Bash command this is a text check: it finds the full path, the `~/`
and `$HOME/` forms, and a command in the plugin folder that names `gate` or `hooks`. Some commands can
still pass (see Known limits). You can always change these files yourself in a Terminal.

## Lock it (optional)

The plugin gate is a speed bump, not a lock: a hands-on window could, in principle, edit the plugin
files. If you want a gate that no Claude session can turn off, install a root-owned copy. It runs from
the managed settings of Claude Code, which a user process cannot change.

Do these steps yourself, in a plain Terminal window, not in Claude Code. The script asks you on the
terminal before it changes anything.

1. Make a fresh clone of the release tag in your home folder:

   ```bash
   git clone --depth 1 --branch v0.5.1 https://github.com/calebgrimes/fable-ladder ~/fable-ladder-lock
   ```

   Do not use the plugin cache in `~/.claude/plugins`. A Claude session can edit the cache, and the
   files you install here run as root later. The script stops if you run it from the cache.
2. Read `~/fable-ladder-lock/gate/lock/install.sh`. It runs as root.
3. Run it:

   ```bash
   sudo bash ~/fable-ladder-lock/gate/lock/install.sh
   ```

4. Read what it shows: the config, the full text of `gatectl` and `uninstall.sh` (both run as root
   later), the hook command, and the sha256 of each file that it copies. Type `y` to install.
5. Quit and start Claude Code again. Type `/conduct status` in a new window.
6. You can delete `~/fable-ladder-lock` now. The locked copy does not use it.

What the script does:

- It needs Python 3.9 or newer, owned by root. It uses the Command Line Tools Python 3.9 if it exists,
  else `python3` on your PATH. A Python that you own (for example from Homebrew) is refused, because a
  user process could replace it.
- It copies the gate files once into a root-owned temp folder (`/tmp/gate-lock.XXXXXX`), refuses
  symlinks, and installs only from that copy. It stops if its folder is in the plugin cache.
- It installs the gate to `/Library/Application Support/ClaudeCode/conductor-gate/` and writes
  `managed-settings.json` with one hook: `/usr/bin/env -i <python> -I ".../conductor-gate.py"`.
- If a locked gate exists, it replaces it and keeps the old files in `conductor-gate/backups/`.
- It stops if `managed-settings.json` holds other settings. It does not merge.

When the lock is on:

- The plugin gate sees the managed hook and exits at once, so each call is checked only once. It does
  this only if root owns the managed settings file and no group or other user can write it.
- The locked gate reads its own root config. From your user config it reads only `enforce_projects`.
  The user OFF file does not count.
- A plugin update does not change the locked copy. Run `install.sh` again from a fresh clone of the new
  tag to update it.
- The locked hook runs with an empty environment (`env -i`). So the locked gate always uses
  `~/.claude`, even if you set `CLAUDE_CONFIG_DIR`. Then the `enforce_projects` in your custom folder do
  not count, the log goes to `~/.claude/conductor-gate/`, and the marker shows `○ GATE ?`.

Control it with `gatectl`, in a Terminal:

```bash
G="/Library/Application Support/ClaudeCode/conductor-gate/gatectl"
sudo "$G" status
sudo "$G" off
sudo "$G" on
sudo "$G" propose ~/new-config.json   # shows a diff, asks, then installs it
sudo "$G" uninstall
```

On Linux the folder is `/etc/claude-code`. This path is written but not tested.

## Uninstall

Do these steps in order:

1. If you installed the lock, remove it first, in a Terminal:

   ```bash
   sudo "/Library/Application Support/ClaudeCode/conductor-gate/gatectl" uninstall
   ```

   On Linux the file is `/etc/claude-code/conductor-gate/gatectl`.
2. Remove the plugin: `/plugin uninstall ladder@fable-ladder` in Claude Code, or
   `claude plugin uninstall ladder@fable-ladder` in a shell.
3. Remove the marketplace: `/plugin marketplace remove fable-ladder`, or
   `claude plugin marketplace remove fable-ladder`.
4. Remove the gate log, the session bookmarks, your gate config and the OFF file, in a Terminal:

   ```bash
   rm -rf ~/.claude/conductor-gate
   ```

## Known limits

- A window can write memory notes that later sessions of every project load. The gate does not judge
  memory note content. Check the ALLOW-MEMORY rows in the log.
- The gate reads the session transcript, which is a file that you own. A worker can append a forged
  `/conduct` or `/direct` line to it. The gate checks every field of the line, but it cannot prove who
  wrote the file. A forged `/direct` line opens one turn. A forged `/conduct off` line opens
  the whole session, survives a compaction, and beats the folder default.
- A forged state file in `~/.claude/conductor-gate/sessions/` alone changes nothing: the gate needs a
  genuine `/conduct` line at the bookmark. But a forged state file can point at an older genuine line
  when the newest one is more than 8 MB back. A worker that can do this can also append a forged line.
- An older bookmark can beat a newer `/conduct` line. Example: `/conduct on`, then the gate is off for a
  while, then `/conduct off` (the gate does not see it), then more than 8 MB of work, then the gate is
  on again. The scan stops at the cap, and the older bookmark (on) still counts.
- The fable check reads text. A Workflow script or a command can hide a fable model from it, for
  example with quotes or string building. The check catches honest slips, not a deliberate trick.
- The fork rule reads the model tier from the transcript. The first tool call of a session can come
  before the transcript names a model. Then the tier is unknown and a fork gets ask. In enforce,
  a fork by a sub-orchestrator is denied (its tier is unknown).
- Headless runs (mode log): an Agent call with no model gets no sonnet fill-in. The gate only logs
  WOULD-FILL. An explicit fable model is still blocked.
- Bash and Monitor commands get a text match for the gate's own paths. The gate first makes the path
  text simple (`//`, `/./`, `x/../`, quotes, backslashes). This is best effort. Examples that pass:
  - A write built in code from another folder: `cd ~/.claude && python3 -c "import os; os.remove('conductor'+'-gate/x')"`.
  - A shell variable that holds the path.
  - A script run with a gate file as an argument: `python3 /tmp/w.py ~/.claude/conductor-gate/config.json`.
    The gate allows a script run and does not read the script.
- The path walk: the file tools check the real path. Every other tool gets the path walk over its input.
  A string counts as a path if its key has a path word (path, file, dir, dest, output, target) or if it
  starts with `/` or `~`. Limits:
  - The gate expands only `~/`. A `~user/...` value, a `$HOME/...` value, and a `file://` URI are not
    seen as gate paths.
  - A value under a free-text key (prompt, command, description, message and others) is not a path.
  - Past 200 strings or depth 6, the gate writes a PATH-WALK-CAP row and also matches the whole input
    as text. Past the hard cap (2000 strings or depth 32), the rest is not checked.
- In guards and warn, the gate closes only its own paths. Settings files and other folders stay open,
  because guards is normal hands-on work.
- The plugin folder check is a text check. In guards and warn these still pass (enforce blocks them):
  - A command that names a parent folder, for example `rm -rf ~/.claude/plugins/cache/fable-ladder`.
  - A path in a shell variable, or a path built in steps (`cd ~/.claude/plugins/cache && rm -rf fable-ladder/ladder/0.5.0/gate`).
  - `claude plugin disable ladder` or `claude plugin uninstall ladder@fable-ladder` run from Bash.
  - For a clone install (outside `~/.claude/plugins`), only the `gate/` folder is closed, by its path.
    The `hooks/` folder and a relative name such as `rm -rf gate` stay open.
  The plugin gate is a speed bump. For a gate that Claude cannot remove, use the lock.
- Contributors: with a clone install, the gate closes the clone's `gate/` folder to the main window in
  every mode. Edit the gate in another worktree or clone, or do it yourself in a Terminal.
- `/conduct on`, `/conduct off` and `/conduct status` each run one Bash command. In the default
  permission mode, Claude Code asks you to approve it.
- No `python3`, and `~/.claude/conductor-gate` cannot be written: the warning shows on every call, not
  once per session.
- With the lock on, a clone install outside `~/.claude/plugins` does not match the allow patterns of the
  locked gate. Then, in enforce, `/conduct status` and the packet form of `ask_opus.sh` are blocked.

## Layout

```text
.claude-plugin/plugin.json        the plugin and its two /config rows (updates, updateEveryDays)
.claude-plugin/marketplace.json   makes this repository installable
hooks/hooks.json                  the hooks module and the gate's PreToolUse command hook
hooks/ladder.mjs                  the delegation policy, /ladder, and the self-updater
hooks/gate-status.mjs             the gate marker in the status line and its button
types/index.d.ts                  the update record and the gate marker state
gate/run.sh, gate/conductor-gate.py, gate/config.json   the conductor gate
gate/lock/                        install.sh, uninstall.sh, gatectl for the optional root lock
agents/suborchestrator.md         the sub-orchestrator agent
skills/orchestrate/               SKILL.md, scripts/ask_opus.sh, templates/
skills/conduct/, skills/direct/   the /conduct and /direct skills
tests/*.test.ts                   run by `claude plugin test`
tests/test-gate.sh                the gate suite (synthetic fixtures, a temp HOME)
tests/test_skill.sh               layout, routing strings, house style, the gate suite, validate and test
```

## Test

```bash
tests/test_skill.sh          # everything, including the gate suite and the engine's validate and test
tests/test_skill.sh --quick  # skips `claude plugin test`
bash tests/test-gate.sh      # only the gate suite
```

The gate suite builds every transcript fixture in the test. It uses a temp HOME and never touches
`~/.claude` or an installed gate. To test another copy of the gate:
`GATE_PY=/path/conductor-gate.py GATE_CONFIG=/path/config.json bash tests/test-gate.sh`.
`GATE_PYTHON=/path/python3` picks the Python.

## License

MIT. See [LICENSE](LICENSE).
