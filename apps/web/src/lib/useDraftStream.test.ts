import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDraftStream } from "./useDraftStream";

/**
 * Minimal EventSource stand-in.
 *
 * jsdom has no EventSource, and the behaviour under test is precisely the part
 * a real server would exercise: named events, sequence gaps, and errors.
 */
class MockEventSource {
  static instances: MockEventSource[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, EventListener[]>();

  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    const existing = this.listeners.get(type) ?? [];
    this.listeners.set(type, [...existing, listener]);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.();
  }

  /** Deliver a named event, the way the real server sends them. */
  emit(type: string, body: Record<string, unknown>) {
    const event = new MessageEvent(type, { data: JSON.stringify(body) });
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }

  fail() {
    this.onerror?.();
  }
}

function frame(type: string, sequence: number, payload: Record<string, unknown> = {}) {
  return {
    draft_id: "d1",
    type,
    sequence,
    payload,
    emitted_at: new Date().toISOString(),
  };
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useDraftStream", () => {
  it("delivers named events, which onmessage alone would never see", async () => {
    // Regression: the server emits `event: pick_made`. EventSource.onmessage
    // fires only for unnamed events, so a client without explicit listeners
    // connects, reports healthy, and silently receives nothing.
    const onEvent = vi.fn();
    renderHook(() => useDraftStream("http://api/events", { onEvent }));

    const source = MockEventSource.instances[0];
    act(() => source.open());
    act(() => source.emit("pick_made", frame("pick_made", 1, { player_name: "X" })));

    await waitFor(() => expect(onEvent).toHaveBeenCalledTimes(1));
    expect(onEvent.mock.calls[0][0].type).toBe("pick_made");
  });

  it("reports LIVE once connected", async () => {
    const { result } = renderHook(() => useDraftStream("http://api/events"));
    act(() => MockEventSource.instances[0].open());
    await waitFor(() => expect(result.current.connection).toBe("LIVE"));
  });

  it("asks for a resync when a sequence gap appears", async () => {
    // Neither event bus is a durable queue, so a missed event is genuinely
    // gone. Detecting the gap and re-reading is the only correct response.
    const onResyncRequired = vi.fn();
    renderHook(() => useDraftStream("http://api/events", { onResyncRequired }));

    const source = MockEventSource.instances[0];
    act(() => source.open());
    act(() => source.emit("pick_made", frame("pick_made", 1)));
    act(() => source.emit("pick_made", frame("pick_made", 5)));

    await waitFor(() => expect(onResyncRequired).toHaveBeenCalled());
    expect(onResyncRequired.mock.calls[0][0]).toContain("sequence gap");
  });

  it("does not cry gap on a contiguous stream", async () => {
    const onResyncRequired = vi.fn();
    renderHook(() => useDraftStream("http://api/events", { onResyncRequired }));

    const source = MockEventSource.instances[0];
    act(() => source.open());
    for (const sequence of [1, 2, 3, 4]) {
      act(() => source.emit("pick_made", frame("pick_made", sequence)));
    }

    expect(onResyncRequired).not.toHaveBeenCalled();
  });

  it("treats an advanced heartbeat sequence as missed events", async () => {
    // An idle client that missed picks looks identical to a quiet one, except
    // that the heartbeat carries a sequence ahead of what it has seen.
    const onResyncRequired = vi.fn();
    renderHook(() => useDraftStream("http://api/events", { onResyncRequired }));

    const source = MockEventSource.instances[0];
    act(() => source.open());
    act(() => source.emit("pick_made", frame("pick_made", 1)));
    act(() => source.emit("heartbeat", frame("heartbeat", 9)));

    await waitFor(() => expect(onResyncRequired).toHaveBeenCalled());
    expect(onResyncRequired.mock.calls[0][0]).toContain("missed events");
  });

  it("does not forward heartbeats as draft events", async () => {
    const onEvent = vi.fn();
    renderHook(() => useDraftStream("http://api/events", { onEvent }));

    const source = MockEventSource.instances[0];
    act(() => source.open());
    act(() => source.emit("heartbeat", frame("heartbeat", 0)));

    expect(onEvent).not.toHaveBeenCalled();
  });

  it("moves to RECONNECTING on error and retries with backoff", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDraftStream("http://api/events"));

    act(() => MockEventSource.instances[0].open());
    act(() => MockEventSource.instances[0].fail());
    expect(result.current.connection).toBe("RECONNECTING");

    // A retry is scheduled rather than fired immediately, so a struggling
    // server is not hammered.
    expect(MockEventSource.instances).toHaveLength(1);
    await act(async () => {
      vi.advanceTimersByTime(2_000);
    });
    expect(MockEventSource.instances.length).toBeGreaterThan(1);
  });

  it("closes the connection on unmount", () => {
    const { unmount } = renderHook(() => useDraftStream("http://api/events"));
    const source = MockEventSource.instances[0];
    unmount();
    expect(source.closed).toBe(true);
  });

  it("stays disconnected without a url", () => {
    const { result } = renderHook(() => useDraftStream(null));
    expect(result.current.connection).toBe("DISCONNECTED");
    expect(MockEventSource.instances).toHaveLength(0);
  });
});
