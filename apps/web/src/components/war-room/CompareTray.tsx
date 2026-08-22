"use client";

import { useQuery } from "@tanstack/react-query";

import { RiskBadge } from "@/components/ui/RiskBadge";
import { api } from "@/lib/api";
import { num, pct } from "@/lib/format";

/**
 * Side-by-side comparison of two to four players.
 *
 * A tray rather than a modal: comparison is something a manager does *while*
 * watching the board, not instead of watching it.
 */
export function CompareTray({
  simulationId,
  playerUuids,
  onClear,
  onRemove,
  onInspect,
}: {
  simulationId: string;
  playerUuids: string[];
  onClear: () => void;
  onRemove: (uuid: string) => void;
  onInspect: (uuid: string) => void;
}) {
  const enabled = playerUuids.length >= 2;
  const { data: players } = useQuery({
    queryKey: ["compare", simulationId, playerUuids],
    queryFn: () => api.comparePlayers(simulationId, playerUuids),
    enabled,
  });

  if (playerUuids.length === 0) return null;

  return (
    <div className="border-t bg-[var(--surface-panel)]">
      <div className="flex items-center gap-3 border-b px-3 py-1.5">
        <span className="rail-label text-[var(--color-signal-300)]">
          Compare · {playerUuids.length}
        </span>
        {!enabled ? (
          <span className="text-[0.75rem] text-[var(--text-muted)]">
            Select one more player to compare.
          </span>
        ) : null}
        <button
          type="button"
          onClick={onClear}
          className="rail-label ml-auto hover:text-[var(--accent)]"
        >
          Clear
        </button>
      </div>

      {players && players.length >= 2 ? (
        <div
          className="grid gap-px bg-[var(--hairline)]"
          style={{ gridTemplateColumns: `repeat(${players.length}, minmax(0, 1fr))` }}
        >
          {players.map((player) => (
            <div key={player.player_uuid} className="bg-[var(--surface-panel)] p-2.5">
              <div className="flex items-start justify-between gap-2">
                <button
                  type="button"
                  onClick={() => onInspect(player.player_uuid)}
                  className="min-w-0 truncate text-left text-[0.875rem] text-[var(--text-primary)] hover:text-[var(--accent)]"
                >
                  {player.name}
                </button>
                <button
                  type="button"
                  aria-label={`Remove ${player.name} from comparison`}
                  onClick={() => onRemove(player.player_uuid)}
                  className="shrink-0 text-[var(--text-muted)] hover:text-[var(--color-hazard-400)]"
                >
                  ×
                </button>
              </div>
              <dl className="mt-1.5 space-y-0.5 text-[0.75rem]">
                {[
                  ["Pos", `${player.position} · ${player.team ?? "FA"}`],
                  ["Proj", num(player.projected_points, 1)],
                  ["ADP", num(player.market_adp, 1)],
                  ["Age", num(player.age, 0)],
                  [
                    "Availability",
                    player.health ? pct(player.health.availability_estimate) : "—",
                  ],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-2">
                    <dt className="text-[var(--text-muted)]">{label}</dt>
                    <dd className="tabular text-[var(--text-secondary)]">{value}</dd>
                  </div>
                ))}
                <div className="flex justify-between gap-2 pt-0.5">
                  <dt className="text-[var(--text-muted)]">Risk</dt>
                  <dd>
                    <RiskBadge
                      score={player.health?.risk_score}
                      band={player.health?.risk_band}
                    />
                  </dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
