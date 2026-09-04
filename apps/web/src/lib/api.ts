/**
 * Typed API client.
 *
 * Every response is parsed through its zod schema, so contract drift surfaces
 * as a validation error naming the field rather than as a runtime surprise.
 */
import { z } from "zod";

import { ApiError } from "./apiError";
import { PREVIEW_MODE } from "./preview/mode";
import { previewApi } from "./preview/previewApi";
import {
  connectedDraftSchema,
  draftBoardSchema,
  draftStateSchema,
  healthStatusSchema,
  nflStateSchema,
  sessionStatusSchema,
  playerDetailSchema,
  simulationStateSchema,
  sleeperDraftSchema,
  sleeperLeagueSchema,
  sleeperUserSchema,
  type ConnectedDraft,
  type DraftBoard,
  type DraftState,
  type HealthStatus,
  type PlayerDetail,
  type SimulationState,
  type SleeperDraft,
  type SleeperLeague,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export { ApiError };

const errorBodySchema = z.object({
  error: z.string(),
  detail: z.string(),
  request_id: z.string().nullable().optional(),
});

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    // The session cookie is set by an API on a different origin, so it travels
    // only when the request asks for it. Without this the gate would refuse
    // every call from a deployed frontend while working perfectly on localhost.
    credentials: "include",
    headers: {
      ...(init?.body instanceof FormData ? {} : { "content-type": "application/json" }),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let code: string | undefined;
    let detail = `${response.status} ${response.statusText}`;
    let requestId: string | undefined;
    try {
      const parsed = errorBodySchema.safeParse(await response.json());
      if (parsed.success) {
        code = parsed.data.error;
        detail = parsed.data.detail;
        requestId = parsed.data.request_id ?? undefined;
      }
    } catch {
      // A non-JSON error body carries nothing more than the status line we
      // already captured above.
    }
    throw new ApiError(detail, response.status, code, requestId);
  }

  return schema.parse(await response.json());
}

export interface SimulationOptions {
  teamCount: number;
  userDraftSlot: number;
  scoringFormat: string;
  seed: number;
  temperature?: number;
}

const liveApi = {
  health: () => request("/api/v1/health", healthStatusSchema),

  // ---- access gate --------------------------------------------------------

  sessionStatus: () => request("/api/v1/auth/session", sessionStatusSchema),

  signIn: (password: string) =>
    request("/api/v1/auth/session", sessionStatusSchema, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  signOut: () =>
    request("/api/v1/auth/session", sessionStatusSchema, { method: "DELETE" }),

  createSimulation: (options: SimulationOptions) =>
    request("/api/v1/simulations", simulationStateSchema, {
      method: "POST",
      body: JSON.stringify({
        team_count: options.teamCount,
        user_draft_slot: options.userDraftSlot,
        scoring_format: options.scoringFormat,
        seed: options.seed,
        temperature: options.temperature ?? 3.0,
      }),
    }),

  getSimulation: (id: string) =>
    request(`/api/v1/simulations/${id}`, simulationStateSchema),

  /**
   * The canonical board, and the re-sync read: after a dropped event stream a
   * client re-reads this rather than trying to replay what it missed.
   *
   * Lives under `/drafts` because a board is the same thing whether picks
   * arrive from a Sleeper poller or the simulator.
   */
  getBoard: (id: string, depth = 120) =>
    request(`/api/v1/drafts/${id}/board?depth=${depth}`, draftBoardSchema),

  getDraftState: (id: string) => request(`/api/v1/drafts/${id}`, draftStateSchema),

  advance: (id: string, picks: number, stopAtUserTurn = true) =>
    request(`/api/v1/simulations/${id}/advance`, z.array(z.unknown()), {
      method: "POST",
      body: JSON.stringify({ picks, stop_at_user_turn: stopAtUserTurn }),
    }),

  pick: (id: string, playerUuid: string) =>
    request(`/api/v1/simulations/${id}/pick`, z.unknown(), {
      method: "POST",
      body: JSON.stringify({ player_uuid: playerUuid }),
    }),

  reset: (id: string) =>
    request(`/api/v1/simulations/${id}/reset`, simulationStateSchema, {
      method: "POST",
    }),

  getPlayer: (id: string, playerUuid: string) =>
    request(`/api/v1/drafts/${id}/players/${playerUuid}`, playerDetailSchema),

  comparePlayers: (id: string, playerUuids: string[]) =>
    request(
      `/api/v1/drafts/${id}/compare?${playerUuids
        .map((uuid) => `player_uuid=${encodeURIComponent(uuid)}`)
        .join("&")}`,
      z.array(playerDetailSchema),
    ),

  eventStreamUrl: (id: string) => `${API_BASE}/api/v1/drafts/${id}/events`,

  // ---- Sleeper onboarding -------------------------------------------------

  nflState: () => request("/api/v1/sleeper/state", nflStateSchema),

  findSleeperUser: (username: string) =>
    request(
      `/api/v1/sleeper/users/${encodeURIComponent(username)}`,
      sleeperUserSchema.nullable(),
    ),

  sleeperLeagues: (userId: string) =>
    request(
      `/api/v1/sleeper/users/${encodeURIComponent(userId)}/leagues`,
      z.array(sleeperLeagueSchema),
    ),

  sleeperDrafts: (leagueId: string, userId?: string) =>
    request(
      `/api/v1/sleeper/leagues/${encodeURIComponent(leagueId)}/drafts` +
        (userId ? `?user_id=${encodeURIComponent(userId)}` : ""),
      z.array(sleeperDraftSchema),
    ),

  /** Connect a real draft. This is the call with side effects. */
  connectDraft: (input: {
    leagueId: string;
    draftId: string;
    userId?: string;
    follow?: boolean;
  }) =>
    request("/api/v1/leagues/connect", connectedDraftSchema, {
      method: "POST",
      body: JSON.stringify({
        league_id: input.leagueId,
        draft_id: input.draftId,
        user_id: input.userId ?? null,
        follow: input.follow ?? true,
      }),
    }),
};

/**
 * What every screen calls.
 *
 * Widened in exactly one place: the live client always has an event-stream URL,
 * while preview has none and must not pretend otherwise. Declaring that here
 * rather than casting means the preview adapter is type-checked against the
 * real client, so it cannot quietly drift out of contract.
 */
export type DraftApi = Omit<typeof liveApi, "eventStreamUrl"> & {
  eventStreamUrl: (id: string) => string | null;
};

/**
 * The active backend.
 *
 * `PREVIEW_MODE` is an inlined build-time constant, so this is decided when the
 * bundle is built, not per request. A build made without the flag always
 * resolves to `liveApi`, and nothing a user does can switch it — which is the
 * property that matters: recorded data can never surface on the production
 * path.
 */
export const api: DraftApi = PREVIEW_MODE ? previewApi : liveApi;

export type {
  ConnectedDraft,
  DraftBoard,
  DraftState,
  HealthStatus,
  PlayerDetail,
  SimulationState,
  SleeperDraft,
  SleeperLeague,
};
