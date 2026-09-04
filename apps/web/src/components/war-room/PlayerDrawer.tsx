"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { RiskBadge } from "@/components/ui/RiskBadge";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { humanise, num, pct, signed } from "@/lib/format";
import type { InjurySpell, PlayerDetail } from "@/lib/types";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="rail-label">{label}</span>
      <span className="tabular text-sm text-[var(--text-primary)]">{value}</span>
    </div>
  );
}

/**
 * Injury timeline.
 *
 * Rendered as a season-banded list rather than a chart: the useful question is
 * "what keeps happening to this player", which a reader answers by spotting a
 * repeated body region, not by reading a date axis. The provider's original
 * wording sits under every normalised region, so a mapping is always auditable.
 */
function Timeline({ spells }: { spells: InjurySpell[] }) {
  if (spells.length === 0) {
    return (
      <p className="text-sm text-[var(--text-muted)]">
        No injury reports on file. That is an absence of evidence, not evidence of
        durability.
      </p>
    );
  }

  // One entry per injury, not per weekly report. Which injuries a player has had,
  // and which areas keep failing, are both decided by the engine: this used to
  // count report rows in the browser and so branded a single nine-week absence
  // as a recurring problem seven times over.
  const spellsByRegion = spells.reduce<Record<string, number>>((counts, spell) => {
    counts[spell.body_region] = (counts[spell.body_region] ?? 0) + 1;
    return counts;
  }, {});

  return (
    <ol className="space-y-1.5">
      {spells.map((spell, index) => {
        const recurrent = (spellsByRegion[spell.body_region] ?? 0) > 1;
        const span =
          spell.first_week === spell.last_week || spell.last_week == null
            ? spell.first_week
              ? `wk${spell.first_week}`
              : ""
            : `wk${spell.first_week}\u2013${spell.last_week}`;
        const seasons =
          spell.first_season === spell.last_season
            ? `${spell.first_season}`
            : `${spell.first_season}\u2013${spell.last_season}`;
        return (
          <li
            key={`${spell.body_region}-${spell.first_season}-${spell.first_week}-${index}`}
            className="flex items-baseline gap-2.5 border-l-2 pl-2.5"
            style={{
              borderColor: recurrent ? "var(--color-hazard-400)" : "var(--hairline)",
            }}
          >
            <span className="tabular w-20 shrink-0 text-[0.75rem] text-[var(--text-muted)]">
              {seasons}
              {span ? ` ${span}` : ""}
            </span>
            <span className="min-w-0 flex-1">
              <span className="text-[0.8125rem] text-[var(--text-primary)]">
                {humanise(spell.body_region)}
              </span>
              {recurrent ? (
                <span className="ml-1.5 text-[0.65rem] tracking-wider text-[var(--color-hazard-400)] uppercase">
                  recurring
                </span>
              ) : null}
              <span className="block text-[0.7rem] text-[var(--text-muted)]">
                {spell.weeks_absent > 0
                  ? `Listed out ${spell.weeks_absent} week${spell.weeks_absent === 1 ? "" : "s"}`
                  : "Reported, played through"}
                {spell.raw_descriptors.length > 0
                  ? ` \u00b7 reported as \u201c${spell.raw_descriptors.join("\u201d, \u201c")}\u201d`
                  : ""}
              </span>
            </span>
            <span className="display shrink-0 text-[0.65rem] text-[var(--text-secondary)]">
              {spell.worst_designation}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Horizontal usage bars. A sparkline would imply a trend one season cannot support. */
function Usage({ player }: { player: PlayerDetail }) {
  const workload = player.workload;
  if (!workload) {
    return <p className="text-sm text-[var(--text-muted)]">No usage on file.</p>;
  }

  const bars = [
    { label: "Games", value: workload.games_played, max: 17 },
    { label: "Snaps/g", value: workload.snaps_per_game, max: 70 },
    { label: "Carries/g", value: workload.carries_per_game, max: 25 },
    { label: "Targets/g", value: workload.targets_per_game, max: 12 },
  ].filter((bar) => bar.value !== null && bar.value !== undefined);

  return (
    <div className="space-y-2">
      {bars.map((bar) => (
        <div key={bar.label}>
          <div className="flex items-baseline justify-between">
            <span className="text-[0.75rem] text-[var(--text-secondary)]">
              {bar.label}
            </span>
            <span className="tabular text-[0.8125rem] text-[var(--text-primary)]">
              {num(bar.value, 1)}
            </span>
          </div>
          <div className="mt-0.5 h-1.5 w-full bg-[var(--surface-row-alt)]">
            <div
              className="h-full bg-[var(--color-signal-500)]"
              style={{
                width: `${Math.min(100, ((bar.value ?? 0) / bar.max) * 100)}%`,
              }}
            />
          </div>
        </div>
      ))}
      {workload.season ? (
        <p className="text-[0.7rem] text-[var(--text-muted)]">
          {workload.season} season
        </p>
      ) : null}
    </div>
  );
}

/**
 * Player detail, as a drawer rather than a page.
 *
 * The draft does not pause while you read, so this slides over the board
 * without unmounting it: closing returns to exactly the scroll position and
 * filter you left.
 */
export function PlayerDrawer({
  simulationId,
  playerUuid,
  onClose,
}: {
  simulationId: string;
  playerUuid: string | null;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  const { data: player, isLoading } = useQuery({
    queryKey: ["player", simulationId, playerUuid],
    queryFn: () => api.getPlayer(simulationId, playerUuid!),
    enabled: Boolean(playerUuid),
  });

  useEffect(() => {
    if (!playerUuid) return;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playerUuid, onClose]);

  if (!playerUuid) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="Close player detail"
        onClick={onClose}
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={player ? `${player.name} detail` : "Player detail"}
        className="relative flex h-full w-full max-w-xl flex-col border-l bg-[var(--surface-panel)] shadow-2xl"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b p-4">
          {isLoading || !player ? (
            <div className="h-12 w-48 animate-pulse bg-[var(--surface-row-alt)]" />
          ) : (
            <div className="min-w-0">
              <h2 className="display truncate text-2xl text-[var(--text-primary)]">
                {player.name}
              </h2>
              <p className="mt-0.5 text-sm text-[var(--text-secondary)]">
                {player.position} · {player.team ?? "FA"}
                {player.age ? ` · age ${num(player.age, 0)}` : ""}
                {player.years_experience !== null &&
                player.years_experience !== undefined
                  ? ` · ${player.years_experience}yr`
                  : ""}
              </p>
            </div>
          )}
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="rail-label shrink-0 border px-2 py-1 transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
          >
            Close · esc
          </button>
        </header>

        {player ? (
          <div className="min-h-0 flex-1 space-y-5 overflow-auto p-4">
            <section className="grid grid-cols-4 gap-3">
              <Field label="Projection" value={num(player.projected_points, 1)} />
              <Field label="ADP" value={num(player.market_adp, 1)} />
              <Field label="ADP σ" value={num(player.adp_stdev, 1)} />
              <Field label="Bye" value={player.bye_week ?? "—"} />
            </section>

            {player.health ? (
              <section>
                <div className="flex items-center justify-between">
                  <span className="rail-label">Availability risk</span>
                  <RiskBadge
                    score={player.health.risk_score}
                    band={player.health.risk_band}
                  />
                </div>
                <div className="mt-2 grid grid-cols-3 gap-3">
                  <Field
                    label="Availability"
                    value={pct(player.health.availability_estimate)}
                  />
                  <Field label="Confidence" value={pct(player.health.confidence)} />
                  <Field
                    label="Practice"
                    value={humanise(player.health.practice_trajectory)}
                  />
                </div>

                <ul className="mt-3 space-y-1.5">
                  {player.health.components.map((component) => (
                    <li
                      key={component.name}
                      className="flex items-baseline justify-between gap-3 text-[0.8125rem]"
                    >
                      <span className="text-[var(--text-secondary)]">
                        {component.detail}
                      </span>
                      <span
                        className={cn(
                          "tabular shrink-0",
                          component.points >= 0
                            ? "text-[var(--color-hazard-400)]"
                            : "text-[var(--color-go-400)]",
                        )}
                      >
                        {signed(component.points, 1)}
                      </span>
                    </li>
                  ))}
                </ul>

                <p className="mt-3 border-l-2 border-l-[var(--color-signal-500)] py-1 pl-2.5 text-[0.7rem] leading-relaxed text-[var(--text-muted)]">
                  {player.health.limitations.join(" ")}
                  {" Model "}
                  {player.health.model_version}.
                </p>
              </section>
            ) : null}

            <section>
              <span className="rail-label">Injury timeline</span>
              <div className="mt-2">
                <Timeline spells={player.injury_spells} />
              </div>
            </section>

            <section>
              <span className="rail-label">Usage</span>
              <div className="mt-2">
                <Usage player={player} />
              </div>
            </section>

            <section>
              <span className="rail-label">Provenance</span>
              <dl className="mt-2 space-y-1 text-[0.75rem] text-[var(--text-muted)]">
                <div className="flex justify-between">
                  <dt>Projection source</dt>
                  <dd className="text-[var(--text-secondary)]">
                    {player.projection_source ?? "none"}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt>ADP source</dt>
                  <dd className="text-[var(--text-secondary)]">
                    {player.adp_source ?? "none"}
                  </dd>
                </div>
                {player.is_demo ? (
                  <div className="flex justify-between">
                    <dt>Data</dt>
                    <dd className="text-[var(--color-signal-300)]">
                      Synthetic demo data
                    </dd>
                  </div>
                ) : null}
              </dl>
            </section>
          </div>
        ) : null}
      </aside>
    </div>
  );
}
