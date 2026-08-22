"use client";

import { cn } from "@/lib/cn";
import type { TeamRoster } from "@/lib/types";

/**
 * The roster, arranged by starting slot.
 *
 * An empty starting slot is drawn as a gap you can see from across the room,
 * because "I still need a tight end" is the question this panel exists to
 * answer at a glance.
 */
export function MyRoster({
  roster,
  onInspect,
}: {
  roster: TeamRoster | null | undefined;
  onInspect: (uuid: string) => void;
}) {
  if (!roster) {
    return (
      <p className="p-4 text-sm text-[var(--text-muted)]">
        No roster yet. Your picks will appear here.
      </p>
    );
  }

  return (
    <div className="flex flex-col">
      <ul className="divide-y">
        {roster.lineup.map((slot, index) => (
          <li
            key={`${slot.slot}-${index}`}
            className={cn(
              "flex items-center gap-2.5 px-3 py-1.5",
              !slot.player &&
                "bg-[color-mix(in_oklab,var(--color-warn-400)_8%,transparent)]",
            )}
          >
            <span
              className={cn(
                "display w-14 shrink-0 border px-1.5 py-0.5 text-center text-[0.7rem]",
                slot.player
                  ? "text-[var(--text-secondary)]"
                  : "border-[var(--color-warn-400)] text-[var(--color-warn-400)]",
              )}
            >
              {slot.slot}
            </span>
            {slot.player ? (
              <button
                type="button"
                onClick={() => onInspect(slot.player!.player_uuid)}
                className="min-w-0 flex-1 truncate text-left text-[0.8125rem] text-[var(--text-primary)] hover:text-[var(--accent)]"
              >
                {slot.player.name}
                <span className="ml-1.5 text-[0.7rem] text-[var(--text-muted)]">
                  {slot.player.position} · {slot.player.team ?? "FA"}
                </span>
              </button>
            ) : (
              <span className="flex-1 text-[0.8125rem] text-[var(--color-warn-400)]">
                Unfilled
              </span>
            )}
          </li>
        ))}
      </ul>

      {roster.bench.length > 0 ? (
        <div className="border-t">
          <div className="px-3 pt-2">
            <span className="rail-label">Bench · {roster.bench.length}</span>
          </div>
          <ul className="px-3 pt-1 pb-2">
            {roster.bench.map((player) => (
              <li key={player.player_uuid}>
                <button
                  type="button"
                  onClick={() => onInspect(player.player_uuid)}
                  className="w-full truncate py-0.5 text-left text-[0.8125rem] text-[var(--text-secondary)] hover:text-[var(--accent)]"
                >
                  {player.name}
                  <span className="ml-1.5 text-[0.7rem] text-[var(--text-muted)]">
                    {player.position}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
