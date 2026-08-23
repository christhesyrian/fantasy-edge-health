"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { bandOf } from "@/components/war-room/RecommendationPanel";
import { RiskBadge } from "@/components/ui/RiskBadge";
import { cn } from "@/lib/cn";
import { num, pct, signed } from "@/lib/format";
import type { Recommendation } from "@/lib/types";

type Filter =
  "ALL" | "QB" | "RB" | "WR" | "TE" | "FLEX" | "VALUE" | "HEALTHY" | "FAVOURITES";

const FILTERS: { key: Filter; label: string; hint: string }[] = [
  { key: "ALL", label: "All", hint: "Every available player" },
  { key: "QB", label: "QB", hint: "Quarterbacks" },
  { key: "RB", label: "RB", hint: "Running backs" },
  { key: "WR", label: "WR", hint: "Wide receivers" },
  { key: "TE", label: "TE", hint: "Tight ends" },
  { key: "FLEX", label: "Flex", hint: "RB, WR and TE" },
  { key: "VALUE", label: "Value", hint: "Falling past their ADP" },
  { key: "HEALTHY", label: "Healthy", hint: "Low measured availability risk" },
  { key: "FAVOURITES", label: "★", hint: "Players you have starred" },
];

const VERDICT_ABBREV: Record<string, { text: string; className: string }> = {
  DRAFT_NOW: { text: "TAKE", className: "text-[var(--color-go-400)]" },
  STRONG_VALUE: { text: "VALUE", className: "text-[var(--color-go-400)]" },
  LIKELY_AVAILABLE_LATER: { text: "WAIT", className: "text-[var(--color-hold-400)]" },
  REACH: { text: "REACH", className: "text-[var(--color-warn-400)]" },
  DISCOUNT_RISK: { text: "RISK", className: "text-[var(--color-warn-400)]" },
  AVOID: { text: "AVOID", className: "text-[var(--color-hazard-400)]" },
};

function matches(
  row: Recommendation,
  filter: Filter,
  favourites: ReadonlySet<string>,
): boolean {
  switch (filter) {
    case "ALL":
      return true;
    case "FAVOURITES":
      return favourites.has(row.player_uuid);
    case "FLEX":
      return ["RB", "WR", "TE"].includes(row.position);
    case "VALUE":
      return (row.adp_value ?? 0) >= 8;
    case "HEALTHY":
      return (
        row.health_risk !== null &&
        row.health_risk !== undefined &&
        row.health_risk <= 20
      );
    default:
      return row.position === filter;
  }
}

/**
 * The board. Deliberately a dense table rather than cards: a manager is
 * comparing twenty players on six numbers, and cards make that comparison
 * impossible.
 */
export type { Filter };

export function BestAvailable({
  rows,
  selectedUuid,
  comparing,
  onSelect,
  onInspect,
  onToggleCompare,
  onDraft,
  isOnTheClock,
  favourites,
  onToggleFavourite,
  filter,
  onFilterChange,
}: {
  rows: Recommendation[];
  selectedUuid: string | null;
  comparing: string[];
  onSelect: (uuid: string) => void;
  onInspect: (uuid: string) => void;
  onToggleCompare: (uuid: string) => void;
  onDraft: (uuid: string) => void;
  isOnTheClock: boolean;
  favourites: ReadonlySet<string>;
  onToggleFavourite: (uuid: string) => void;
  filter: Filter;
  onFilterChange: (filter: Filter) => void;
}) {
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter(
      (row) =>
        matches(row, filter, favourites) &&
        (needle === "" ||
          row.name.toLowerCase().includes(needle) ||
          (row.team ?? "").toLowerCase().includes(needle)),
    );
  }, [rows, filter, query, favourites]);

  // Reveal whatever is selected. The palette and the arrow keys can both land on
  // a player far down a hundred-row table, and a selection you cannot see reads
  // as the command having done nothing at all. "nearest" so an already-visible
  // row does not jump.
  const selectedRowRef = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    selectedRowRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [selectedUuid]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b px-2 py-2">
        {FILTERS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            title={entry.hint}
            onClick={() => onFilterChange(entry.key)}
            aria-pressed={filter === entry.key}
            className={cn(
              "display border px-2 py-1 text-[0.7rem] transition-colors",
              filter === entry.key
                ? "border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_16%,transparent)] text-[var(--accent)]"
                : "text-[var(--text-muted)] hover:border-[var(--color-pit-400)] hover:text-[var(--text-secondary)]",
            )}
          >
            {entry.label}
          </button>
        ))}
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search player…"
          aria-label="Search players"
          className="ml-auto w-40 border bg-[var(--surface-base)] px-2 py-1 text-[0.8125rem] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full table-fixed border-collapse text-[0.78rem]">
          <thead className="sticky top-0 z-10 bg-[var(--surface-panel)]">
            <tr className="border-b">
              {[
                { label: "#", align: "text-right w-[2.2rem]" },
                { label: "Player", align: "text-left" },
                { label: "Pos", align: "text-left w-[2.4rem]" },
                { label: "Score", align: "text-right w-[3.1rem]" },
                { label: "Proj", align: "text-right w-[2.8rem]" },
                { label: "ADP", align: "text-right w-[2.6rem]" },
                { label: "Val", align: "text-right w-[2.6rem]" },
                { label: "Risk", align: "text-left w-[3.6rem]" },
                { label: "Surv", align: "text-right w-[2.9rem]" },
                { label: "Call", align: "text-left w-[3.1rem]" },
                { label: "", align: "w-[4.2rem]" },
              ].map((column) => (
                <th
                  key={column.label}
                  scope="col"
                  className={cn("rail-label px-1.5 py-1.5", column.align)}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => {
              const verdict = VERDICT_ABBREV[row.recommendation];
              const isSelected = row.player_uuid === selectedUuid;
              const isComparing = comparing.includes(row.player_uuid);
              const survival = row.next_pick_survival_probability;

              return (
                <tr
                  key={row.player_uuid}
                  ref={isSelected ? selectedRowRef : undefined}
                  data-selected={isSelected ? "true" : undefined}
                  onClick={() => onSelect(row.player_uuid)}
                  onDoubleClick={() => onInspect(row.player_uuid)}
                  style={{ ["--i" as string]: Math.min(index, 30) }}
                  className={cn(
                    "rise cursor-pointer border-b border-[color-mix(in_oklab,var(--hairline)_45%,transparent)] transition-colors",
                    index % 2 === 1 && "bg-[var(--surface-row-alt)]",
                    row.recommendation === "AVOID" && "hazard-stripe",
                    isSelected
                      ? "bg-[color-mix(in_oklab,var(--accent)_22%,transparent)]"
                      : "hover:bg-[color-mix(in_oklab,var(--accent)_7%,transparent)]",
                  )}
                >
                  <td className="tabular px-1.5 py-1.5 text-right text-[var(--text-secondary)]">
                    {row.model_rank}
                  </td>
                  <td className="px-1.5 py-1.5">
                    <span
                      className={cn(
                        "truncate",
                        isSelected
                          ? "text-[var(--accent)]"
                          : "text-[var(--text-primary)]",
                      )}
                    >
                      {row.name}
                    </span>
                    <span className="ml-1.5 text-[0.7rem] text-[var(--text-muted)]">
                      {row.team ?? "FA"}
                    </span>
                  </td>
                  <td className="px-1.5 py-1.5">
                    <span className="display text-[0.7rem] text-[var(--text-secondary)]">
                      {row.position}
                    </span>
                  </td>
                  <td className="tabular px-1.5 py-1.5 text-right text-[0.86rem] font-semibold text-[var(--accent)]">
                    {num(row.overall_score, 1)}
                  </td>
                  <td className="tabular px-1.5 py-1.5 text-right text-[var(--text-secondary)]">
                    {num(row.projected_points, 0)}
                  </td>
                  <td className="tabular px-1.5 py-1.5 text-right text-[var(--text-secondary)]">
                    {num(row.market_adp, 0)}
                  </td>
                  <td
                    className={cn(
                      "tabular px-1.5 py-1.5 text-right",
                      (row.adp_value ?? 0) > 0
                        ? "text-[var(--color-go-400)]"
                        : "text-[var(--text-muted)]",
                    )}
                  >
                    {signed(row.adp_value, 0)}
                  </td>
                  <td className="px-1.5 py-1.5">
                    <RiskBadge score={row.health_risk} band={bandOf(row.health_risk)} />
                  </td>
                  <td
                    className={cn(
                      "tabular px-1.5 py-1.5 text-right",
                      survival !== null && survival !== undefined && survival < 0.35
                        ? "text-[var(--color-warn-400)]"
                        : "text-[var(--text-secondary)]",
                    )}
                  >
                    {pct(survival)}
                  </td>
                  <td className="px-1.5 py-1.5">
                    {verdict ? (
                      <span
                        className={cn(
                          "display inline-block border px-1 py-0.5 text-[0.6rem] leading-none",
                          verdict.className,
                        )}
                      >
                        {verdict.text}
                      </span>
                    ) : (
                      <span className="text-[var(--text-muted)]">—</span>
                    )}
                  </td>
                  <td className="px-1.5 py-1.5">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        aria-label={`${
                          favourites.has(row.player_uuid) ? "Unstar" : "Star"
                        } ${row.name}`}
                        aria-pressed={favourites.has(row.player_uuid)}
                        onClick={(event) => {
                          event.stopPropagation();
                          onToggleFavourite(row.player_uuid);
                        }}
                        className={cn(
                          "px-1 text-[0.75rem] leading-none transition-colors",
                          favourites.has(row.player_uuid)
                            ? "text-[var(--accent)]"
                            : "text-[var(--color-pit-500)] hover:text-[var(--text-secondary)]",
                        )}
                      >
                        ★
                      </button>
                      <button
                        type="button"
                        aria-label={`Compare ${row.name}`}
                        aria-pressed={isComparing}
                        onClick={(event) => {
                          event.stopPropagation();
                          onToggleCompare(row.player_uuid);
                        }}
                        className={cn(
                          "border px-1.5 py-0.5 text-[0.65rem] transition-colors",
                          isComparing
                            ? "border-[var(--color-signal-400)] text-[var(--color-signal-300)]"
                            : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]",
                        )}
                      >
                        vs
                      </button>
                      {isOnTheClock ? (
                        <button
                          type="button"
                          aria-label={`Draft ${row.name}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            onDraft(row.player_uuid);
                          }}
                          className="border border-[var(--accent)] px-1.5 py-0.5 text-[0.65rem] text-[var(--accent)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--color-pit-000)]"
                        >
                          +
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
            {visible.length === 0 ? (
              <tr>
                <td
                  colSpan={11}
                  className="px-4 py-10 text-center text-sm text-[var(--text-muted)]"
                >
                  No players match this filter.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
