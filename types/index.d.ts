// The ladder keeps no session state: it neither switches models nor tracks
// prompts. Update bookkeeping lives in the plugin store.
export type LadderUpdateResult = {
  at: number;
  local: string | null;
  remote: string | null;
  outcome: "current" | "available" | "applied" | "failed" | "unreachable";
  detail?: string;
};
