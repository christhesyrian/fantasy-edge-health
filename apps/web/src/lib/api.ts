/**
 * Typed API client.
 *
 * Every response is parsed through its zod schema, so contract drift surfaces
 * as a validation error naming the field rather than as a runtime surprise.
 */
import { z } from "zod";

import {
  draftBoardSchema,
  healthStatusSchema,
  playerDetailSchema,
  simulationStateSchema,
  type DraftBoard,
  type HealthStatus,
  type PlayerDetail,
  type SimulationState,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** An API call that failed, carrying the server's structured error. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

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

export const api = {
  health: () => request("/api/v1/health", healthStatusSchema),

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
   */
  getBoard: (id: string, depth = 120) =>
    request(`/api/v1/simulations/${id}/board?depth=${depth}`, draftBoardSchema),

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
    request(`/api/v1/simulations/${id}/players/${playerUuid}`, playerDetailSchema),

  comparePlayers: (id: string, playerUuids: string[]) =>
    request(
      `/api/v1/simulations/${id}/compare?${playerUuids
        .map((uuid) => `player_uuid=${encodeURIComponent(uuid)}`)
        .join("&")}`,
      z.array(playerDetailSchema),
    ),

  eventStreamUrl: (id: string) => `${API_BASE}/api/v1/simulations/${id}/events`,
};

export type { DraftBoard, HealthStatus, PlayerDetail, SimulationState };
