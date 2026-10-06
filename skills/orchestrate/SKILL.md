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
the cheaper one and escalate on failure; the section on choosing and
correcting the tier says how.

## Invocation

Everything after `/orchestrate` is the objective, or a path to a plan file:

- `/orchestrate build the export feature`
- `/orchestrate ~/plans/line-3.md`
- `/orchestrate debug the failing sync; implementer: opus`
- `/orchestrate migrate these 40 files; parallel`
- `/orchestrate resume`

An explicit route wins only when it names `sonnet`, `haiku` or `opus`. Without
one, each node gets a tier from the rubric in "Choosing and correcting the
tier" below, and that tier can move after the worker reports.

## Choosing and correcting the tier

The first pick is a forecast; the report is the evidence. Pick low, read the
evidence, move one rung at a time.

### First pick: score the node

Score each node on five questions, 0, 1 or 2 points each:

| Question | 0 | 1 | 2 |
|---|---|---|---|
| Spec: how much is left to judgment? | exact steps or a pattern to copy | goal and acceptance lines, path open | goal only, or the acceptance lines themselves need deciding |
| Reasoning depth | look up, move, rename, convert | ordinary logic across a few files | concurrency, subtle algorithm, root cause unknown, design trade-off |
| Blast radius if wrong | caught by the node's own check | caught by a later node | silent, outward-facing, or expensive to unwind |
| Context to hold | one file or one record | a module, a handful of files | the whole system, or long cross-file chains |
| Novelty | done before in this project (see the tier log) | familiar kind, new place | nothing like it here yet |

Total 0 to 2 goes to `haiku`, 3 to 6 to `sonnet`, 7 and up to `opus`. Two
overrides: any single 2 on reasoning depth sets the floor at `sonnet`, and
volume work (the same small step many times) goes to `haiku` whatever the
total, with one `sonnet` node to write the pattern first if none exists. Write
the score on the board row (`s4 sonnet`) so the choice can be audited. Then
check the tier log (below): if this kind of node has escalated before in this
project, start it at the tier where it last succeeded.

### Reading the report: was the tier too low?

Every report ends with a `Tier:` line where the worker rates its own fit. Do
not take that alone. Treat the tier as **too low** when any of these show:

- `BLOCKED` or `PARTIAL` for a reason about understanding (could not find the
  cause, unsure which design, ran out of approaches), not about access.
- The check failed, or the worker fixed one failure and caused another.
- The report misses acceptance lines, names files or functions that do not
  exist, or claims a pass with no command shown.
- A test was edited or weakened to pass, or the worker touched files it did
  not own.
- `Tier: too low`, or `Confidence: low` on work with a blast radius of 2.

A failure about access, a missing file, a denied command or a wrong brief is
**not** a tier problem. Fix the brief or raise a Needs you row; do not
escalate for it.

### Scaling up

1. Re-dispatch the same node **one rung up** (`haiku` to `sonnet`, `sonnet`
   to `opus`). Never skip a rung unless the node scored 2 on reasoning depth
   and blast radius both.
2. The new brief carries a `Prior attempt:` line: the failed report's path or
   its Built, Check and Left lines, and one sentence on why it fell short. The
   stronger worker continues from the partial work; it does not start over.
3. At most two escalations per node. When `opus` also falls short, the node
   goes to Needs you with both reports summarised. Never escalate a worker to
   Fable; a Fable review is the user's call, asked for first.
4. One retry at the **same** tier is allowed only when the failure was clearly
   a slip (typo, one missed acceptance line) and the report shows the worker
   understood the job.

### Scaling down

Overpaying is a failure too, just a quieter one. Treat the tier as **higher
than needed** when the check passed first time, the worker rated
`Tier: could go lower`, and the report shows no real decisions made. Do not
re-run that node. Instead mark the kind of work in the tier log, and start the
next node of the same kind one rung lower. If that cheaper node then
escalates, the kind goes back up and stays there for this objective.

### The tier log

`.orchestrate/tier-log.md` (template in `templates/tier-log.md`) is one line
per finished node: kind of work, score, starting tier, final tier, and
`ok`, `up` or `could go lower`. Read it at Orient; it is how the second build
in a project starts smarter than the first. It survives handovers and new
objectives in the same project. Keep it under 80 lines by folding old lines
into a short "Settled" list at the top (for example, "test fixtures:
haiku is enough").

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
   and resume from it. Read `.orchestrate/tier-log.md` if it exists.
2. **Plan.** Build a compact packet: objective, acceptance criteria, workspace
   facts, protected files, constraints, the worker menu, dispatch mode. Pipe it
   to `scripts/ask_opus.sh`. Show the reply verbatim under `Opus 5.5 speaks:`.
   Validate the graph against the real task and tools; reject invented models,
   unsafe steps, or work outside the request.
3. **Open the board.** Write `.orchestrate/board.md` in the project (template
   in `templates/board.md`): one row per node with column (To do, Doing,
   Checking, Needs you, Done), model, agent id, note.
4. **Dispatch.** Score the node and pick its tier (see above). Fill
   `templates/brief.md` for the next ready node: ten lines or so, pointing at
   the plan and acceptance lines rather than restating them. Launch it.
   Record the agent id and the score on its board row.
5. **Read the report** (format below) and judge the tier: too low, right, or
   higher than needed. Escalate or note it as above, update the board's tier
   column (`haiku>sonnet` when it moved) and append a tier log line. Run
   proportionate verification yourself only when it is one command;
   otherwise make the check its own node.
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
Confidence: high | medium | low, and the one thing most likely to be wrong
Tier: right | too low (what was beyond reach) | could go lower (what made it easy)
```

The `Tier:` line is the worker's honest read of its own fit. A worker that
says `too low` is doing its job, not failing it; say so in every brief.

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
