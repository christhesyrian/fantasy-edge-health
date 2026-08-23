"use client";

/**
 * Theme selection.
 *
 * Dark is the primary experience — a war room is used in a dark room — but the
 * light palette exists and is reachable. Three states rather than two: an
 * explicit choice stamps `data-theme`, while "system" removes the attribute and
 * lets `prefers-color-scheme` decide.
 *
 * Like [useFavourites], this reads a store outside React, so it subscribes to
 * that store rather than copying it into state inside an effect. The server
 * snapshot is the default theme, so the markup React sends matches the markup it
 * hydrates.
 */
import { useCallback, useEffect, useSyncExternalStore } from "react";

const STORAGE_KEY = "fhe:theme";

export type Theme = "dark" | "light" | "system";

const DEFAULT_THEME: Theme = "dark";

const listeners = new Set<() => void>();

// Cached against the stored text so repeated renders get a stable value.
let lastRaw: string | null | undefined;
let cached: Theme = DEFAULT_THEME;

// A choice storage refused to keep. Without this the next render would re-read
// storage, not find it, and silently snap the theme back to the default.
let unpersisted: Theme | null = null;

function isTheme(value: string | null): value is Theme {
  return value === "dark" || value === "light" || value === "system";
}

function readRaw(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function getSnapshot(): Theme {
  const raw = readRaw();
  if (raw !== lastRaw) {
    lastRaw = raw;
    cached = isTheme(raw) ? raw : DEFAULT_THEME;
    unpersisted = null; // Storage moved on; the rejected choice is stale.
  }
  return unpersisted ?? cached;
}

function getServerSnapshot(): Theme {
  return DEFAULT_THEME;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function commit(next: Theme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
    lastRaw = readRaw();
    cached = next;
    unpersisted = null;
  } catch {
    // Blocked storage only costs persistence across reloads; the choice still
    // applies for this session.
    unpersisted = next;
  }
  for (const listener of listeners) listener();
}

export function useTheme(): {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  cycle: () => void;
} {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => commit(next), []);

  const cycle = useCallback(() => {
    const current = getSnapshot();
    commit(current === "dark" ? "light" : current === "light" ? "system" : "dark");
  }, []);

  return { theme, setTheme, cycle };
}
