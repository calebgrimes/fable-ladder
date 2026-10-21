---
name: direct
description: "The user types /direct <task> to let the main window do this one task itself, hands-on, for this turn only, while the conductor gate is on. It is not for workers or sub-orchestrators."
argument-hint: "<task>"
disable-model-invocation: true
---

# /direct: do this task yourself, this turn only

The user allowed direct, hands-on work for this turn only.
The conductor gate lets the main window act until the next message from the user.

Do the task in $ARGUMENTS yourself. Do not dispatch it to a worker.
If a worker already started on this job, check its report or stop it (TaskStop) first, so the job is not done twice.
If $ARGUMENTS is empty, ask the user what to do.

The money guards still apply. Do not start a Fable worker, a Fable fork, or a Workflow with fable unless the user approves it.
Set model on every Agent call, as usual.

The permission ends at the next message. A task notification or a sub-agent report also ends it.
If the gate blocks a call, the direct turn has ended. Tell the user, and dispatch the rest.
