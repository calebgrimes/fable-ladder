// Tests for the ladder's prompt gate. The hooks registered here sit BENEATH the
// plugin and stand in for Claude Code, so what they answer is what the plugin
// sees: the session's model, the turn count, the triage reply, and what became
// of the /model command and the resubmitted prompt.
//
// The invariant that matters most is that a prompt is never lost, so each
// failure path gets its own test.

import { describe, expect, mock, test } from "claude-code/testing";

const LONG =
  "Please add a --json flag to bin/report.py, update the twelve call sites in docs, and add a test for it.";
const SHORT = "yes, continue";

type Opts = {
  model?: string;
  label?: string | null;
  turns?: number;
  failRun?: boolean;
  failResubmit?: boolean;
};

function world(on: any, opts: Opts = {}) {
  const w = {
    model: opts.model ?? "claude-fable-5-1",
    runs: [] as any[],
    entered: [] as any[],
    fills: [] as any[],
    completes: 0,
    clock: mock.clock(on),
  };
  on("session.start", ($: any, e: any) => ({ cwd: e.cwd }));
  on("session.model", () => ({ value: w.model }));
  on("session.turns", () => ({ value: opts.turns ?? 5 }));
  on("session.usage", () => ({
    value: { startedAt: 0, context: { tokens: 42_000, window: 200_000, percent: 21 }, rateLimits: [] },
  }));
  on("command.register", () => ({ value: { command: "ladder" } }));
  on("command.run", ($: any, e: any) => {
    w.runs.push(e);
    if (opts.failRun) throw new Error("model command refused");
    if (e.command === "model") w.model = `claude-${e.args}-test`;
    return { text: "" };
  });
  on("model.complete", () => {
    w.completes += 1;
    if (opts.label === null) {
      return { value: { isAnswered: false, reason: "api-error", status: 500, error: "server_error" } };
    }
    return {
      value: {
        isAnswered: true,
        text: opts.label ?? "sonnet",
        usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 },
      },
    };
  });
  on("prompt.submit", ($: any, e: any) => {
    if (opts.failResubmit && e.origin?.kind === "plugin") throw new Error("resubmit refused");
    w.entered.push(e);
    return { text: e.text };
  });
  on("prompt.fill", ($: any, e: any) => {
    w.fills.push(e);
    return { isFilled: true };
  });
  on("turn.complete", () => ({ text: "" }));
  return w;
}

async function start($: any) {
  await $.session.start({ surface: "terminal", isInteractive: true, cwd: "/work" });
}

function typed(text: string) {
  return { text, wait: false, origin: { kind: "composer" } } as any;
}

describe("ladder", () => {
  test("a routine prompt on fable moves the session down to sonnet and resubmits the same words", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "sonnet" });
    await start($);
    const r: any = await $.prompt.submit(typed(LONG));
    expect(r.drop).toMatch(/fable down to sonnet/);
    await w.clock.advance(1);
    expect(w.runs.some((e) => e.command === "model" && e.args === "sonnet")).toBe(true);
    const again = w.entered.find((e) => e.origin?.kind === "plugin");
    expect(again?.text).toBe(LONG);
    // The original, typed prompt never entered on fable.
    expect(w.entered.some((e) => e.origin?.kind === "composer")).toBe(false);
  });

  test("a judgment prompt on sonnet moves back up to fable and names the context cost", async ($, on) => {
    const w = world(on, { model: "claude-sonnet-5-5", label: "fable" });
    await start($);
    const r: any = await $.prompt.submit(typed(LONG));
    expect(r.drop).toMatch(/sonnet up to fable/);
    expect(r.drop).toMatch(/42k of context/);
    await w.clock.advance(1);
    expect(w.runs.some((e) => e.command === "model" && e.args === "fable")).toBe(true);
  });

  test("a short prompt passes through untouched and is never triaged", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "sonnet" });
    await start($);
    const r: any = await $.prompt.submit(typed(SHORT));
    expect(r.drop).toBeUndefined();
    expect(w.completes).toBe(0);
    expect(w.runs.length).toBe(0);
    expect(w.entered[0]?.text).toBe(SHORT);
  });

  test("a triage failure lets the prompt through on the current model", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: null });
    await start($);
    const r: any = await $.prompt.submit(typed(LONG));
    expect(r.drop).toBeUndefined();
    expect(w.completes).toBe(1);
    expect(w.runs.length).toBe(0);
    expect(w.entered[0]?.text).toBe(LONG);
  });

  test("a refused /model still resubmits the prompt on the current model", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "sonnet", failRun: true });
    await start($);
    await $.prompt.submit(typed(LONG));
    await w.clock.advance(1);
    const again = w.entered.find((e) => e.origin?.kind === "plugin");
    expect(again?.text).toBe(LONG);
  });

  test("a refused resubmission puts the text back in the prompt box", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "sonnet", failResubmit: true });
    await start($);
    await $.prompt.submit(typed(LONG));
    await w.clock.advance(1);
    expect(w.fills[0]?.text).toBe(LONG);
  });

  test("a model the person chose by hand is held, not overridden", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "sonnet" });
    await start($);
    w.model = "claude-opus-5"; // typed /model opus since the ladder last looked
    const r: any = await $.prompt.submit(typed(LONG));
    expect(r.drop).toBeUndefined();
    expect(w.completes).toBe(0);
    expect(w.entered[0]?.text).toBe(LONG);
    // Still held on the next prompt.
    const r2: any = await $.prompt.submit(typed(LONG));
    expect(r2.drop).toBeUndefined();
    expect(w.completes).toBe(0);
  });

  test("/ladder off stops triage", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "sonnet" });
    await start($);
    const out: any = await $.command.run({ command: "ladder", args: "off", origin: { kind: "composer" } } as any);
    expect(out.text).toMatch(/Ladder off/);
    const r: any = await $.prompt.submit(typed(LONG));
    expect(r.drop).toBeUndefined();
    expect(w.completes).toBe(0);
  });

  test("the floor holds: haiku work on fable lands on sonnet, not haiku", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "haiku" });
    await start($);
    const r: any = await $.prompt.submit(typed(LONG));
    expect(r.drop).toMatch(/down to sonnet/);
    await w.clock.advance(1);
    expect(w.runs.some((e) => e.command === "model" && e.args === "sonnet")).toBe(true);
  });

  test("with switching back up off, a judgment prompt stays on sonnet", { options: { upSwitch: false } }, async ($, on) => {
    const w = world(on, { model: "claude-sonnet-5-5", label: "fable" });
    await start($);
    const r: any = await $.prompt.submit(typed(LONG));
    expect(r.drop).toBeUndefined();
    expect(w.runs.length).toBe(0);
    expect(w.entered[0]?.text).toBe(LONG);
  });

  test("a prompt a plugin submitted is never triaged (the loop guard)", async ($, on) => {
    const w = world(on, { model: "claude-fable-5-1", label: "sonnet" });
    await start($);
    const r: any = await $.prompt.submit({ text: LONG, wait: false, origin: { kind: "plugin", name: "other" } } as any);
    expect(r.drop).toBeUndefined();
    expect(w.completes).toBe(0);
  });

  test("the policy section is appended to every system prompt, on the session side", async ($, on) => {
    world(on, {});
    on("prompt.compose", () => ({ sections: [{ id: "intro", text: "x", scope: "shared" }] }));
    await start($);
    const r: any = await $.prompt.compose({
      model: "claude-fable-5-1",
      promptModel: "claude-fable-5-1",
      surfaces: ["terminal"],
      tools: [],
      outputStyle: null,
      traits: [],
    } as any);
    const last = r.sections[r.sections.length - 1];
    expect(last.id).toBe("ladder:policy");
    expect(last.scope).toBe("session");
    expect(last.text).toMatch(/fable/);
  });
});
