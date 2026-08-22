"use client";

import { cn } from "@/lib/cn";
import type { DraftAlert } from "@/lib/types";

const LEVEL_STYLE: Record<string, string> = {
  CRITICAL:
    "border-[var(--color-hazard-500)] text-[var(--color-hazard-400)] bg-[color-mix(in_oklab,var(--color-hazard-500)_12%,transparent)]",
  WARNING:
    "border-[var(--color-warn-400)] text-[var(--color-warn-400)] bg-[color-mix(in_oklab,var(--color-warn-400)_10%,transparent)]",
  INFO: "border-[var(--hairline)] text-[var(--text-secondary)]",
};

/**
 * The alert rail. Severity-ordered by the server, so the most urgent notice is
 * always leftmost where the eye lands first.
 */
export function AlertRail({
  alerts,
  onFocusPlayer,
}: {
  alerts: DraftAlert[];
  onFocusPlayer: (uuid: string) => void;
}) {
  if (alerts.length === 0) {
    return (
      <div className="flex items-center px-3 py-2">
        <span className="rail-label">No alerts</span>
      </div>
    );
  }

  return (
    <ul
      className="flex items-stretch gap-2 overflow-x-auto px-3 py-2"
      aria-live="polite"
      aria-label="Draft alerts"
    >
      {alerts.map((alert) => {
        const clickable = Boolean(alert.player_uuid);
        const Tag = clickable ? "button" : "div";
        return (
          <li key={alert.key} className="shrink-0">
            <Tag
              {...(clickable
                ? {
                    type: "button" as const,
                    onClick: () => onFocusPlayer(alert.player_uuid!),
                  }
                : {})}
              className={cn(
                "flex h-full items-center gap-2 border px-2.5 py-1.5 text-left text-[0.8125rem] whitespace-nowrap",
                LEVEL_STYLE[alert.level] ?? LEVEL_STYLE.INFO,
                clickable && "transition-colors hover:border-[var(--accent)]",
              )}
            >
              <span className="display text-[0.65rem] opacity-80">{alert.level}</span>
              <span>{alert.message}</span>
            </Tag>
          </li>
        );
      })}
    </ul>
  );
}
