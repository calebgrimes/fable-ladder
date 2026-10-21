// The ladder keeps no session state: it neither switches models nor tracks
// prompts. Update bookkeeping lives in the plugin store.
export type LadderUpdateResult = {
  at: number;
  local: string | null;
  remote: string | null;
  outcome: "current" | "available" | "applied" | "failed" | "unreachable";
  detail?: string;
};

// The gate marker (hooks/gate-status.mjs).
// gateMode: the conductor gate mode of this window, or null when the gate log
// has no row for this session yet.
export type GateMode = string | null;

declare module "claude-code" {
  interface PluginState {
    ladder: { gateMode: GateMode };
  }
}
