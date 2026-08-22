"use client";

/**
 * Live draft event stream.
 *
 * The connection is the part of a war room most likely to fail at the worst
 * moment, so its failure behaviour is explicit rather than incidental:
 *
 * - **Gaps are detected, not assumed away.** Every event carries a monotonic
 *   sequence. If one arrives out of order the hook does not try to reconstruct
 *   what it missed; it asks the caller to re-read canonical state.
 * - **Silence is not health.** A stream that has produced nothing for longer
 *   than the server's heartbeat interval is reported STALE, because a socket
 *   that is open but dead looks exactly like one that is quiet.
 * - **Reconnection backs off.** A server having a bad moment is not helped by
 *   a client retrying twenty times a second.
 *
 * The server emits *named* SSE events (`event: pick_made`). `EventSource.onmessage`
 * fires only for unnamed events, so each type is registered explicitly below.
 * Getting this wrong is silent: the connection opens, reports healthy, and then
 * never delivers a single event.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { ConnectionState } from "./types";

/** The server sends a keep-alive every 15s; allow for one missed beat. */
const STALE_AFTER_MS = 40_000;
const STALE_CHECK_INTERVAL_MS = 5_000;

const BASE_RETRY_MS = 1_000;
const MAX_RETRY_MS = 20_000;

/** Every named event the server emits. Anything absent here is never delivered. */
const EVENT_TYPES = [
  "connection_status",
  "pick_made",
  "board_updated",
  "draft_complete",
  "resync_required",
  "heartbeat",
] as const;

export interface DraftStreamEvent {
  draft_id: string;
  type: string;
  sequence: number;
  payload: Record<string, unknown>;
  emitted_at: string;
}

export interface DraftStreamOptions {
  /** Called for every event except heartbeats. */
  onEvent?: (event: DraftStreamEvent) => void;
  /**
   * Called when canonical state must be re-read: a sequence gap, an explicit
   * resync_required, or a reconnect. The caller refetches the board.
   */
  onResyncRequired?: (reason: string) => void;
  enabled?: boolean;
}

export interface DraftStreamStatus {
  connection: ConnectionState;
  lastEventAt: number | null;
  lastSequence: number;
  eventCount: number;
  /** Reconnect attempts since the last clean connection. */
  attempts: number;
}

export function useDraftStream(
  url: string | null,
  options: DraftStreamOptions = {},
): DraftStreamStatus {
  const { onEvent, onResyncRequired, enabled = true } = options;

  // Held separately from the value returned below: when there is no url, or
  // the stream is disabled, "disconnected" is a *derived* fact rather than a
  // state to write. Setting it inside the effect would trigger a cascading
  // render for something already known from the arguments.
  const [socketState, setSocketState] = useState<ConnectionState>("DISCONNECTED");
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [lastSequence, setLastSequence] = useState(0);
  const [eventCount, setEventCount] = useState(0);
  const [attempts, setAttempts] = useState(0);

  // Callbacks live in refs so a caller re-rendering with a new closure does not
  // tear down and rebuild the connection.
  const onEventRef = useRef(onEvent);
  const onResyncRef = useRef(onResyncRequired);
  useEffect(() => {
    onEventRef.current = onEvent;
    onResyncRef.current = onResyncRequired;
  }, [onEvent, onResyncRequired]);

  const sequenceRef = useRef(0);
  const lastEventAtRef = useRef<number | null>(null);
  const attemptRef = useRef(0);

  const markActivity = useCallback(() => {
    const now = Date.now();
    lastEventAtRef.current = now;
    setLastEventAt(now);
  }, []);

  useEffect(() => {
    if (!url || !enabled) {
      return;
    }

    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) return;

      source = new EventSource(url);

      source.onopen = () => {
        if (disposed) return;
        attemptRef.current = 0;
        setAttempts(0);
        setSocketState("LIVE");
        markActivity();
      };

      const handleFrame = (message: MessageEvent<string>) => {
        if (disposed) return;
        markActivity();
        setSocketState("LIVE");

        let event: DraftStreamEvent;
        try {
          event = JSON.parse(message.data) as DraftStreamEvent;
        } catch {
          // A frame we cannot parse is a contract problem, not a transport
          // one. Re-reading canonical state is the safe response.
          onResyncRef.current?.("unparseable event frame");
          return;
        }

        if (event.type === "heartbeat") {
          // Heartbeats prove liveness and carry the current sequence, which is
          // how an idle client notices it missed something.
          if (event.sequence > sequenceRef.current) {
            sequenceRef.current = event.sequence;
            setLastSequence(event.sequence);
            onResyncRef.current?.("missed events while idle");
          }
          return;
        }

        if (event.type === "resync_required") {
          onResyncRef.current?.(
            String(event.payload?.reason ?? "server requested resync"),
          );
          return;
        }

        const expected = sequenceRef.current + 1;
        if (event.sequence > expected && sequenceRef.current > 0) {
          // A gap. Neither bus is a durable queue, so the missing events are
          // genuinely gone; the only correct move is to re-read the board.
          onResyncRef.current?.(
            `sequence gap: expected ${expected}, received ${event.sequence}`,
          );
        }
        if (event.sequence > sequenceRef.current) {
          sequenceRef.current = event.sequence;
          setLastSequence(event.sequence);
        }

        setEventCount((count) => count + 1);
        onEventRef.current?.(event);
      };

      // Named events need explicit listeners; onmessage covers only the
      // default, unnamed type and is kept as a safety net.
      source.onmessage = handleFrame;
      for (const type of EVENT_TYPES) {
        source.addEventListener(type, handleFrame as EventListener);
      }

      source.onerror = () => {
        if (disposed) return;
        source?.close();
        source = null;

        attemptRef.current += 1;
        setAttempts(attemptRef.current);
        setSocketState("RECONNECTING");

        // Exponential backoff with jitter, so many tabs reconnecting after a
        // blip do not arrive as one synchronised wave.
        const backoff = Math.min(
          MAX_RETRY_MS,
          BASE_RETRY_MS * 2 ** (attemptRef.current - 1),
        );
        const delay = backoff / 2 + Math.random() * (backoff / 2);
        retryTimer = setTimeout(() => {
          onResyncRef.current?.("reconnected");
          connect();
        }, delay);
      };
    };

    connect();

    // Detect a socket that is open but has gone silent.
    const staleTimer = setInterval(() => {
      const last = lastEventAtRef.current;
      if (last === null) return;
      if (Date.now() - last > STALE_AFTER_MS) {
        setSocketState((current) => (current === "LIVE" ? "STALE" : current));
      }
    }, STALE_CHECK_INTERVAL_MS);

    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      clearInterval(staleTimer);
      source?.close();
      setSocketState("DISCONNECTED");
    };
  }, [url, enabled, markActivity]);

  const connection: ConnectionState = url && enabled ? socketState : "DISCONNECTED";

  return { connection, lastEventAt, lastSequence, eventCount, attempts };
}
