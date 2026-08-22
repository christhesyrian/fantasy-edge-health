"use client";

import { cn } from "@/lib/cn";
import { num, pct, signed } from "@/lib/format";
import type { DraftBoard, Recommendation } from "@/lib/types";

/**
 * The three alternatives to the top recommendation.
 *
 * Each answers a different question a manager actually asks: what is safest,
 * what has the most room to grow, and where is the market wrong. They are shown
 * together so the trade-off between them is visible rather than sequential.
 */
export function HeadlinePicks({
  board,
  onInspect,
  onSelect,
}: {
  board: DraftBoard;
  onInspect: (uuid: string) => void;
  onSelect: (uuid: string) => void;
}) {
  const cards: {
    key: string;
    label: string;
    rationale: string;
    pick: Recommendation | null | undefined;
    metric: (pick: Recommendation) => string;
    accent: string;
  }[] = [
    {
      key: "safest",
      label: "Safest",
      rationale: "Lowest measured availability risk with enough data to trust it",
      pick: board.safest_pick,
      metric: (pick) => `${pct(pick.availability_estimate)} available`,
      accent: "var(--color-go-400)",
    },
    {
      key: "upside",
      label: "Highest upside",
      rationale: "Young or early-career, ranked on raw value before risk",
      pick: board.highest_upside,
      metric: (pick) => `${num(pick.vorp, 0)} over replacement`,
      accent: "var(--color-signal-400)",
    },
    {
      key: "value",
      label: "Best value",
      rationale: "Largest gap between where the market drafts him and our rank",
      pick: board.best_value,
      metric: (pick) => `${signed(pick.adp_value, 0)} vs ADP`,
      accent: "var(--accent)",
    },
  ];

  return (
    <div className="grid grid-cols-3 gap-1.5 p-1.5">
      {cards.map((card) => (
        <button
          key={card.key}
          type="button"
          title={card.rationale}
          disabled={!card.pick}
          onClick={() => card.pick && onSelect(card.pick.player_uuid)}
          onDoubleClick={() => card.pick && onInspect(card.pick.player_uuid)}
          className={cn(
            "border bg-[var(--surface-raised)] px-2 py-1.5 text-left transition-colors",
            card.pick
              ? "hover:border-[var(--accent)]"
              : "cursor-not-allowed opacity-50",
          )}
          style={{ borderLeftColor: card.accent, borderLeftWidth: 2 }}
        >
          <span
            className="rail-label block text-[0.6rem]"
            style={{ color: card.accent }}
          >
            {card.label}
          </span>
          {card.pick ? (
            <>
              <div className="truncate text-[0.8125rem] leading-tight text-[var(--text-primary)]">
                {card.pick.name}
              </div>
              <div className="tabular truncate text-[0.65rem] leading-tight text-[var(--text-muted)]">
                {card.pick.position} · {card.metric(card.pick)}
              </div>
            </>
          ) : (
            <div className="text-[0.75rem] text-[var(--text-muted)]">Not available</div>
          )}
        </button>
      ))}
    </div>
  );
}
