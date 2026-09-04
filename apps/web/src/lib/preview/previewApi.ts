/**
 * The preview backend: recorded engine output, replayed.
 *
 * Every response here was produced by the real FastAPI application and the real
 * recommendation engine, captured by `fhe preview capture` and committed as
 * `recorded.json`. Nothing in this file computes a score, a ranking, a
 * survival probability, or a risk figure — it selects an already-computed
 * snapshot and hands it back.
 *
 * The recording is validated through the same zod schemas as a live response,
 * so a contract change breaks preview exactly as loudly as it breaks
 * production, rather than letting the two drift apart silently.
 *
 * Advancing a draft steps to the next recorded snapshot. Actions that cannot be
 * replayed — drafting a player of your choosing, connecting a Sleeper league —
 * are refused with a clear reason rather than faked, because a preview that
 * invents an outcome is worse than one that admits its limits.
 */
import { ApiError } from "@/lib/apiError";
import {
  draftBoardSchema,
  draftStateSchema,
  playerDetailSchema,
  type DraftBoard,
  type DraftState,
  type HealthStatus,
  type PlayerDetail,
  type SessionStatus,
  type SimulationState,
} from "@/lib/types";

import { PREVIEW_UNAVAILABLE } from "./mode";

/** The single draft id preview serves. */
export const PREVIEW_DRAFT_ID = "preview";

interface RecordedSnapshot {
  index: number;
  board: unknown;
  state: unknown;
}

interface Recording {
  generated_at: string;
  engine_version: string;
  seed: number;
  warning: string;
  snapshots: RecordedSnapshot[];
  players: Record<string, unknown>;
}

let recording: Recording | null = null;

/**
 * Loaded on first use rather than imported at module scope, so the recording
 * is a separate chunk fetched only by a preview build.
 */
async function load(): Promise<Recording> {
  if (recording === null) {
    recording = (await import("./recorded.json")).default as unknown as Recording;
  }
  return recording;
}

/**
 * How far through the recording the preview has advanced. Module state because
 * there is exactly one preview draft, and it should survive navigation the way
 * a real draft on the server would.
 */
let cursor = 0;

function clamp(index: number, length: number): number {
  return Math.max(0, Math.min(index, length - 1));
}

function boardAt(data: Recording, index: number): DraftBoard {
  return draftBoardSchema.parse(
    data.snapshots[clamp(index, data.snapshots.length)].board,
  );
}

function stateAt(data: Recording, index: number): DraftState {
  return draftStateSchema.parse(
    data.snapshots[clamp(index, data.snapshots.length)].state,
  );
}

/** True once the recording has no further snapshots to show. */
export async function isAtEndOfRecording(): Promise<boolean> {
  const data = await load();
  return cursor >= data.snapshots.length - 1;
}

export async function previewRecordingInfo(): Promise<{
  snapshots: number;
  position: number;
  seed: number;
  engineVersion: string;
}> {
  const data = await load();
  return {
    snapshots: data.snapshots.length,
    position: cursor + 1,
    seed: data.seed,
    engineVersion: data.engine_version,
  };
}

function unavailable(): never {
  throw new ApiError(PREVIEW_UNAVAILABLE, 501, "preview_mode");
}

export const previewApi = {
  // Preview has no backend and therefore nothing to protect. Reporting no gate
  // is the truth about this environment, not a bypass of a real one: the flag
  // is a build-time constant and a normal build never reaches this code.
  sessionStatus: async (): Promise<SessionStatus> => ({
    required: false,
    authenticated: true,
  }),

  signIn: async (): Promise<SessionStatus> => ({
    required: false,
    authenticated: true,
  }),

  signOut: async (): Promise<SessionStatus> => ({
    required: false,
    authenticated: true,
  }),

  health: async (): Promise<HealthStatus> => ({
    status: "ok",
    version: (await load()).engine_version,
    environment: "preview",
    checks: {},
    // Preview is a degradation and says so, in the same channel the API uses
    // to report SQLite and the in-process bus.
    degradations: [
      "Preview mode: the board is recorded synthetic output, not a live engine.",
    ],
  }),

  createSimulation: async (): Promise<SimulationState> => {
    cursor = 0;
    const data = await load();
    const state = stateAt(data, cursor);
    return {
      simulation_id: PREVIEW_DRAFT_ID,
      is_demo: true,
      seed: data.seed,
      status: state.status,
      pick_count: state.pick_count,
      total_picks: state.total_picks,
      is_complete: state.is_complete,
      is_user_on_the_clock: state.is_user_on_the_clock,
      created_at: state.created_at,
    };
  },

  getSimulation: async (): Promise<SimulationState> => previewApi.createSimulation(),

  getBoard: async (_id: string, depth = 120): Promise<DraftBoard> => {
    const board = boardAt(await load(), cursor);
    return { ...board, recommendations: board.recommendations.slice(0, depth) };
  },

  getDraftState: async (): Promise<DraftState> => stateAt(await load(), cursor),

  advance: async (
    _id: string,
    picks: number,
    stopAtUserTurn = true,
  ): Promise<unknown[]> => {
    const data = await load();
    const last = data.snapshots.length - 1;
    if (cursor >= last) {
      throw new ApiError(
        "End of the recorded preview draft. Reset to start again, or run the " +
          "real API for a full draft.",
        409,
        "preview_exhausted",
      );
    }

    // Same contract as the real endpoint: advance up to `picks` picks, and stop
    // early if the user's turn arrives. Stepping the cursor is the whole of it
    // — the recorded snapshots already hold the engine's answer at each pick.
    const steps = Math.max(1, Math.min(picks, last - cursor));
    for (let step = 0; step < steps; step += 1) {
      cursor = clamp(cursor + 1, data.snapshots.length);
      if (stopAtUserTurn && stateAt(data, cursor).is_user_on_the_clock) break;
    }
    return [];
  },

  // Refused rather than faked: the recording contains the picks the simulator
  // actually made, so honouring an arbitrary choice would mean showing a
  // different player as drafted than the one that was clicked.
  pick: async (): Promise<unknown> => unavailable(),

  reset: async (): Promise<SimulationState> => {
    cursor = 0;
    return previewApi.createSimulation();
  },

  getPlayer: async (_id: string, playerUuid: string): Promise<PlayerDetail> => {
    const data = await load();
    const raw = data.players[playerUuid];
    if (raw === undefined) {
      throw new ApiError(
        "This player's detail was not part of the preview recording. The top of " +
          "the board is recorded in full.",
        404,
        "preview_missing_player",
      );
    }
    return playerDetailSchema.parse(raw);
  },

  comparePlayers: async (id: string, playerUuids: string[]): Promise<PlayerDetail[]> =>
    Promise.all(playerUuids.map((uuid) => previewApi.getPlayer(id, uuid))),

  /** Null, never a URL: preview must not claim a live connection. */
  eventStreamUrl: (): string | null => null,

  nflState: async (): Promise<never> => unavailable(),
  findSleeperUser: async (): Promise<never> => unavailable(),
  sleeperLeagues: async (): Promise<never> => unavailable(),
  sleeperDrafts: async (): Promise<never> => unavailable(),
  connectDraft: async (): Promise<never> => unavailable(),
};
