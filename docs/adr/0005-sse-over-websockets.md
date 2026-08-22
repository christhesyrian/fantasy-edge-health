# 5. Server-sent events over WebSockets

**Status:** Accepted · 2026-08-22

## Context

The war room needs live updates as picks land. Communication is almost entirely
server-to-client: the user's own actions are ordinary POSTs.

## Decision

Server-sent events. Two bus implementations behind one interface: an in-process
asyncio bus, and Redis pub/sub when configured.

## Alternatives considered

**WebSockets.** More capable, and the extra capability is unused. They also
require a library on the client, negotiate an upgrade that some corporate
proxies mangle, and need reconnection logic written by hand — where `EventSource`
reconnects natively.

**Polling from the browser.** Simple, and either wasteful or laggy. It also
duplicates the polling already happening server-side against Sleeper.

**Long polling.** All of SSE's constraints with none of its ergonomics.

## Consequences

**Good.** No client library. Automatic reconnection. Works through ordinary HTTP
infrastructure. Trivially inspectable with `curl`.

**Bad.** One-directional. Browsers cap connections per origin over HTTP/1.1 —
irrelevant here, potentially relevant with many concurrent drafts per user.

**Neither bus is a durable queue, and the design says so.** Every event carries a
monotonic per-draft sequence so a client can *detect* a gap; the response is
always to re-read canonical state rather than reconstruct what was missed.

Two implementation traps found while building this, both recorded because they
are silent failures:

1. `subscribe()` was an async generator, whose body does not run until the first
   `__anext__` — so registration happened *after* the handler returned and every
   event published in that window was lost. Registration is now eager.
2. The server emits *named* events, and `EventSource.onmessage` fires only for
   unnamed ones. The browser connected, reported healthy, and received nothing
   until it aged into STALE. Each type now has an explicit listener.
