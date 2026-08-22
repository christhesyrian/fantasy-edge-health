"use client";

import { cn } from "@/lib/cn";
import type { DraftPick } from "@/lib/types";

/**
 * Recent picks, newest first.
 *
 * The newest row flashes once on arrival so a pick registers peripherally
 * without the manager having to watch the panel.
 */
export function DraftTicker({
  picks,
  userDraftSlot,
}: {
  picks: DraftPick[];
  userDraftSlot: number | null | undefined;
}) {
  if (picks.length === 0) {
    return (
      <p className="p-4 text-sm text-[var(--text-muted)]">
        No picks yet. Advance the draft to begin.
      </p>
    );
  }

  return (
    <ul className="divide-y">
      {picks.map((pick, index) => {
        const isUser = pick.draft_slot === userDraftSlot;
        return (
          <li
            key={pick.pick_no}
            className={cn(
              "flex items-center gap-2 px-3 py-1.5 text-[0.8125rem]",
              index === 0 && "land",
              isUser && "border-l-2 border-l-[var(--accent)]",
            )}
          >
            <span className="tabular w-12 shrink-0 text-[var(--text-muted)]">
              {pick.round_number}.
              {String(((pick.pick_no - 1) % 100) + 1).padStart(2, "0")}
            </span>
            <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">
              {pick.player?.name ?? "Unknown player"}
            </span>
            <span className="display shrink-0 text-[0.7rem] text-[var(--text-muted)]">
              {pick.player?.position ?? "—"}
            </span>
            {isUser ? (
              <span className="display shrink-0 text-[0.65rem] text-[var(--accent)]">
                YOU
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
