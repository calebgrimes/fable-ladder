// The ladder: an orchestrator plugin.
//
// It never switches the session's model. It does three things: it appends the
// delegation policy to every system prompt (so tier choice on Agent calls is
// explicit in every session, whether or not anyone types /orchestrate), it
// registers /ladder to report the version and update status, and it keeps
// itself current with the self-updater below. The conductor skill lives in
// skills/orchestrate.

const DEFAULTS = {
  updates: "notify",
  updateEveryDays: 21,
};

// Added to every session's system prompt.
const POLICY = [
  "# Model ladder",
  "Tiers, top to bottom: fable (judgment, strategy, adversarial review, anything expensive to get",
  "wrong), opus (hard technical work, planning a multi-step build), sonnet (ordinary implementation",
  "from clear instructions; the default worker, with a deliberately wide lane), haiku (bulk",
  "mechanical work). When you delegate with the Agent tool, set `model` explicitly to the lowest",
  "tier that will do the node well; never omit it. Read each worker's report as evidence of fit: if",
  "it stalled on understanding, failed its check, or claimed work it did not show, re-dispatch one",
  "tier up with that report attached (never to fable without asking); if it passed easily, start the",
  "next similar task one tier lower. A session running on fable or opus is a",
  "conductor: it dispatches implementation and bulk work to cheaper workers one at a time, reads",
  "their short reports, and spends its own turns on judgment. For a multi-stage build, use the",
  "/orchestrate skill.",
].join("\n");

export function register(on, options = {}) {
  const cfg = settings(options);

  on("session.start", async ($, e, next) => {
    const result = await next(e);
    await safe(() =>
      $.command.register({
        name: "ladder",
        description: "Show the ladder plugin's version and update status, or check for an update now.",
        argumentHint: "[update]",
      }),
    );
    scheduleUpdateCheck($, e, cfg);
    return result;
  });

  on("prompt.compose", async ($, e, next) => {
    const result = await next(e);
    const sections = (result.sections ?? []).filter((s) => s.id !== "ladder:policy");
    return { sections: [...sections, { id: "ladder:policy", text: POLICY, scope: "session" }] };
  });

  on("command.run", { command: "ladder" }, async ($, e) => {
    const arg = String(e.args ?? "").trim().toLowerCase();
    if (arg === "update") {
      const outcome = await checkForUpdate($, cfg, { force: true });
      const said = {
        current: "Already current.",
        available: "An update is available.",
        applied: "Updated. /reload-plugins to run it.",
        failed: "The update failed; see the notice.",
      }[outcome] ?? "Could not check; see the notice.";
      return { text: `${said}\n${await updateSummary($, cfg)}` };
    }
    const version = (await localVersion($)) ?? "unknown";
    return { text: `Ladder ${version}: orchestrator plugin (/orchestrate, delegation policy). It does not switch the session model.\n${await updateSummary($, cfg)}` };
  }).catch(($, e, next) => next(e));
}

function settings(options) {
  const out = { ...DEFAULTS };
  for (const key of Object.keys(DEFAULTS)) {
    const v = options[key];
    if (v === undefined || v === null || v === "") continue;
    out[key] = v;
  }
  if (!["apply", "notify", "off"].includes(out.updates)) out.updates = DEFAULTS.updates;
  const days = Number(out.updateEveryDays);
  out.updateEveryDays = Number.isFinite(days) && days >= 1 ? days : DEFAULTS.updateEveryDays;
  return out;
}

async function safe(fn) {
  try {
    return await fn();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Self-update
// Self-update. Every few weeks, compare the installed manifest's version with
// the one on the repository's main branch, then notify or apply, per /config.
//
// A folder install (the plugin read from a git working copy) updates with
// `git pull --ff-only`, which refuses rather than overwrite local edits. A
// marketplace copy updates with `claude plugin update`. Either way the running
// session keeps the old code until /reload-plugins; the toast says so.
//
// The check runs well after session start and off the prompt's path, and every
// failure is swallowed: an updater must never cost the person a prompt.

const SOURCE = "https://raw.githubusercontent.com/calebgrimes/fable-ladder/main/.claude-plugin/plugin.json";
const INSTALL_ID = "ladder@fable-ladder";
const LAST_CHECK = "updates.lastCheck";
const LAST_RESULT = "updates.lastResult";
const START_DELAY_MS = 20_000;
const DAY_MS = 86_400_000;

// Called from the ladder's own session.start hook (one plugin may register
// session.start only once), well after the session is up.
function scheduleUpdateCheck($, e, cfg) {
  if (cfg.updates === "off" || e.isInteractive !== true) return;
  try {
    $.clock.after(START_DELAY_MS, () => checkForUpdate($, cfg, { force: false }));
  } catch {
    // An updater that cannot schedule itself is simply absent this session.
  }
}

// Resolves what happened: "current", "available", "applied", "failed", or null
// when nothing was checked (too soon, off, or unreachable).
async function checkForUpdate($, cfg, { force }) {
  try {
    const now = await $.clock.now();
    if (!force) {
      const last = await $.store.get(LAST_CHECK);
      if (typeof last === "number" && now - last < cfg.updateEveryDays * DAY_MS) return null;
    }
    // Claim the check before fetching, so several windows opening together make one request.
    await $.store.set(LAST_CHECK, now);

    const local = await localVersion($);
    const remote = await remoteVersion($);
    if (!local || !remote) {
      await $.store.set(LAST_RESULT, { at: now, local, remote, outcome: "unreachable" });
      if (force) $.ui.toast(`Ladder: could not read the ${local ? "published" : "installed"} version.`);
      return null;
    }
    if (compareVersions(remote, local) <= 0) {
      await $.store.set(LAST_RESULT, { at: now, local, remote, outcome: "current" });
      if (force) $.ui.toast(`Ladder ${local} is current.`);
      return "current";
    }
    if (cfg.updates !== "apply" && !force) {
      await $.store.set(LAST_RESULT, { at: now, local, remote, outcome: "available" });
      $.ui.toast(`Ladder ${remote} is out (installed: ${local}). /ladder update applies it.`, { timeoutMs: 10_000 });
      return "available";
    }
    const applied = await apply($);
    await $.store.set(LAST_RESULT, {
      at: now,
      local,
      remote,
      outcome: applied.ok ? "applied" : "failed",
      detail: applied.detail,
    });
    if (applied.ok) {
      $.ui.toast(`Ladder updated ${local} to ${remote}. /reload-plugins to run it.`, { timeoutMs: 10_000 });
      return "applied";
    }
    $.ui.toast(`Ladder ${remote} is out but the update failed: ${applied.detail}. Update by hand.`, { timeoutMs: 10_000 });
    return "failed";
  } catch {
    return null;
  }
}

async function updateSummary($, cfg) {
  const r = await safe(() => $.store.get(LAST_RESULT));
  const when = r && typeof r.at === "number" ? new Date(r.at).toISOString().slice(0, 10) : "never";
  const what = r && r.outcome ? `${r.outcome}${r.remote ? ` (published ${r.remote}, installed ${r.local ?? "?"})` : ""}` : "no check yet";
  return `Updates: ${cfg.updates}, checked every ${cfg.updateEveryDays} days; last check ${when}: ${what}. /ladder update checks now.`;
}

async function localVersion($) {
  try {
    const raw = await $.fs.read(`${$.plugin.root}/.claude-plugin/plugin.json`);
    const text = typeof raw === "string" ? raw : raw && typeof raw.text === "string" ? raw.text : "{}";
    const v = JSON.parse(text).version;
    return typeof v === "string" ? v : null;
  } catch {
    return null;
  }
}

async function remoteVersion($) {
  try {
    const res = await $.http.fetch(SOURCE, { headers: { "cache-control": "no-cache" } });
    if (!res || !res.ok) return null;
    const v = JSON.parse(res.text).version;
    return typeof v === "string" ? v : null;
  } catch {
    return null;
  }
}

async function apply($) {
  const root = $.plugin.root;
  const isCheckout = (await safe(() => $.fs.exists(`${root}/.git`))) === true;
  const argv = isCheckout ? ["git", "-C", root, "pull", "--ff-only"] : ["claude", "plugin", "update", INSTALL_ID];
  try {
    const out = await $.process.run(argv, { timeoutMs: 120_000 });
    if (out && out.exitCode === 0) return { ok: true, detail: argv[0] };
    return { ok: false, detail: oneLine((out && (out.stderr || out.stdout)) || `exit ${out?.exitCode}`) };
  } catch (err) {
    return { ok: false, detail: oneLine(err && err.message ? err.message : String(err)) };
  }
}

// Positive when a is newer than b. Plain dotted numbers; anything else reads as 0.
function compareVersions(a, b) {
  const pa = String(a).split(".").map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d;
  }
  return 0;
}

function oneLine(s) {
  return String(s ?? "").replace(/\s+/g, " ").trim().slice(0, 160);
}

