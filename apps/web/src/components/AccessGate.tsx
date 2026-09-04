"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";

/**
 * The password form in front of a deployed instance.
 *
 * Wrapped around the whole app rather than checked per page: the point of the
 * gate is that nothing is reachable without it, and a per-page check is one
 * page away from being forgotten.
 *
 * The status query decides which of three things to render, and the ordering
 * matters. While it is loading nothing is shown, because flashing a login form
 * at somebody who is already signed in reads as having been logged out. An
 * instance with no password configured renders its children immediately and
 * never mentions any of this.
 */
export function AccessGate({ children }: { children: React.ReactNode }) {
  const status = useQuery({
    queryKey: ["session"],
    queryFn: () => api.sessionStatus(),
    retry: false,
    staleTime: 60_000,
  });

  // A backend that cannot be reached at all is not a locked one. Showing a
  // password form here would send somebody hunting for a password that would
  // not have helped; the app's own error handling explains a dead API better.
  if (status.isError || status.isPending) {
    return status.isError ? <>{children}</> : null;
  }

  if (!status.data.required || status.data.authenticated) {
    return <>{children}</>;
  }

  return <SignInForm />;
}

function SignInForm() {
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");

  const signIn = useMutation({
    mutationFn: (candidate: string) => api.signIn(candidate),
    onSuccess: async (result) => {
      if (!result.authenticated) {
        return;
      }
      setPassword("");
      // Everything fetched while locked out was a 401. Clearing the cache
      // rather than refetching one key means no stale error survives into the
      // signed-in view.
      await queryClient.invalidateQueries();
    },
  });

  // A 200 carrying `authenticated: false` is the wrong password; the endpoint
  // answers the question either way rather than only on success.
  const rejected = signIn.isSuccess && !signIn.data.authenticated;
  const lockedOut = signIn.error instanceof ApiError && signIn.error.status === 429;
  const wrongPassword =
    rejected || (signIn.error instanceof ApiError && signIn.error.status === 401);

  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <section className="w-full max-w-sm border bg-[var(--surface-panel)] p-5">
        <h1 className="display text-xl text-[var(--text-primary)]">
          Fantasy Health Edge
        </h1>
        <p className="mt-2 text-[0.8125rem] leading-relaxed text-[var(--text-secondary)]">
          This instance is password protected. Enter the shared password to reach the
          war room.
        </p>

        <form
          className="mt-4 flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (password) {
              signIn.mutate(password);
            }
          }}
        >
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Shared password"
            aria-label="Shared password"
            // A shared password belongs in a password manager like any other,
            // and "current-password" is what lets one offer to save it.
            autoComplete="current-password"
            autoFocus
            className="border bg-[var(--surface-base)] px-2.5 py-1.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
          />
          <button
            type="submit"
            disabled={!password || signIn.isPending}
            className="display border border-[var(--accent)] px-3 py-1.5 text-sm text-[var(--accent)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--color-pit-000)] disabled:opacity-40"
          >
            {signIn.isPending ? "Checking…" : "Enter"}
          </button>
        </form>

        <p
          aria-live="polite"
          className="mt-3 min-h-[1.25rem] text-[0.8125rem] text-[var(--color-hazard-400)]"
        >
          {lockedOut
            ? "Too many attempts from this address. Wait a few minutes and try again."
            : wrongPassword
              ? "That password is not right."
              : signIn.isError
                ? "Could not reach the API to check that password."
                : ""}
        </p>
      </section>
    </main>
  );
}
