/**
 * Wire types, validated at the boundary.
 *
 * These mirror the FastAPI contracts in `src/fhe/api/schemas.py`. Responses are
 * parsed with zod rather than cast, so a backend change surfaces here as a loud
 * validation error naming the field, instead of `undefined` appearing three
 * components deep.
 */
import { z } from "zod";

export const provenanceSchema = z.object({
  source: z.string(),
  observed_at: z.string().nullable().optional(),
  freshness: z.string(),
});

export const scoreComponentSchema = z.object({
  name: z.string(),
  label: z.string(),
  points: z.number(),
  detail: z.string(),
});

export const healthSchema = z.object({
  risk_score: z.number(),
  raw_score: z.number(),
  risk_band: z.string(),
  availability_estimate: z.number(),
  confidence: z.number(),
  practice_trajectory: z.string(),
  model_version: z.string(),
  components: z.array(scoreComponentSchema).default([]),
  limitations: z.array(z.string()).default([]),
  designation: z.string().nullable().optional(),
  body_region: z.string().nullable().optional(),
  raw_body_part: z.string().nullable().optional(),
});

export const injuryEventSchema = z.object({
  season: z.number(),
  week: z.number().nullable(),
  body_region: z.string(),
  raw_descriptor: z.string().nullable(),
  designation: z.string(),
  games_missed: z.number().nullable().optional(),
  observed_at: z.string().nullable().optional(),
});

export const workloadSchema = z.object({
  season: z.number().nullable().optional(),
  games_played: z.number().nullable().optional(),
  snaps_per_game: z.number().nullable().optional(),
  carries_per_game: z.number().nullable().optional(),
  targets_per_game: z.number().nullable().optional(),
  touches_per_game: z.number().nullable().optional(),
});

export const playerSummarySchema = z.object({
  player_uuid: z.string(),
  name: z.string(),
  position: z.string(),
  team: z.string().nullable().optional(),
  age: z.number().nullable().optional(),
  years_experience: z.number().nullable().optional(),
  bye_week: z.number().nullable().optional(),
});

export const playerDetailSchema = playerSummarySchema.extend({
  jersey_number: z.number().nullable().optional(),
  height_inches: z.number().nullable().optional(),
  weight_pounds: z.number().nullable().optional(),
  college: z.string().nullable().optional(),
  identity_method: z.string().nullable().optional(),
  identity_confidence: z.number().nullable().optional(),
  external_ids: z.record(z.string(), z.string()).default({}),
  health: healthSchema.nullable().optional(),
  injury_history: z.array(injuryEventSchema).default([]),
  workload: workloadSchema.nullable().optional(),
  projected_points: z.number().nullable().optional(),
  market_adp: z.number().nullable().optional(),
  adp_stdev: z.number().nullable().optional(),
  projection_source: z.string().nullable().optional(),
  adp_source: z.string().nullable().optional(),
  is_demo: z.boolean().default(false),
});

export const recommendationSchema = z.object({
  player_uuid: z.string(),
  name: z.string(),
  position: z.string(),
  team: z.string().nullable().optional(),
  overall_score: z.number(),
  model_rank: z.number(),
  recommendation: z.string(),
  market_adp: z.number().nullable().optional(),
  adp_value: z.number().nullable().optional(),
  projected_points: z.number().nullable().optional(),
  vorp: z.number().nullable().optional(),
  tier: z.number().nullable().optional(),
  health_risk: z.number().nullable().optional(),
  availability_estimate: z.number().nullable().optional(),
  next_pick_survival_probability: z.number().nullable().optional(),
  take_now_probability: z.number().nullable().optional(),
  bye_week: z.number().nullable().optional(),
  components: z.array(scoreComponentSchema).default([]),
  reasons: z.array(z.string()).default([]),
});

export const scarcitySchema = z.object({
  position: z.string(),
  available_starters: z.number(),
  tier_size_remaining: z.number(),
  next_tier_dropoff: z.number().nullable().optional(),
  expected_gone_before_next_pick: z.number(),
  scarcity_index: z.number(),
});

export const alertSchema = z.object({
  key: z.string(),
  level: z.string(),
  message: z.string(),
  position: z.string().nullable().optional(),
  player_uuid: z.string().nullable().optional(),
});

export const rosterSlotSchema = z.object({
  slot: z.string(),
  is_starter: z.boolean(),
  player: playerSummarySchema.nullable(),
});

export const teamRosterSchema = z.object({
  draft_slot: z.number(),
  roster_id: z.number().nullable().optional(),
  display_name: z.string().nullable().optional(),
  is_user: z.boolean().default(false),
  lineup: z.array(rosterSlotSchema).default([]),
  bench: z.array(playerSummarySchema).default([]),
  unfilled_starting_slots: z.array(z.string()).default([]),
});

export const draftPickSchema = z.object({
  pick_no: z.number(),
  round_number: z.number(),
  draft_slot: z.number(),
  roster_id: z.number().nullable().optional(),
  player: playerSummarySchema.nullable(),
  is_keeper: z.boolean().default(false),
});

export const leagueSettingsSchema = z.object({
  team_count: z.number(),
  scoring_format: z.string(),
  draft_type: z.string(),
  rounds: z.number(),
  roster_positions: z.array(z.string()),
  user_draft_slot: z.number().nullable().optional(),
  is_superflex: z.boolean().default(false),
  replacement_ranks: z.record(z.string(), z.number()).default({}),
});

export const draftBoardSchema = z.object({
  draft_id: z.string(),
  is_demo: z.boolean(),
  status: z.string(),
  current_pick: z.number().nullable().optional(),
  current_round: z.number().nullable().optional(),
  next_user_pick: z.number().nullable().optional(),
  picks_until_user_turn: z.number().nullable().optional(),
  is_user_on_the_clock: z.boolean().default(false),
  league: leagueSettingsSchema,
  recommendations: z.array(recommendationSchema).default([]),
  best_pick: recommendationSchema.nullable().optional(),
  safest_pick: recommendationSchema.nullable().optional(),
  highest_upside: recommendationSchema.nullable().optional(),
  best_value: recommendationSchema.nullable().optional(),
  scarcity: z.array(scarcitySchema).default([]),
  alerts: z.array(alertSchema).default([]),
  my_roster: teamRosterSchema.nullable().optional(),
  recent_picks: z.array(draftPickSchema).default([]),
  computed_at: z.string(),
  computation_ms: z.number().nullable().optional(),
  provenance: z.array(provenanceSchema).default([]),
});

export const simulationStateSchema = z.object({
  simulation_id: z.string(),
  is_demo: z.boolean().default(true),
  seed: z.number(),
  status: z.string(),
  pick_count: z.number(),
  total_picks: z.number(),
  is_complete: z.boolean(),
  is_user_on_the_clock: z.boolean(),
  created_at: z.string(),
});

export const healthStatusSchema = z.object({
  status: z.string(),
  version: z.string(),
  environment: z.string(),
  checks: z.record(z.string(), z.string()).default({}),
  degradations: z.array(z.string()).default([]),
});

export type Provenance = z.infer<typeof provenanceSchema>;
export type ScoreComponent = z.infer<typeof scoreComponentSchema>;
export type Health = z.infer<typeof healthSchema>;
export type InjuryEvent = z.infer<typeof injuryEventSchema>;
export type Workload = z.infer<typeof workloadSchema>;
export type PlayerSummary = z.infer<typeof playerSummarySchema>;
export type PlayerDetail = z.infer<typeof playerDetailSchema>;
export type Recommendation = z.infer<typeof recommendationSchema>;
export type Scarcity = z.infer<typeof scarcitySchema>;
export type DraftAlert = z.infer<typeof alertSchema>;
export type TeamRoster = z.infer<typeof teamRosterSchema>;
export type DraftPick = z.infer<typeof draftPickSchema>;
export type DraftBoard = z.infer<typeof draftBoardSchema>;
export type SimulationState = z.infer<typeof simulationStateSchema>;
export type HealthStatus = z.infer<typeof healthStatusSchema>;

/** Real-time transport state shown in the top rail. */
export type ConnectionState = "LIVE" | "RECONNECTING" | "STALE" | "DISCONNECTED";
