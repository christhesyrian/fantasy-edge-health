"use client";

import { cn } from "@/lib/cn";
import { since } from "@/lib/format";
import type { ConnectionState, DraftBoard } from "@/lib/types";

const CONNECTION_STYLE: Record<ConnectionState, { dot: string; label: string }> = {
  LIVE: {
    dot: "bg-[var(--color-go-400)] breathe",
    label: "text-[var(--color-go-400)]",
  },
  RECONNECTING: {
    dot: "bg-[var(--color-hold-400)] breathe",
    label: "text-[var(--color-hold-400)]",
  },
  STALE: { dot: "bg-[var(--color-warn-400)]", label: "text-[var(--color-warn-400)]" },
  DISCONNECTED: {
    dot: "bg-[var(--color-hazard-400)]",
    label: "text-[var(--color-hazard-400)]",
  },
};

function Stat({
  label,
  value,
  testId,
}: {
  label: string;
  value: React.ReactNode;
  testId?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="rail-label">{label}</span>
      <span data-testid={testId} className="tabular text-sm text-[var(--text-primary)]">
        {value}
      </span>
    </div>
  );
}

/**
 * The status strip. Modelled on a broadcast bug: identity on the left, the
 * numbers that change in the middle, connection health on the right where the
 * eye checks it without leaving the board.
 */
export function TopRail({
  board,
  connection,
  lastEventAt,
  onOpenSettings,
}: {
  board: DraftBoard;
  connection: ConnectionState;
  lastEventAt: number | null;
  onOpenSettings?: () => void;
}) {
  const style = CONNECTION_STYLE[connection];
  const league = board.league;

  return (
    <header className="flex shrink-0 items-center gap-6 border-b bg-[var(--surface-panel)] px-4 py-2.5">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center border border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_14%,transparent)]">
          <span className="display text-[0.9rem] leading-none text-[var(--accent)]">
            FH
          </span>
        </div>
        <div className="flex flex-col">
          <span className="display text-[0.95rem] leading-tight text-[var(--text-primary)]">
            Fantasy Health Edge
          </span>
          <span className="rail-label">Draft war room</span>
        </div>
      </div>

      {board.is_demo ? (
        <span
          className="border border-[var(--color-signal-500)] px-2 py-1 text-[0.625rem] font-semibold tracking-[0.18em] text-[var(--color-signal-300)] uppercase"
          title="Synthetic data. No real player, projection, or ADP figures are shown."
        >
          Demo · synthetic data
        </span>
      ) : null}

      <div className="ml-auto flex items-center gap-6">
        <Stat
          label="League"
          value={`${league.team_count}-team ${league.scoring_format.replace("_", " ")}`}
        />
        <Stat label="Format" value={`${league.draft_type} · ${league.rounds}rd`} />
        <Stat
          label="Pick"
          testId="current-pick"
          value={
            board.current_pick
              ? `${board.current_pick} · R${board.current_round}`
              : "complete"
          }
        />
        <Stat
          label="Your next"
          value={
            board.next_user_pick ? (
              <>
                {board.next_user_pick}
                {board.picks_until_user_turn !== null &&
                board.picks_until_user_turn !== undefined ? (
                  <span className="ml-1.5 text-[var(--text-muted)]">
                    ({board.picks_until_user_turn} away)
                  </span>
                ) : null}
              </>
            ) : (
              "—"
            )
          }
        />

        <div className="flex flex-col gap-0.5">
          <span className="rail-label">Feed</span>
          <span className={cn("flex items-center gap-1.5 text-sm", style.label)}>
            <span className={cn("h-2 w-2 rounded-full", style.dot)} aria-hidden />
            <span data-testid="connection-state" className="display text-[0.8rem]">
              {connection}
            </span>
            <span className="tabular text-[0.6875rem] text-[var(--text-muted)]">
              {lastEventAt ? since(new Date(lastEventAt).toISOString()) : "—"}
            </span>
          </span>
        </div>

        {onOpenSettings ? (
          <button
            type="button"
            onClick={onOpenSettings}
            className="rail-label border px-2.5 py-1.5 transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
          >
            Settings
          </button>
        ) : null}
      </div>
    </header>
  );
}
