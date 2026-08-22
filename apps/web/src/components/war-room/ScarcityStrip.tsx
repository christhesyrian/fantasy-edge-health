"use client";

import { cn } from "@/lib/cn";
import { num } from "@/lib/format";
import type { Scarcity } from "@/lib/types";

/**
 * Positional scarcity.
 *
 * The bar is the share of the current tier expected to be gone before your next
 * pick, which is the only form of "scarcity" that changes a decision. A full bar
 * means the tier will not survive your wait.
 */
export function ScarcityStrip({ scarcity }: { scarcity: Scarcity[] }) {
  const ordered = [...scarcity].sort((a, b) => b.scarcity_index - a.scarcity_index);

  return (
    <ul className="divide-y">
      {ordered.map((entry) => {
        const urgent = entry.scarcity_index >= 0.7;
        return (
          <li key={entry.position} className="px-3 py-1.5">
            <div className="flex items-baseline justify-between">
              <span className="display text-[0.8rem] text-[var(--text-primary)]">
                {entry.position}
              </span>
              <span className="tabular text-[0.7rem] text-[var(--text-muted)]">
                {entry.tier_size_remaining} in tier · {entry.available_starters}{" "}
                startable
              </span>
            </div>
            <div className="mt-1 h-1.5 w-full bg-[var(--surface-row-alt)]">
              <div
                className={cn(
                  "h-full transition-[width] duration-500",
                  urgent
                    ? "bg-[var(--color-hazard-500)]"
                    : "bg-[var(--color-sodium-500)]",
                )}
                style={{ width: `${Math.min(100, entry.scarcity_index * 100)}%` }}
              />
            </div>
            {entry.next_tier_dropoff ? (
              <p className="mt-0.5 text-[0.65rem] text-[var(--text-muted)]">
                {num(entry.next_tier_dropoff, 0)} pt cliff to the next tier
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
