"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SleeperDraft, SleeperLeague } from "@/lib/types";

/**
 * Connect a real Sleeper draft.
 *
 * Three steps, in the order the user thinks about them: find me, pick the
 * league, pick the draft. Sleeper needs no credentials, so this is three
 * read-only lookups rather than an OAuth dance — which is worth saying on
 * screen, because "connect your account" usually means handing over a password.
 *
 * Connecting is the only step with side effects, and it is the only one behind
 * an explicit button.
 */
export function ConnectSleeper() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [leagueId, setLeagueId] = useState<string | null>(null);

  const user = useQuery({
    queryKey: ["sleeper-user", submitted],
    queryFn: () => api.findSleeperUser(submitted!),
    enabled: Boolean(submitted),
    retry: 0,
  });

  const leagues = useQuery({
    queryKey: ["sleeper-leagues", user.data?.user_id],
    queryFn: () => api.sleeperLeagues(user.data!.user_id),
    enabled: Boolean(user.data?.user_id),
  });

  const drafts = useQuery({
    queryKey: ["sleeper-drafts", leagueId, user.data?.user_id],
    queryFn: () => api.sleeperDrafts(leagueId!, user.data?.user_id),
    enabled: Boolean(leagueId),
  });

  const connect = useMutation({
    mutationFn: (draft: SleeperDraft) =>
      api.connectDraft({
        leagueId: leagueId!,
        draftId: draft.draft_id,
        userId: user.data?.user_id,
        follow: draft.status !== "complete",
      }),
    onSuccess: (connected) => router.push(`/war-room/${connected.draft_id}`),
  });

  const notFound = submitted && user.isFetched && user.data === null;

  return (
    <section className="border bg-[var(--surface-panel)] p-5">
      <div className="flex items-baseline gap-2.5">
        <h2 className="display text-xl text-[var(--text-primary)]">Connect Sleeper</h2>
        <span className="border border-[var(--color-go-500)] px-1.5 py-0.5 text-[0.6rem] tracking-[0.16em] text-[var(--color-go-400)] uppercase">
          live
        </span>
      </div>
      <p className="mt-2 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
        Follow a real draft as it happens. Sleeper&rsquo;s API is public and read-only,
        so this needs your username and nothing else — no password, no OAuth, no API
        key.
      </p>

      {/* Step 1 — find the account */}
      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setLeagueId(null);
          setSubmitted(username.trim());
        }}
      >
        <input
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          placeholder="Sleeper username"
          aria-label="Sleeper username"
          autoComplete="off"
          className="flex-1 border bg-[var(--surface-base)] px-2.5 py-1.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
        />
        <button
          type="submit"
          disabled={!username.trim() || user.isFetching}
          className="display border border-[var(--accent)] px-3 py-1.5 text-sm text-[var(--accent)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--color-pit-000)] disabled:opacity-40"
        >
          {user.isFetching ? "Looking…" : "Find"}
        </button>
      </form>

      {notFound ? (
        <p className="mt-2 text-[0.8125rem] text-[var(--color-hazard-400)]">
          No Sleeper account called &ldquo;{submitted}&rdquo;. Check the spelling — it
          is the username, not the display name.
        </p>
      ) : null}

      {user.isError ? (
        <p className="mt-2 text-[0.8125rem] text-[var(--color-hazard-400)]">
          {user.error instanceof ApiError
            ? user.error.message
            : "Could not reach Sleeper."}
        </p>
      ) : null}

      {/* Step 2 — choose the league */}
      {user.data ? (
        <div className="mt-4">
          <span className="rail-label">
            Leagues for {user.data.display_name ?? user.data.username}
          </span>
          {leagues.isLoading ? (
            <p className="mt-1 text-[0.8125rem] text-[var(--text-muted)]">
              Loading leagues…
            </p>
          ) : leagues.data && leagues.data.length > 0 ? (
            <ul className="mt-1.5 space-y-1">
              {leagues.data.map((league: SleeperLeague) => (
                <li key={league.league_id}>
                  <button
                    type="button"
                    onClick={() => setLeagueId(league.league_id)}
                    aria-pressed={leagueId === league.league_id}
                    className={cn(
                      "flex w-full items-baseline justify-between gap-3 border px-2.5 py-1.5 text-left transition-colors",
                      leagueId === league.league_id
                        ? "border-[var(--accent)] bg-[color-mix(in_oklab,var(--accent)_12%,transparent)]"
                        : "hover:border-[var(--color-pit-400)]",
                    )}
                  >
                    <span className="truncate text-[0.875rem] text-[var(--text-primary)]">
                      {league.name}
                    </span>
                    <span className="tabular shrink-0 text-[0.7rem] text-[var(--text-muted)]">
                      {league.total_rosters}-team {league.scoring_format}
                      {league.is_superflex ? " · superflex" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[0.8125rem] text-[var(--text-muted)]">
              No NFL leagues found for this season.
            </p>
          )}
        </div>
      ) : null}

      {/* Step 3 — choose the draft */}
      {leagueId ? (
        <div className="mt-4">
          <span className="rail-label">Drafts</span>
          {drafts.isLoading ? (
            <p className="mt-1 text-[0.8125rem] text-[var(--text-muted)]">
              Loading drafts…
            </p>
          ) : drafts.data && drafts.data.length > 0 ? (
            <ul className="mt-1.5 space-y-1">
              {drafts.data.map((draft: SleeperDraft) => (
                <li key={draft.draft_id}>
                  <button
                    type="button"
                    disabled={connect.isPending}
                    onClick={() => connect.mutate(draft)}
                    className="flex w-full items-baseline justify-between gap-3 border px-2.5 py-1.5 text-left transition-colors hover:border-[var(--accent)] disabled:opacity-50"
                  >
                    <span className="text-[0.875rem] text-[var(--text-primary)]">
                      {draft.draft_type} · {draft.rounds ?? "?"} rounds
                      {draft.user_draft_slot ? (
                        <span className="ml-1.5 text-[var(--accent)]">
                          you pick {draft.user_draft_slot}
                        </span>
                      ) : null}
                    </span>
                    <span className="display shrink-0 text-[0.7rem] text-[var(--text-muted)]">
                      {draft.status}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[0.8125rem] text-[var(--text-muted)]">
              No drafts in this league yet.
            </p>
          )}
        </div>
      ) : null}

      {connect.isError ? (
        <p className="mt-3 border-l-2 border-l-[var(--color-hazard-500)] pl-2.5 text-[0.8125rem] text-[var(--color-hazard-400)]">
          {connect.error instanceof ApiError
            ? connect.error.message
            : "Could not connect that draft."}
        </p>
      ) : null}

      {connect.isPending ? (
        <p className="mt-3 text-[0.8125rem] text-[var(--text-muted)]">
          Connecting and loading the board…
        </p>
      ) : null}
    </section>
  );
}
