# Fable ladder

A Claude Code plugin that turns a session into an orchestrator. Fable or Opus conducts: it decides and
dispatches. Opus plans in a clean side call, Sonnet builds, Haiku does the bulk mechanical work, one
worker at a time, with the board and handover kept in files so the conducting window stays small
enough to run for days. The plugin never switches your session's model; you choose that.

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

## Layout

```text
.claude-plugin/plugin.json        the plugin and its two /config rows (updates, updateEveryDays)
.claude-plugin/marketplace.json   makes this repository installable
hooks/hooks.json, hooks/ladder.mjs   the delegation policy, /ladder, and the self-updater
types/index.d.ts                  the update-record contract
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
