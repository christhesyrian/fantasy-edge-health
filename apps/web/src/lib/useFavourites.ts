"use client";

/**
 * Favourited players, persisted locally.
 *
 * localStorage rather than the server: a favourite is a private, per-device
 * note about players you are watching, there are no user accounts to attach it
 * to, and losing it costs nothing. Persisting it server-side would mean
 * inventing an identity model for a feature that does not need one.
 *
 * Exposed through `useSyncExternalStore` rather than state-plus-effect. Storage
 * is genuinely external, so the subscription model is the honest description of
 * it: every caller sees one set (the board and the palette cannot drift apart),
 * other tabs propagate, and the server snapshot is empty so SSR markup matches
 * the client's first paint instead of tripping a hydration mismatch.
 */
import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "fhe:favourites";

const EMPTY: ReadonlySet<string> = new Set();

const listeners = new Set<() => void>();

/**
 * The stored text behind `cached`, so a snapshot is rebuilt only when the
 * underlying value actually changed. `getSnapshot` runs on every render and
 * must return a referentially stable set, or React re-renders forever.
 */
let lastRaw: string | null | undefined;
let cached: ReadonlySet<string> = EMPTY;

/**
 * A set that storage refused to accept (private browsing, quota). Keeping it
 * means the toggle still works for the rest of the session; only persistence
 * across reloads is lost, which is the part the user does not immediately see.
 */
let unpersisted: ReadonlySet<string> | null = null;

function readRaw(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage can be unavailable outright, not merely empty.
    return null;
  }
}

function parse(raw: string | null): ReadonlySet<string> {
  if (!raw) return EMPTY;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return EMPTY;
    return new Set(
      parsed.filter((entry): entry is string => typeof entry === "string"),
    );
  } catch {
    // Corrupt storage is not worth failing the app over; an empty set is a
    // perfectly good starting point, and the next write repairs it.
    return EMPTY;
  }
}

function getSnapshot(): ReadonlySet<string> {
  const raw = readRaw();
  if (raw !== lastRaw) {
    lastRaw = raw;
    cached = parse(raw);
    unpersisted = null; // Storage moved on; the rejected set is stale.
  }
  return unpersisted ?? cached;
}

function getServerSnapshot(): ReadonlySet<string> {
  return EMPTY;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab starring a player should show up here too.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function commit(next: ReadonlySet<string>): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
    lastRaw = readRaw();
    cached = next;
    unpersisted = null;
  } catch {
    unpersisted = next;
  }
  for (const listener of listeners) listener();
}

export interface Favourites {
  ids: ReadonlySet<string>;
  isFavourite: (playerUuid: string) => boolean;
  toggle: (playerUuid: string) => void;
  clear: () => void;
  count: number;
}

export function useFavourites(): Favourites {
  const ids = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggle = useCallback((playerUuid: string) => {
    const current = getSnapshot();
    const next = new Set(current);
    if (!next.delete(playerUuid)) next.add(playerUuid);
    commit(next);
  }, []);

  const clear = useCallback(() => commit(new Set()), []);

  return {
    ids,
    isFavourite: useCallback((uuid: string) => ids.has(uuid), [ids]),
    toggle,
    clear,
    count: ids.size,
  };
}
