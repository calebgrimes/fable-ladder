// The model ladder.
//
// The top model (Fable by default) is spent on judgment only. Opus, Sonnet and
// Haiku take the rungs below. Every prompt the person types is triaged by one
// cheap Haiku call, and when the session's current model is the wrong rung for
// it, the prompt is held, the session is moved with the same `/model` the
// person would type, and the prompt is resubmitted in their own words.
//
// Nothing is ever lost. Every failure path lets the prompt through on the
// current model, and a resubmission that fails puts the text back in the box.
//
// State lives in $.state, never in module variables: a hot reload re-runs
// register and would otherwise forget a pin the person asked for.

const TIERS = ["haiku", "sonnet", "opus", "fable"]; // bottom rung first
const STATE = { plugin: "ladder", key: "state" };

const DEFAULTS = {
  enabled: true,
  ceiling: "fable",
  floor: "sonnet",
  upSwitch: true,
  holdTurns: 3,
  minTurnsBetweenSwitches: 1,
  minChars: 40,
  classifier: "haiku",
};

const CLASSIFIER_TIMEOUT_MS = 3000;

// What the triage model reads. The prompt it labels is data, never instructions.
const CLASSIFIER_SYSTEM = [
  "You route one request to the cheapest model tier that will do it well.",
  "Answer with exactly one word: fable, opus, sonnet, or haiku. No punctuation, no explanation.",
  "",
  "fable: judgment calls, strategy, architecture, adversarial review, ethical or clinical reasoning,",
  "ambiguous or high-stakes decisions, writing that must sound like the user to a real person,",
  "anything where a wrong answer is expensive.",
  "opus: hard technical work: debugging, concurrency, subtle algorithms, deep analysis of a long",
  "document, planning a multi-step build.",
  "sonnet: ordinary implementation and edits from clear instructions, drafting from a spec,",
  "summaries, lookups that use tools, routine questions, continuing work already planned.",
  "haiku: bulk mechanical work: renames, format conversion, log triage, yes or no checks.",
  "",
  "When unsure between two tiers, answer the cheaper one.",
].join("\n");

// Added to every session's system prompt, so the ladder runs in every session
// whether or not anyone types /orchestrate.
const POLICY = [
  "# Model ladder",
  "Tiers, top to bottom: fable (judgment, strategy, adversarial review, anything expensive to get",
  "wrong), opus (hard technical work, planning a multi-step build), sonnet (ordinary implementation",
  "from clear instructions; the default worker, with a deliberately wide lane), haiku (bulk",
  "mechanical work). When you delegate with the Agent tool, set `model` explicitly to the lowest",
  "tier that will do the node well; never omit it. A session running on fable or opus is a",
  "conductor: it dispatches implementation and bulk work to cheaper workers one at a time, reads",
  "their short reports, and spends its own turns on judgment. For a multi-stage build, use the",
  "/orchestrate skill. The ladder plugin may move this session to another tier between prompts and",
  "says so each time; the status line shows the current tier and /ladder explains it.",
].join("\n");

export function register(on, options = {}) {
  const cfg = settings(options);

  on("session.start", async ($, e, next) => {
    const result = await next(e);
    await safe(() =>
      $.command.register({
        name: "ladder",
        description: "Show the model ladder, or set it: auto, pin, off.",
        argumentHint: "[auto|pin|off]",
      }),
    );
    const st = await load($);
    st.interactive = e.isInteractive === true;
    st.setTier = tierOf(await safe(() => $.session.model()));
    await save($, st);
    showStatus($, st, st.setTier);
    return result;
  });

  on("prompt.compose", async ($, e, next) => {
    const result = await next(e);
    if (!cfg.enabled) return result;
    const sections = (result.sections ?? []).filter((s) => s.id !== "ladder:policy");
    return { sections: [...sections, { id: "ladder:policy", text: POLICY, scope: "session" }] };
  });

  on("turn.complete", async ($, e, next) => {
    const result = await next(e);
    if (!e.agentId) {
      const st = await load($);
      showStatus($, st, tierOf(await safe(() => $.session.model())));
    }
    return result;
  });

  on("command.run", { command: "ladder" }, async ($, e) => {
    const st = await load($);
    const current = tierOf(await safe(() => $.session.model())) ?? "unknown";
    const arg = String(e.args ?? "").trim().toLowerCase();
    if (arg === "auto" || arg === "pin" || arg === "off") {
      st.mode = arg;
      if (arg === "auto") st.pinUntilTurn = 0;
      if (arg === "pin") st.setTier = current;
      await save($, st);
      showStatus($, st, current);
      const tail = arg === "auto" ? "; the next prompt is triaged" : "";
      return { text: `Ladder ${arg}. The session stays on ${current}${tail}.` };
    }
    return { text: report(st, current, cfg) };
  });

  // The gating hook. It judges before it calls next, and its catch lets the
  // prompt through on the current model whatever went wrong.
  on("prompt.submit", async ($, e, next) => {
    if (!cfg.enabled) return next(e);
    if (e.origin && e.origin.kind !== "composer" && e.origin.kind !== "bridge") return next(e);
    if (e.turnId) return next(e);
    if (e.attachments && e.attachments.length > 0) return next(e);
    const text = typeof e.text === "string" ? e.text : "";
    if (text.trimStart().startsWith("/")) return next(e);
    if (text.trim().length < cfg.minChars) return next(e);

    const st = await load($);
    if (st.mode === "off" || st.interactive === false) return next(e);

    const current = tierOf(await safe(() => $.session.model()));
    if (!current) return next(e);
    const turns = (await safe(() => $.session.turns())) ?? 0;

    // The person chose a model by hand since the ladder last looked. Honour it.
    if (st.setTier && current !== st.setTier) {
      st.setTier = current;
      st.pinUntilTurn = turns + cfg.holdTurns;
      await save($, st);
      $.ui.toast(`Ladder: you chose ${current}; holding it for ${cfg.holdTurns} prompts.`);
      showStatus($, st, current);
      return next(e);
    }
    if (st.mode === "pin" || turns < st.pinUntilTurn) return next(e);
    if (st.switches > 0 && turns - st.lastSwitchTurn < cfg.minTurnsBetweenSwitches) return next(e);

    const t0 = Date.now();
    const label = await classify($, text, cfg);
    const ms = Date.now() - t0;
    if (!label) return next(e);
    st.lastLabel = label;

    const target = clamp(label, cfg.floor, cfg.ceiling);
    const up = rank(target) > rank(current);
    if (target === current || (up && !cfg.upSwitch)) {
      await save($, st);
      return next(e);
    }

    let costNote = "";
    if (up) {
      const usage = await safe(() => $.session.usage());
      const k = Math.round((usage?.context?.tokens ?? 0) / 1000);
      if (k > 0) costNote = `; re-reads ~${k}k of context on ${target}`;
    }

    st.setTier = target;
    st.lastSwitchTurn = turns;
    st.switches += 1;
    await save($, st);

    // Everything that can fail has run. From here the prompt is either dropped
    // and resubmitted by the timer, or let through: never both.
    const original = text;
    try {
      $.clock.after(0, async () => {
        let landed = current;
        try {
          await $.command.run({ command: "model", args: target });
          landed = target;
        } catch (err) {
          $.ui.toast(`Ladder: could not switch to ${target} (${reason(err)}); sending on ${current}.`);
          const back = await load($);
          back.setTier = current;
          await save($, back);
        }
        try {
          await $.prompt.submit({ text: original, asUser: true });
        } catch (err) {
          await safe(() => $.prompt.fill({ text: original }));
          $.ui.toast(`Ladder: could not resubmit (${reason(err)}); your prompt is back in the box.`);
        }
        showStatus($, await load($), landed);
      });
    } catch {
      return next(e);
    }
    const arrow = up ? "up to" : "down to";
    return {
      drop: `Ladder: ${current} ${arrow} ${target} for this one (${label} work, triaged in ${ms} ms${costNote}). Resubmitting your prompt.`,
    };
  }).catch(($, e, next) => next(e));
}

async function classify($, text, cfg) {
  const r = await safe(() =>
    $.model.complete({
      model: cfg.classifier,
      system: CLASSIFIER_SYSTEM,
      prompt: text.slice(0, 4000),
      effort: "low",
      maxTokens: 8,
      timeoutMs: CLASSIFIER_TIMEOUT_MS,
    }),
  );
  if (!r || !r.isAnswered || typeof r.text !== "string") return null;
  const m = r.text.toLowerCase().match(/fable|opus|sonnet|haiku/);
  return m ? m[0] : null;
}

function settings(options) {
  const out = { ...DEFAULTS };
  for (const key of Object.keys(DEFAULTS)) {
    const v = options[key];
    if (v === undefined || v === null || v === "") continue;
    out[key] = v;
  }
  for (const key of ["holdTurns", "minTurnsBetweenSwitches", "minChars"]) {
    const n = Number(out[key]);
    out[key] = Number.isFinite(n) && n >= 0 ? n : DEFAULTS[key];
  }
  if (!TIERS.includes(out.ceiling)) out.ceiling = DEFAULTS.ceiling;
  if (!TIERS.includes(out.floor)) out.floor = DEFAULTS.floor;
  if (rank(out.floor) > rank(out.ceiling)) out.floor = out.ceiling;
  return out;
}

function tierOf(modelId) {
  const m = String(modelId ?? "").toLowerCase();
  for (const t of ["fable", "opus", "sonnet", "haiku"]) if (m.includes(t)) return t;
  return null;
}

function rank(tier) {
  return TIERS.indexOf(tier);
}

function clamp(label, floor, ceiling) {
  const r = Math.min(Math.max(rank(label), rank(floor)), rank(ceiling));
  return TIERS[r];
}

async function load($) {
  const { value } = await $.state.get(STATE);
  return {
    mode: "auto",
    interactive: true,
    setTier: null,
    pinUntilTurn: 0,
    lastSwitchTurn: 0,
    lastLabel: null,
    switches: 0,
    ...(value ?? {}),
  };
}

async function save($, st) {
  await $.state.set(STATE, st);
}

function showStatus($, st, tier) {
  const mode = st.mode === "auto" ? "auto" : st.mode === "pin" ? "pinned" : "off";
  $.ui.status(`ladder: ${tier ?? "?"} · ${mode}`);
}

function report(st, current, cfg) {
  return [
    `Ladder is ${st.mode}. Session model: ${current}.`,
    `Rungs: ${cfg.ceiling} (top) down to ${cfg.floor}; switch back up ${cfg.upSwitch ? "on" : "off"}.`,
    `Last triage: ${st.lastLabel ?? "none yet"}. Switches this session: ${st.switches}.`,
    `/ladder auto resumes, /ladder pin holds the current model, /ladder off stops triage. Rungs and holds are in /config.`,
  ].join("\n");
}

function reason(err) {
  const m = err && typeof err.message === "string" ? err.message : String(err ?? "unknown");
  return m.slice(0, 120);
}

async function safe(fn) {
  try {
    return await fn();
  } catch {
    return null;
  }
}
