import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { useFavourites as UseFavourites } from "./useFavourites";

const KEY = "fhe:favourites";

// The hook is backed by a module-level store, deliberately: every caller must
// see one set. That store therefore has to be rebuilt between tests, or one
// test's stars leak into the next.
let useFavourites: typeof UseFavourites;

beforeEach(async () => {
  window.localStorage.clear();
  vi.resetModules();
  ({ useFavourites } = await import("./useFavourites"));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useFavourites", () => {
  it("renders empty on the server even when storage has entries", () => {
    // Reading storage during render would make the server markup disagree with
    // the client's first paint and trip a hydration mismatch, so the hook must
    // start empty and fill in from an effect.
    window.localStorage.setItem(KEY, JSON.stringify(["a", "b"]));

    function Probe() {
      const { count } = useFavourites();
      return createElement("span", null, `count:${count}`);
    }

    expect(renderToString(createElement(Probe))).toContain("count:0");
  });

  it("restores previously starred players", async () => {
    window.localStorage.setItem(KEY, JSON.stringify(["a", "b"]));
    const { result } = renderHook(() => useFavourites());

    await waitFor(() => expect(result.current.count).toBe(2));
    expect(result.current.isFavourite("a")).toBe(true);
  });

  it("toggles on and off", async () => {
    const { result } = renderHook(() => useFavourites());

    act(() => result.current.toggle("x"));
    await waitFor(() => expect(result.current.isFavourite("x")).toBe(true));

    act(() => result.current.toggle("x"));
    await waitFor(() => expect(result.current.isFavourite("x")).toBe(false));
  });

  it("persists across a remount", async () => {
    const first = renderHook(() => useFavourites());
    act(() => first.result.current.toggle("keep"));
    await waitFor(() => expect(first.result.current.isFavourite("keep")).toBe(true));
    first.unmount();

    const second = renderHook(() => useFavourites());
    await waitFor(() => expect(second.result.current.isFavourite("keep")).toBe(true));
  });

  it("survives corrupt storage rather than failing the app", async () => {
    window.localStorage.setItem(KEY, "{ not json");
    const { result } = renderHook(() => useFavourites());

    await waitFor(() => expect(result.current.count).toBe(0));
    act(() => result.current.toggle("x"));
    await waitFor(() => expect(result.current.isFavourite("x")).toBe(true));
  });

  it("ignores non-string entries in stored data", async () => {
    window.localStorage.setItem(KEY, JSON.stringify(["ok", 42, null]));
    const { result } = renderHook(() => useFavourites());

    await waitFor(() => expect(result.current.count).toBe(1));
    expect(result.current.isFavourite("ok")).toBe(true);
  });

  it("keeps working when storage writes are blocked", async () => {
    const { result } = renderHook(() => useFavourites());
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });

    act(() => result.current.toggle("x"));
    // The in-memory set is the part the user notices this session.
    await waitFor(() => expect(result.current.isFavourite("x")).toBe(true));
  });

  it("clears everything", async () => {
    const { result } = renderHook(() => useFavourites());
    act(() => result.current.toggle("a"));
    await waitFor(() => expect(result.current.count).toBe(1));

    act(() => result.current.clear());
    await waitFor(() => expect(result.current.count).toBe(0));
  });
});
