// The gate marker: it shows the conductor gate mode of this window in the
// status line, and one button above the prompt that fills /ladder:conduct on
// or /ladder:conduct off. It only reads the gate log. It never throws.
//
// The gate (gate/conductor-gate.py) writes a row for each call it checks, with
// the mode. This file reads the tail of that log and shows the newest
// conductor row of this session.

import { atom, read, update } from "claude-code";

export const TAIL_BYTES = 262144;
const REFRESH_MS = 5000;

const modeAtom = atom({ plugin: "ladder", key: "gateMode" }, null);

// The gate log: $CLAUDE_CONFIG_DIR/conductor-gate/log.jsonl, else ~/.claude/...
export async function logPath($) {
  try {
    const dir = await $.env.get("CLAUDE_CONFIG_DIR");
    if (typeof dir === "string" && dir.startsWith("/")) return dir + "/conductor-gate/log.jsonl";
    const home = await $.env.get("HOME");
    if (typeof home === "string" && home.startsWith("/")) return home + "/.claude/conductor-gate/log.jsonl";
  } catch {
    // No path: the marker shows "○ GATE ?".
  }
  return null;
}

// The last conductor row of this session in the log text, or null.
// A bad JSON line is skipped. The first line of a tail is often cut, so it is
// skipped the same way.
export function lastConductorRow(text, sessionId) {
  if (typeof text !== "string" || typeof sessionId !== "string" || sessionId === "") return null;
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    if (!line || line.trim() === "") continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    if (typeof row !== "object" || row === null) continue;
    if (row.session_id === sessionId && row.level === "conductor" && typeof row.mode === "string" && row.mode !== "") {
      return { time: typeof row.time === "string" ? row.time : null, mode: row.mode };
    }
  }
  return null;
}

function stamp(t) {
  if (t === null) return Number.NEGATIVE_INFINITY;
  const n = Date.parse(t);
  return Number.isNaN(n) ? Number.NEGATIVE_INFINITY : n;
}

// Keep the newest row seen for this session. A tail with no row keeps the cache.
export function mergeSeen(cache, seen) {
  if (seen === null) return cache;
  if (cache === null) return seen;
  return stamp(seen.time) >= stamp(cache.time) ? seen : cache;
}

// Shape and word for each mode. Color is not used.
export function labelFor(mode) {
  if (mode === "enforce") return "◆ CONDUCTOR";
  if (mode === "guards") return "◇ DIRECT";
  if (mode === null) return "○ GATE ?";
  return `○ GATE ${mode}`;
}

// The button text and the line it puts in the prompt box.
export function buttonFor(mode) {
  return mode === "enforce"
    ? { label: "Conductor off", fill: "/ladder:conduct off" }
    : { label: "Conductor on", fill: "/ladder:conduct on" };
}

// Read only the tail of the log. A large log goes through "tail -c",
// because $.fs.read refuses files over 4 MiB.
export async function readTail($, path, bytes = TAIL_BYTES) {
  if (typeof path !== "string") return null;
  try {
    const st = await $.fs.stat(path);
    if (st.size <= bytes) {
      const t = await $.fs.read(path);
      return typeof t === "string" ? t : null;
    }
    const r = await $.process.run(["tail", "-c", String(bytes), path]);
    return r.exitCode === 0 ? r.stdout : null;
  } catch {
    return null;
  }
}

const held = { sid: null, row: null, timer: undefined };

async function refresh($) {
  try {
    const sid = await $.session.id();
    const text = await readTail($, await logPath($));
    // A new session id (for example after /clear) drops the old cache.
    if (held.sid !== sid) {
      held.sid = sid;
      held.row = null;
    }
    const seen = text === null ? null : lastConductorRow(text, sid);
    held.row = mergeSeen(held.row, seen);
    const mode = held.row === null ? null : held.row.mode;
    $.ui.status(labelFor(mode));
    await update($, modeAtom, () => mode);
  } catch {
    // Keep the last shown state.
  }
}

// Called from the ladder's own session.start hook (a plugin registers
// session.start only once).
// The row cache stays across a reload. A new session id drops it.
export async function gateStatusStart($) {
  await refresh($);
  try {
    if (held.timer !== undefined) held.timer.cancel();
    held.timer = $.clock.every(REFRESH_MS, () => {
      void refresh($);
    });
  } catch {
    // No timer: the marker still updates after each tool call and turn.
  }
}

export function gateStatusHooks(on) {
  // A tool call ended: refresh. The result passes on unchanged.
  on("tool.call", async ($, e, next) => {
    const result = await next(e);
    await refresh($);
    return result;
  }).catch(($, e, next) => next(e));

  on("turn.complete", async ($, e, next) => {
    await refresh($);
    return next(e);
  });

  // One line above the prompt: the mode word and one button.
  on("ui.render", { component: "AbovePrompt" }, async ($, e, next) => {
    if (e.props && e.props.hasSurvey) return next(e);
    const mode = await read($, modeAtom);
    const { Box, Button, Text } = $.ui.resolve(e);
    const btn = buttonFor(mode);
    return h(
      Box,
      null,
      h(Text, { dimColor: true }, "Gate: " + labelFor(mode) + "  "),
      h(Button, {
        key: "toggle",
        label: btn.label,
        onPress: () => {
          void $.prompt.fill({ text: btn.fill, mode: "replace" });
        },
      }),
    );
  });
}
