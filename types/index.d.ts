export type LadderMode = "auto" | "pin" | "off";

export type LadderState = {
  mode: LadderMode;
  interactive: boolean;
  setTier: string | null;
  pinUntilTurn: number;
  lastSwitchTurn: number;
  lastLabel: string | null;
  switches: number;
};

declare module "claude-code" {
  interface PluginState {
    ladder: {
      state: LadderState;
    };
  }
}
