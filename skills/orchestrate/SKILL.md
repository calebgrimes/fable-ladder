---
name: orchestrate
description: Run a multi-step build as a conductor on the model ladder. Fable (or whatever the session runs) decides and dispatches, Opus drafts and judges the plan in a clean side call, Sonnet implements by default, Haiku handles loops and bulk mechanical work, one worker at a time, with state kept in files so the conducting window stays small. Use when the user invokes /orchestrate, says "orchestrate this", "delegate this out", "have subagents build it", or hands over a plan with several stages. NOT for single-step tasks you can finish directly.
---

# Orchestrate

The session you are in is the **conductor**. It decides, dispatches and reads
reports. It does not read source files, render, or write code itself; workers
do. The ladder, top to bottom:

| Rung | Model | Agent `model` | Owns |
|---|---|---|---|
| Conductor | Fable 5.1 (the session, when it runs there) | never a subagent | Decides, dispatches, reads reports, rules on conflicts, signs off. Spends nothing on drafting. |
| Planner and judge | Opus 5.5 (`claude-opus-5-5`) | via `scripts/ask_opus.sh` | Drafts the task graph in a clean side call; one bounded ruling when nodes conflict. |
| Implementer (default) | Sonnet 5.5 | `sonnet` | All well-specified implementation. Deliberately wide lane: Sonnet 5.5 can carry most of a build. |
| Throughput worker | Haiku 4.5 | `haiku` | Loops, repeated iteration, renames, boilerplate, format conversion, scoring, log triage, other high-volume mechanical work. |
| Specialist | Opus 5.5 | `opus` | Only genuinely tricky nodes: design decisions, concurrency, subtle algorithms, hard debugging, fresh-eyes review. |

Whatever model the session runs (Fable, Opus, Sonnet), it stays the conductor.
Every Agent call sets `model` explicitly; omitting it silently inherits the
session model. Never route a worker to Fable. When unsure between tiers, pick
the cheaper one and escalate on failure.

## Invocation

Everything after `/orchestrate` is the objective, or a path to a plan file:

- `/orchestrate build the export feature`
- `/orchestrate ~/plans/line-3.md`
- `/orchestrate debug the failing sync; implementer: opus`
- `/orchestrate migrate these 40 files; parallel`
- `/orchestrate resume`

An explicit route wins only when it names `sonnet`, `haiku` or `opus`. Without
one, classify each node: loops and bulk mechanical work go to `haiku`;
implementation with clear acceptance criteria goes to `sonnet`; genuinely
tricky work goes to `opus`; research and review take the cheapest tier that fits.

## Four standing rules

1. **Linear by default.** One worker at a time: launch, wait for its completion
   notice, read its report, launch the next. Parallel only when the objective
   says so, capped at 4 at once. Never invoke the Workflow tool on your own.
   Workers may spawn their own helpers under the same model rules.
2. **State lives in files, not in the window.** The board and handover below
   are the memory. A fresh session resumes from them, never from a compaction
   summary or by re-reading old logs.
3. **Script the skeleton, delegate the judgment.** When the same setup, check
   or landing steps recur across nodes, the first worker that meets them
   writes a small script, and every later brief says "run X" instead of
   explaining it. Agent tokens go to the part that changes each time.
4. **Orchestration never widens permission.** Publishing, deploying, deleting,
   spending and messaging people keep their normal approval gates. A step a
   worker is denied comes back as one exact command for the user (see report
   format) and stops there. Never look for a route around a denial.

## Workflow

1. **Orient.** Read the objective and local instructions (CLAUDE.md, rules,
   memory). If `.orchestrate/handover.md` exists in the project, read it first
   and resume from it.
2. **Plan.** Build a compact packet: objective, acceptance criteria, workspace
   facts, protected files, constraints, the worker menu, dispatch mode. Pipe it
   to `scripts/ask_opus.sh`. Show the reply verbatim under `Opus 5.5 speaks:`.
   Validate the graph against the real task and tools; reject invented models,
   unsafe steps, or work outside the request.
3. **Open the board.** Write `.orchestrate/board.md` in the project (template
   in `templates/board.md`): one row per node with column (To do, Doing,
   Checking, Needs you, Done), model, agent id, note.
4. **Dispatch.** Fill `templates/brief.md` for the next ready node: ten lines
   or so, pointing at the plan and acceptance lines rather than restating
   them. Launch it. Record the agent id on its board row.
5. **Read the report** (format below). Update the board. Run proportionate
   verification yourself only when it is one command; otherwise make the
   check its own node.
6. **Adjudicate when needed.** For a conflict between two nodes, or a
   judgment the graph did not settle, send a short results packet back
   through `ask_opus.sh`. One bounded round, ruling of at most 150 words. If
   the ruling does not hold, it becomes a Needs you row. Cap planner calls at
   three per objective unless the user asks for more.
7. **Hand over before the window fills.** When the conversation is getting
   long, write `.orchestrate/handover.md` (under 120 lines: Needs you first,
   the board, agents in flight, next action per open node), tell the user to
   start a fresh session with `/orchestrate resume`, and stop dispatching.
8. **Finish** only when acceptance criteria and verification pass. Report the
   models used, what changed, concrete proof, and every Needs you row.

## Worker report format

Every brief requires this, at most 300 words, so the conductor reads it once:

```text
NODE <id> DONE | PARTIAL | BLOCKED
Built: <what changed, file paths, commit if any>
Check: <command run, pass/fail counts, failing lines named>
Needs you: <one exact `! <command>` per step only the user can run, what it changes; or "nothing">
Cost: <tokens, tool uses, minutes>
Left: <what is undone and what the next node must know>
```

## Calling the planner

The helper sits beside this file. Installed as part of the `ladder` plugin it
is `${CLAUDE_PLUGIN_ROOT}/skills/orchestrate/scripts/ask_opus.sh`; copied into
a skills folder it is `scripts/ask_opus.sh` under that folder.

```bash
printf '%s' "$PACKET" | "<that path>/scripts/ask_opus.sh"
```

The helper calls Claude Code headless with no tools and no saved session, so
the plan costs the conducting window only its answer. Overrides: `ORCH_MODEL`
(default `claude-opus-5-5`, falls back to the `opus` alias), `ORCH_EFFORT`
(default `medium`). Never put secrets in a packet. If the session is itself
Opus and the task is small, planning in-session is fine; say which you did.
