# Fable ladder

A Claude Code plugin that keeps your best model for the work that needs it. Fable sits at the top of a
ladder: Fable, Opus, Sonnet, Haiku. Every prompt you type is triaged by one cheap Haiku call, and when
the session's current model is the wrong rung, the plugin moves the session with the same `/model` you
would type and resubmits your prompt in your own words. Routine building drops to Sonnet; a judgment
call climbs back to Fable; you are told each time, and the status line shows the rung.

It also ships `/orchestrate`, a conductor skill for multi-stage builds: Opus drafts the plan in a clean
side call, Sonnet builds, Haiku runs the bulk work, one worker at a time, with the board and handover
kept in files so the conducting window stays small enough to run for days.

Adapted from [codejunkie99/fable-orchestrator](https://github.com/codejunkie99/fable-orchestrator),
which plans with Claude Fable inside OpenAI Codex and builds with GPT and DeepSeek workers. This
version keeps its best idea (planning in a separate, tool-less Claude call), moves everything into
Claude Code with Claude models only, and adds the ladder itself, so the routing runs in every session
rather than only when a skill is invoked.

## What runs in every session

| Piece | What it does |
|---|---|
| Triage on every prompt | A Haiku call labels the prompt `fable`, `opus`, `sonnet` or `haiku` (cheaper when unsure). Prompts under 40 characters, slash commands, attachments and mid-turn prompts pass through untouched. |
| The switch | When the label's rung differs from the session's, the prompt is held, `/model <rung>` runs, and the prompt is resubmitted as your own words. Nothing is lost: any failure lets the prompt through on the current model, and a failed resubmission puts the text back in the box. |
| Your own `/model` wins | A model you chose by hand is held for 3 prompts before triage resumes. `/ladder pin` holds it for the session; `/ladder auto` resumes; `/ladder off` stops triage; `/ladder` reports. |
| The policy | A short section is added to every system prompt: delegate with an explicit `model`, lowest rung that will do the node well; a session on Fable or Opus conducts rather than builds. |
| Status | `ladder: fable · auto` in the status line; a toast on every move. Words, not colour, carry the signal. |

Defaults, all in `/config`: top rung `fable`, bottom rung `sonnet` (Haiku remains the bulk *worker*
tier; set the floor to `haiku` to let the session itself drop that far), switching back up on, 3-prompt
hold after a manual `/model`, at least 1 prompt between moves.

One cost to know: a move re-reads the whole context on the new model once (the prompt cache does not
carry across models). Moving down costs Sonnet tokens, which is the point. Moving up costs the top model
one context read; the toast says how much, and `upSwitch` turns that direction off.

## Updates

Every 21 days (a `/config` row) the plugin compares its installed version with the one on this
repository's `main` branch, twenty seconds into a session and never on the prompt's path. The
default is **notify**: a notice says a newer version is out and `/ladder update` applies it. Set
**apply** to have it pulled in on its own: a git checkout fast-forwards (local edits make it refuse,
not overwrite); a marketplace copy runs `claude plugin update`. Either way the running session keeps
the old code until `/reload-plugins`, and the notice says so. `/ladder` shows the last check.

## Install

In a terminal session of Claude Code:

```text
/plugin install ladder --marketplace calebgrimes/fable-ladder
```

Answer `y` to add the marketplace and take the user scope. The hooks run from then on and in every
later session. From a clone, the folder itself is the marketplace, so edits apply with `/reload-plugins`:

```bash
claude plugin marketplace add /path/to/fable-ladder
claude plugin install ladder@fable-ladder --scope user
```

## Use

Nothing, for the ladder: type as usual. For a build:

```text
/orchestrate build the export feature
/orchestrate path/to/plan.md
/orchestrate migrate these 40 files; parallel
/orchestrate resume
```

Planner overrides: `ORCH_MODEL` (default `claude-opus-5-5`, falls back to `opus`), `ORCH_EFFORT`
(default `medium`).

## Layout

```text
.claude-plugin/plugin.json        the plugin and its /config rows
.claude-plugin/marketplace.json   makes this repository installable
hooks/hooks.json, hooks/ladder.mjs   the ladder and its self-updater (one file: the engine follows $ only within a file)
types/index.d.ts                  the state contract
skills/orchestrate/               SKILL.md, scripts/ask_opus.sh, templates/
tests/ladder.test.ts, tests/updater.test.ts   run by `claude plugin test`
tests/test_skill.sh               layout, routing strings, house style, then validate and test
```

## Test

```bash
tests/test_skill.sh          # everything, including the engine's validate and test
tests/test_skill.sh --quick  # skips `claude plugin test`
```

## License

MIT. See [LICENSE](LICENSE).
