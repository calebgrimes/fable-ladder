// Tests for the ladder's session side. The hooks registered here sit BENEATH
// the plugin and stand in for Claude Code. The plugin no longer switches the
// session model, so what matters is that the policy section is appended, that
// a prompt passes through untouched, and that /ladder reports.

import { describe, expect, mock, test } from "claude-code/testing";

const LONG =
  "Please add a --json flag to bin/report.py, update the twelve call sites in docs, and add a test for it.";

function world(on: any) {
  const w = {
    runs: [] as any[],
    completes: 0,
    entered: [] as any[],
    clock: mock.clock(on),
  };
  on("session.start", ($: any, e: any) => ({ cwd: e.cwd }));
  on("session.model", () => ({ value: "claude-fable-5-1" }));
  on("command.register", () => ({ value: { command: "ladder" } }));
  on("fs.read", () => ({ value: JSON.stringify({ name: "ladder", version: "0.4.0" }) }));
  on("store.get", () => ({ value: undefined }));
  on("model.complete", () => {
    w.completes += 1;
    return { value: { isAnswered: true, text: "sonnet" } };
  });
  on("prompt.submit", ($: any, e: any) => {
    w.entered.push(e);
    return { text: e.text };
  });
  on("turn.complete", () => ({ text: "" }));
  return w;
}

async function start($: any) {
  await $.session.start({ surface: "terminal", isInteractive: true, cwd: "/work" });
}

describe("ladder", () => {
  test("the policy section is appended to every system prompt, on the session side", async ($, on) => {
    world(on);
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
    expect(last.text).toMatch(/\/orchestrate/);
    expect(last.text).not.toMatch(/status line/);
    expect(last.text).not.toMatch(/may move this session/);
  });

  test("a prompt passes through untouched: no triage, no model switch", async ($, on) => {
    const w = world(on);
    await start($);
    const r: any = await $.prompt.submit({ text: LONG, wait: false, origin: { kind: "composer" } } as any);
    expect(r.drop).toBeUndefined();
    expect(w.completes).toBe(0);
    expect(w.entered.length).toBe(1);
    expect(w.entered[0].text).toBe(LONG);
  });

  test("bare /ladder reports the version and does not switch anything", async ($, on) => {
    world(on);
    await start($);
    const out: any = await $.command.run({ command: "ladder", args: "", origin: { kind: "composer" } } as any);
    expect(out.text).toMatch(/Ladder 0\.4\.0/);
    expect(out.text).toMatch(/does not switch the session model/);
  });

  test("the removed modes are not commands: /ladder pin just reports", async ($, on) => {
    world(on);
    await start($);
    const out: any = await $.command.run({ command: "ladder", args: "pin", origin: { kind: "composer" } } as any);
    expect(out.text).toMatch(/Ladder 0\.4\.0/);
  });
});
