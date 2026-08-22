"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";

const SCORING = [
  { value: "ppr", label: "PPR" },
  { value: "half_ppr", label: "Half PPR" },
  { value: "standard", label: "Standard" },
];

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="rail-label">{label}</span>
      {children}
      {hint ? (
        <span className="text-[0.7rem] text-[var(--text-muted)]">{hint}</span>
      ) : null}
    </label>
  );
}

/**
 * Entry point.
 *
 * Demo mode is the primary path and is labelled as synthetic everywhere, per
 * the product rule that generated data must never be mistakable for real.
 * Connecting a Sleeper league is shown as genuinely unavailable rather than as
 * a button that fails — an integration that is not built should say so.
 */
export function Onboarding() {
  const router = useRouter();
  const [teamCount, setTeamCount] = useState(12);
  const [userSlot, setUserSlot] = useState(5);
  const [scoring, setScoring] = useState("ppr");
  const [seed, setSeed] = useState(42);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    retry: 0,
    staleTime: 30_000,
  });

  const start = useMutation({
    mutationFn: () =>
      api.createSimulation({
        teamCount,
        userDraftSlot: Math.min(userSlot, teamCount),
        scoringFormat: scoring,
        seed,
      }),
    onSuccess: (simulation) => router.push(`/war-room/${simulation.simulation_id}`),
  });

  const apiDown = health.isError;

  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col justify-center px-6 py-12">
      <header className="mb-10">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center border border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_14%,transparent)]">
            <span className="display text-lg leading-none text-[var(--accent)]">
              FH
            </span>
          </div>
          <div>
            <h1 className="display text-3xl leading-none text-[var(--text-primary)]">
              Fantasy Health Edge
            </h1>
            <p className="rail-label mt-1">Injury-adjusted draft intelligence</p>
          </div>
        </div>
        <p className="mt-5 max-w-2xl text-[0.9375rem] leading-relaxed text-[var(--text-secondary)]">
          Who should you draft right now, after accounting for expected production,
          availability risk, roster construction, positional scarcity, ADP value, and
          the probability a player survives until your next pick. Every score shows the
          arithmetic behind it.
        </p>
      </header>

      {apiDown ? (
        <div className="mb-6 border border-[var(--color-hazard-500)] bg-[color-mix(in_oklab,var(--color-hazard-500)_10%,transparent)] p-4">
          <p className="display text-[var(--color-hazard-400)]">API unreachable</p>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            Start the backend, then reload:{" "}
            <code className="tabular text-[var(--text-primary)]">
              uvicorn fhe.api.app:app --reload
            </code>
          </p>
        </div>
      ) : null}

      {health.data && health.data.degradations.length > 0 ? (
        <details className="mb-6 border bg-[var(--surface-panel)] p-3">
          <summary className="rail-label cursor-pointer text-[var(--color-hold-400)]">
            Running in degraded configuration · {health.data.degradations.length}
          </summary>
          <ul className="mt-2 space-y-1 text-[0.8125rem] text-[var(--text-secondary)]">
            {health.data.degradations.map((line) => (
              <li key={line}>— {line}</li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="grid gap-4 md:grid-cols-[1.4fr_1fr]">
        <section className="reticle border border-[var(--hairline-bright)] bg-[var(--surface-panel)] p-5">
          <div className="flex items-baseline gap-2.5">
            <h2 className="display text-xl text-[var(--text-primary)]">Demo mode</h2>
            <span className="border border-[var(--color-signal-500)] px-1.5 py-0.5 text-[0.6rem] tracking-[0.16em] text-[var(--color-signal-300)] uppercase">
              synthetic data
            </span>
          </div>
          <p className="mt-2 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
            A full mock draft against a deterministic synthetic player pool. No account,
            no credentials, no ingestion. The same seed always reproduces the same
            draft, and the board is computed by the same engine a live draft uses.
          </p>

          <div className="mt-5 grid grid-cols-2 gap-4">
            <Field label="Teams">
              <input
                type="number"
                min={4}
                max={16}
                value={teamCount}
                onChange={(event) => setTeamCount(Number(event.target.value))}
                className="tabular border bg-[var(--surface-base)] px-2.5 py-1.5 text-sm focus:border-[var(--accent)] focus:outline-none"
              />
            </Field>
            <Field label="Your pick" hint={`1 to ${teamCount}`}>
              <input
                type="number"
                min={1}
                max={teamCount}
                value={userSlot}
                onChange={(event) => setUserSlot(Number(event.target.value))}
                className="tabular border bg-[var(--surface-base)] px-2.5 py-1.5 text-sm focus:border-[var(--accent)] focus:outline-none"
              />
            </Field>
            <Field label="Scoring">
              <div className="flex gap-1.5">
                {SCORING.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setScoring(option.value)}
                    aria-pressed={scoring === option.value}
                    className={cn(
                      "display flex-1 border px-2 py-1.5 text-[0.75rem] transition-colors",
                      scoring === option.value
                        ? "border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_16%,transparent)] text-[var(--accent)]"
                        : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]",
                    )}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Seed" hint="Same seed, same draft">
              <input
                type="number"
                value={seed}
                onChange={(event) => setSeed(Number(event.target.value))}
                className="tabular border bg-[var(--surface-base)] px-2.5 py-1.5 text-sm focus:border-[var(--accent)] focus:outline-none"
              />
            </Field>
          </div>

          {start.isError ? (
            <p className="mt-3 text-[0.8125rem] text-[var(--color-hazard-400)]">
              {start.error instanceof ApiError
                ? start.error.message
                : "Could not start the draft."}
            </p>
          ) : null}

          <button
            type="button"
            onClick={() => start.mutate()}
            disabled={start.isPending || apiDown}
            className="display mt-5 w-full bg-[var(--accent)] px-4 py-2.5 text-[var(--color-pit-000)] transition-colors hover:bg-[var(--color-sodium-300)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {start.isPending ? "Starting…" : "Enter the war room"}
          </button>
        </section>

        <section className="flex flex-col gap-4">
          <div className="border bg-[var(--surface-panel)] p-4 opacity-70">
            <h2 className="display text-lg text-[var(--text-secondary)]">
              Connect Sleeper
            </h2>
            <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-[var(--text-muted)]">
              Follow a real Sleeper draft live. The provider is public and needs no API
              key — only your username.
            </p>
            <p className="mt-3 border-l-2 border-l-[var(--color-hold-400)] pl-2.5 text-[0.75rem] text-[var(--color-hold-400)]">
              Not yet wired up. The Sleeper client, live poller, and onboarding flow are
              the next milestone; this is shown as unavailable rather than as a button
              that fails.
            </p>
          </div>

          <div className="border bg-[var(--surface-panel)] p-4">
            <h2 className="display text-lg text-[var(--text-secondary)]">
              Bring your own data
            </h2>
            <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-[var(--text-muted)]">
              ADP and projections import from CSV, so the product works without any paid
              API. Every imported number keeps the source you name and is shown with it.
            </p>
            <code className="tabular mt-3 block text-[0.7rem] text-[var(--text-secondary)]">
              POST /api/v1/imports/adp
            </code>
          </div>
        </section>
      </div>

      <footer className="mt-10 border-t pt-4">
        <p className="text-[0.75rem] leading-relaxed text-[var(--text-muted)]">
          Availability risk is an estimate of fantasy availability derived from public
          injury reports. It is not a medical prediction, and it never claims a player
          will be injured. Every health figure carries its own limitations.
        </p>
      </footer>
    </main>
  );
}
