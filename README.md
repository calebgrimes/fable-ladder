# Opus orchestrator

A Claude Code skill that runs a multi-step build as a conductor. Opus 5.5 drafts and judges the plan in a
clean side call, Sonnet 5.5 does most of the building, and Haiku 4.5 takes the loops and bulk mechanical
work. Workers run one at a time by default, and the conductor keeps its state in two small files, so the
window you steer from stays short enough to run for days.

Adapted from [codejunkie99/fable-orchestrator](https://github.com/codejunkie99/fable-orchestrator), which
plans with Claude Fable inside OpenAI Codex and builds with GPT and DeepSeek workers. This version keeps
its best idea (planning in a separate, tool-less Claude call) and moves everything into Claude Code with
Claude models only. It also folds in conductor habits learned from running long multi-stage builds:

- **Linear by default.** One worker, one report, then the next. Parallel only when asked, at most 4.
- **State in files.** `.orchestrate/board.md` and `.orchestrate/handover.md` are the memory, so a fresh
  session resumes with `/orchestrate resume` instead of re-reading history.
- **Short briefs, fixed reports.** A ten-line brief per worker; a 300-word report back in a set order,
  with anything only a human can do returned as one exact `! <command>`.
- **Script the skeleton.** Steps that repeat on every node become a script once; agents spend their
  tokens on the part that changes.
- **One bounded ruling.** Conflicts between nodes get one Opus ruling of at most 150 words, then go to
  the human.

## Roles

| Role | Model | Owns |
|---|---|---|
| Planner and judge | Opus 5.5 (`claude-opus-5-5`) via `scripts/ask_opus.sh` | Task graph, routing, rulings, final verdict |
| Implementer (default) | Sonnet 5.5 (`sonnet`) | Well-specified implementation, which is most of it |
| Throughput worker | Haiku 4.5 (`haiku`) | Loops, renames, boilerplate, conversion, scoring, triage |
| Specialist | Opus 5.5 (`opus`) | Genuinely tricky nodes and fresh-eyes review |

## Layout

```text
skill/orchestrate/
  SKILL.md
  scripts/ask_opus.sh
  templates/brief.md
  templates/board.md
install.sh
tests/test_skill.sh
```

## Install

```bash
./install.sh --dry-run
./install.sh --copy
```

Installs to `~/.claude/skills/orchestrate` (or `--target DIR`). Safe to re-run. It never reads or writes
credentials; the planner helper uses your existing Claude Code login. Open a new Claude Code session
afterwards so the skill is picked up.

## Use

```text
/orchestrate build the export feature
/orchestrate path/to/plan.md
/orchestrate migrate these 40 files; parallel
/orchestrate resume
```

Planner overrides: `ORCH_MODEL` (default `claude-opus-5-5`, falls back to `opus`), `ORCH_EFFORT`
(default `medium`).

## Test

```bash
tests/test_skill.sh
```

Checks shell syntax, frontmatter, routing strings, the absence of non-Claude model names, house style
(no em dashes), credential-shaped strings, a dry run that writes nothing, an idempotent copy, and that an
empty packet is refused. Needs no live Claude login.

## License

MIT. See [LICENSE](LICENSE).
