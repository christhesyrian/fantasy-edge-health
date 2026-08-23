"use client";

import { cn } from "@/lib/cn";
import { humanise, num, pct, signed } from "@/lib/format";
import type { Recommendation } from "@/lib/types";
import { RiskBadge } from "@/components/ui/RiskBadge";

const VERDICT_STYLE: Record<string, string> = {
  DRAFT_NOW: "text-[var(--color-go-400)] border-[var(--color-go-500)]",
  STRONG_VALUE: "text-[var(--color-go-400)] border-[var(--color-go-500)]",
  LIKELY_AVAILABLE_LATER: "text-[var(--color-hold-400)] border-[var(--color-hold-400)]",
  REACH: "text-[var(--color-warn-400)] border-[var(--color-warn-400)]",
  DISCOUNT_RISK: "text-[var(--color-warn-400)] border-[var(--color-warn-400)]",
  AVOID: "text-[var(--color-hazard-400)] border-[var(--color-hazard-500)]",
};

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  tone?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 border-l pl-2">
      <span className="rail-label">{label}</span>
      <span className={cn("tabular text-base leading-none", tone)}>{value}</span>
    </div>
  );
}

/**
 * The hero panel. One player, one verdict, and the arithmetic behind it.
 *
 * The score breakdown is not hidden behind a disclosure. The product's whole
 * claim is that a number can justify itself, and a manager with fifteen seconds
 * will not click to find out why.
 */
export function RecommendationPanel({
  pick,
  isOnTheClock,
  onDraft,
  onInspect,
  isDrafting,
}: {
  pick: Recommendation | null | undefined;
  isOnTheClock: boolean;
  onDraft: (playerUuid: string) => void;
  onInspect: (playerUuid: string) => void;
  isDrafting: boolean;
}) {
  if (!pick) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <p className="text-sm text-[var(--text-muted)]">
          No players available. The draft is complete.
        </p>
      </div>
    );
  }

  const maxMagnitude = Math.max(
    ...pick.components.map((component) => Math.abs(component.points)),
    1,
  );

  return (
    <div className="flex h-full flex-col">
      <div className="reticle m-2 shrink-0 border border-[var(--hairline-bright)] bg-[var(--surface-raised)] p-3">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <span className="rail-label text-[var(--accent)]">
              {isOnTheClock ? "You are on the clock" : "Best available"}
            </span>
            <button
              type="button"
              data-testid="hero-player"
              onClick={() => onInspect(pick.player_uuid)}
              className="mt-1 block truncate text-left"
            >
              <span className="display text-2xl leading-none text-[var(--text-primary)] hover:text-[var(--accent)]">
                {pick.name}
              </span>
            </button>
            <div className="mt-1.5 flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <span className="display border px-1.5 py-0.5 text-[0.7rem] text-[var(--text-primary)]">
                {pick.position}
              </span>
              <span>{pick.team ?? "FA"}</span>
              {pick.bye_week ? (
                <span className="text-[var(--text-muted)]">bye {pick.bye_week}</span>
              ) : null}
              {pick.tier ? (
                <span className="text-[var(--text-muted)]">tier {pick.tier}</span>
              ) : null}
            </div>
          </div>

          <div className="flex shrink-0 flex-col items-end gap-2">
            <span
              className={cn(
                "display border px-2.5 py-1 text-sm whitespace-nowrap",
                VERDICT_STYLE[pick.recommendation] ??
                  "border-[var(--hairline)] text-[var(--text-secondary)]",
              )}
            >
              {humanise(pick.recommendation)}
            </span>
            <div className="text-right">
              <div className="tabular text-3xl leading-none text-[var(--accent)]">
                {num(pick.overall_score, 1)}
              </div>
              <span className="rail-label">Draft score</span>
            </div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-4 gap-2">
          <Metric label="Proj" value={num(pick.projected_points, 0)} />
          <Metric label="ADP" value={num(pick.market_adp, 1)} />
          <Metric
            label="ADP value"
            value={signed(pick.adp_value, 0)}
            tone={
              (pick.adp_value ?? 0) > 0
                ? "text-[var(--color-go-400)]"
                : "text-[var(--text-secondary)]"
            }
          />
          <Metric
            label="Survives"
            value={pct(pick.next_pick_survival_probability)}
            tone={
              (pick.next_pick_survival_probability ?? 1) < 0.35
                ? "text-[var(--color-warn-400)]"
                : "text-[var(--text-primary)]"
            }
          />
        </div>
      </div>

      {/* Score decomposition. The components always sum to the headline. */}
      {/* The breakdown is the panel's main scroll region, so it gets the
          remaining height rather than whatever the hero leaves over. */}
      <div className="min-h-0 flex-1 overflow-auto px-3 pb-2">
        <span className="rail-label">Why this score</span>
        <ul className="mt-1.5 space-y-1">
          {pick.components.map((component) => {
            const width = (Math.abs(component.points) / maxMagnitude) * 100;
            const positive = component.points >= 0;
            return (
              <li key={component.name} className="group">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-[0.8125rem] text-[var(--text-secondary)]">
                    {component.label}
                  </span>
                  <span
                    className={cn(
                      "tabular text-[0.8125rem]",
                      positive
                        ? "text-[var(--color-go-400)]"
                        : "text-[var(--color-hazard-400)]",
                    )}
                  >
                    {signed(component.points, 1)}
                  </span>
                </div>
                <div className="mt-1 h-[3px] w-full bg-[var(--surface-row-alt)]">
                  <div
                    className={cn(
                      "h-full transition-[width] duration-500",
                      positive
                        ? "bg-[var(--color-go-500)]"
                        : "bg-[var(--color-hazard-500)]",
                    )}
                    style={{ width: `${width}%` }}
                  />
                </div>
                <p className="mt-0.5 text-[0.7rem] leading-snug text-[var(--text-muted)]">
                  {component.detail}
                </p>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="flex shrink-0 items-center gap-2 border-t p-3">
        <RiskBadge score={pick.health_risk} band={bandOf(pick.health_risk)} />
        <span className="text-[0.75rem] text-[var(--text-muted)]">
          {pct(pick.availability_estimate)} availability
        </span>
        <button
          type="button"
          disabled={!isOnTheClock || isDrafting}
          onClick={() => onDraft(pick.player_uuid)}
          className={cn(
            "display ml-auto px-4 py-2 text-sm transition-colors",
            isOnTheClock && !isDrafting
              ? "bg-[var(--accent)] text-[var(--color-pit-000)] hover:bg-[var(--color-sodium-300)]"
              : "cursor-not-allowed border text-[var(--text-muted)]",
          )}
        >
          {isDrafting ? "Drafting…" : isOnTheClock ? "Draft now" : "Not your pick"}
        </button>
      </div>
    </div>
  );
}

/** Recover the band from a score, since the row payload carries only the number. */
function bandOf(score: number | null | undefined): string | null {
  if (score === null || score === undefined) return null;
  if (score >= 70) return "SEVERE";
  if (score >= 45) return "ELEVATED";
  if (score >= 25) return "MODERATE";
  return "LOW";
}

export { bandOf };
