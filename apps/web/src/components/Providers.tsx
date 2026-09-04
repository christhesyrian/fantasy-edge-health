"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { AccessGate } from "./AccessGate";

/**
 * Query client.
 *
 * Retries are off for mutations and low for reads: during a draft a stale
 * answer delivered late is worse than an error shown immediately, and the SSE
 * stream is what keeps the board current anyway.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
            staleTime: 2_000,
          },
          mutations: { retry: 0 },
        },
      }),
  );

  // The gate lives inside the query client because it is itself a query, and
  // outside everything else because nothing should render behind it.
  return (
    <QueryClientProvider client={client}>
      <AccessGate>{children}</AccessGate>
    </QueryClientProvider>
  );
}
