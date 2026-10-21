---
name: conduct
description: "The user types /conduct on or /conduct off to switch the conductor rule for this window, or /conduct status to check the gate. On: the main window dispatches work to sub-agents. Off: the main window works hands-on. It is not for workers or sub-orchestrators."
argument-hint: "on|off|status"
disable-model-invocation: true
---

# /conduct: switch the conductor rule for this window

The user typed `/conduct $ARGUMENTS`.

The gate decides. The conductor gate reads the typed line in the transcript and sets the mode. This skill text changes nothing by itself.

1. Read $ARGUMENTS in any letter case, and ignore spaces before and after it. "ON", "On" and " on " all mean "on". "OFF" and "Off" mean "off".
2. If the argument is "on" or "off", make one tool call now: the Bash command `date`. The gate allows `date` in every mode, and it logs the new mode for the status line at once.
3. Then confirm in one line:
   - If the argument is "on", write: Requested: ◆ CONDUCTOR on. The status line shows the result.
   - If the argument is "off", write: Requested: ◇ DIRECT, conductor off. The status line shows the result.
   - If the argument is not on, off, or status, write: Use /conduct on, /conduct off, or /conduct status. Make no tool call.
4. If the argument is "status", make one tool call: run this exact Bash command, and change nothing in it:
   `python3 -I "${CLAUDE_SKILL_DIR}/scripts/gate_status.py" ${CLAUDE_SESSION_ID}`
   Show its output to the user word for word. "✔ ACTIVE" means the gate checks this window. "✖ NOT SEEN" means it does not.
5. Do nothing else in this turn.

Do not claim that the switch worked. The status marker shows the result after the cheap `date` call. The marker does not change if the gate is off, or in a headless run.

The switch lasts for this window until the next /conduct line. A compaction does not change it.
/clear starts a new session, so the switch resets to the folder default (a folder in enforce_projects: on; other folders: off).
The money guards stay on in both modes: the gate sets model on every Agent call that has none, and every Fable call needs the user's yes.
Sub-orchestrators always dispatch. /conduct does not change them.
If the user types /conduct while a turn runs, it arrives as plain text and does not count. Type it again after the turn ends.
