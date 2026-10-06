// Tests for the self-updater. The hooks here stand in for the network, the
// filesystem, the host's processes and the clock, so each outcome is exercised
// without touching GitHub or git.

import { describe, expect, mock, test } from "claude-code/testing";

const DAY = 86_400_000;
const START = 100 * DAY;
const START_DELAY = 20_000;

type Opts = {
  local?: string;
  remote?: string | null;
  lastCheck?: number;
  isCheckout?: boolean;
  runExit?: number;
};

function world(on: any, opts: Opts = {}) {
  const w = { fetches: 0, runs: [] as string[][], writes: [] as any[], clock: mock.clock(on, { now: START }) };
  // A store of the test's own, so seeding and capture come from one hook each
  // (one module may hook an event only once, and the kit's mock.store would be a second).
  const mem: Record<string, unknown> = opts.lastCheck === undefined ? {} : { "updates.lastCheck": opts.lastCheck };
  on("store.get", ($: any, e: any) => ({ value: mem[e.key] }));
  on("store.set", ($: any, e: any) => {
    mem[e.key] = e.value;
    w.writes.push(e);
    return { value: undefined };
  });
  on("store.delete", ($: any, e: any) => {
    delete mem[e.key];
    return { value: undefined };
  });
  on("store.keys", () => ({ value: Object.keys(mem) }));
  on("ui.status", () => ({ value: undefined }));
  on("ui.toast", () => ({ value: undefined }));
  on("session.start", ($: any, e: any) => ({ cwd: e.cwd }));
  on("session.model", () => ({ value: "claude-fable-5-1" }));
  on("session.turns", () => ({ value: 1 }));
  on("command.register", () => ({ value: { command: "ladder" } }));
  on("turn.complete", () => ({ text: "" }));
  on("fs.read", () => ({ value: JSON.stringify({ name: "ladder", version: opts.local ?? "0.3.0" }) }));
  on("fs.exists", () => ({ value: opts.isCheckout ?? true }));
  on("http.fetch", () => {
    w.fetches += 1;
    if (opts.remote === null) return { value: { status: 500, ok: false, headers: {}, text: "" } };
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify({ version: opts.remote ?? "0.3.0" }) } };
  });
  on("process.run", ($: any, e: any) => {
    w.runs.push([...e.argv]);
    return { value: { exitCode: opts.runExit ?? 0, stdout: "", stderr: opts.runExit ? "boom" : "" } };
  });
  return w;
}

async function start($: any, isInteractive = true) {
  await $.session.start({ surface: "terminal", isInteractive, cwd: "/work" });
}

function lastResult(w: { writes: any[] }) {
  const hits = w.writes.filter((e) => e.key === "updates.lastResult");
  return hits.length ? hits[hits.length - 1].value : undefined;
}

describe("ladder updater", () => {
  test("a stale check with a newer version published notifies and leaves the install alone", async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0" });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    expect(w.fetches).toBe(1);
    expect(w.runs.length).toBe(0);
    expect(lastResult(w)?.outcome).toBe("available");
  });

  test("a recent check is not repeated", async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0", lastCheck: START - DAY });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    expect(w.fetches).toBe(0);
  });

  test("a check older than the interval runs again", async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.3.0", lastCheck: START - 30 * DAY });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    expect(w.fetches).toBe(1);
    expect(lastResult(w)?.outcome).toBe("current");
  });

  test("apply mode fast-forwards a git checkout", { options: { updates: "apply" } }, async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0", isCheckout: true });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    expect(w.runs[0]?.slice(0, 1)).toEqual(["git"]);
    expect(w.runs[0]).toContain("--ff-only");
    expect(lastResult(w)?.outcome).toBe("applied");
  });

  test("apply mode on a marketplace copy runs claude plugin update", { options: { updates: "apply" } }, async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0", isCheckout: false });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    expect(w.runs[0]?.slice(0, 3)).toEqual(["claude", "plugin", "update"]);
  });

  test("a failed update is recorded as failed, with the reason", { options: { updates: "apply" } }, async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0", runExit: 1 });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    const r = lastResult(w);
    expect(r?.outcome).toBe("failed");
    expect(r?.detail).toMatch(/boom/);
  });

  test("an unreachable source is recorded and nothing runs", async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: null });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    expect(w.runs.length).toBe(0);
    expect(lastResult(w)?.outcome).toBe("unreachable");
  });

  test("/ladder update checks now and applies even in notify mode", async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0", lastCheck: START - DAY });
    await start($);
    const out: any = await $.command.run({ command: "ladder", args: "update", origin: { kind: "composer" } } as any);
    expect(w.fetches).toBe(1);
    expect(w.runs.length).toBe(1);
    expect(out.text).toMatch(/Updated/);
  });

  test("updates off never looks", { options: { updates: "off" } }, async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0" });
    await start($);
    await w.clock.advance(START_DELAY + 1);
    expect(w.fetches).toBe(0);
  });

  test("a headless session never looks", async ($, on) => {
    const w = world(on, { local: "0.3.0", remote: "0.4.0" });
    await start($, false);
    await w.clock.advance(START_DELAY + 1);
    expect(w.fetches).toBe(0);
  });

  test("/ladder reports the update line", async ($, on) => {
    world(on, {});
    await start($);
    const out: any = await $.command.run({ command: "ladder", args: "", origin: { kind: "composer" } } as any);
    expect(out.text).toMatch(/Updates: notify, checked every 21 days/);
  });
});
