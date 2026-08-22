"""Draft event bus and server-sent events.

Transport choice
----------------
Server-sent events rather than WebSockets. Draft updates are overwhelmingly
server-to-client, SSE reconnects automatically in the browser with no library,
and it survives proxies that mangle WebSocket upgrades. The one thing SSE cannot
do - client-to-server streaming - is not needed: the user's own actions are
ordinary POSTs.

Two implementations
-------------------
* :class:`InProcessEventBus` - the zero-infrastructure default, so the demo runs
  with nothing installed. Correct for a single API process, and no use at all
  across several, which is why the health endpoint reports it as a degradation.
* :class:`RedisEventBus` - pub/sub, used when ``FHE_REDIS_URL`` is set.

Both satisfy :class:`EventBus`, so nothing downstream knows which is in play.

Subscription timing
-------------------
:meth:`EventBus.subscribe` is a coroutine that registers the subscriber *before*
returning, rather than an async generator. That distinction is load-bearing: an
async generator's body does not execute until its first ``__anext__``, so a
lazily-registering subscriber silently misses every event published between the
handler starting and the first tick of its loop. During a draft that window is
exactly when picks arrive.

Delivery guarantees
-------------------
Neither bus is a durable queue, and pretending otherwise would be dangerous. A
subscriber that misses events while disconnected must re-fetch canonical state
on reconnect rather than assume it can replay the gap. Every event therefore
carries a monotonic ``sequence`` so a client can *detect* the gap, and the
reconnect path in the war room re-reads the board.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum, unique
from typing import Any

from fhe.db.base import utcnow
from fhe.observability import ACTIVE_STREAM_CLIENTS, get_logger

log = get_logger(__name__)

# Bounded per-subscriber buffer. A slow browser tab must not grow memory without
# limit; when it overflows the subscriber is told to re-sync rather than being
# fed a silently truncated stream.
SUBSCRIBER_QUEUE_SIZE = 64

# How often a keep-alive comment is sent. Proxies commonly drop an idle
# connection after 60s, and a draft can legitimately be quiet for longer.
HEARTBEAT_SECONDS = 15.0


@unique
class EventType(StrEnum):
    """Kinds of draft event."""

    PICK_MADE = "pick_made"
    BOARD_UPDATED = "board_updated"
    DRAFT_COMPLETE = "draft_complete"
    CONNECTION_STATUS = "connection_status"
    RESYNC_REQUIRED = "resync_required"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True, slots=True)
class DraftEvent:
    """One event on a draft's channel."""

    draft_id: str
    type: EventType
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: datetime = field(default_factory=utcnow)

    def to_sse(self) -> dict[str, str]:
        """Render as the field set ``sse-starlette`` expects."""
        body = asdict(self)
        body["type"] = self.type.value
        body["emitted_at"] = self.emitted_at.isoformat()
        return {
            "event": self.type.value,
            "id": str(self.sequence),
            "data": json.dumps(body, default=str),
        }


class Subscription(ABC):
    """A registered event stream that can be iterated and closed."""

    def __aiter__(self) -> Subscription:
        """Subscriptions are their own iterator."""
        return self

    @abstractmethod
    async def __anext__(self) -> DraftEvent:
        """Return the next event, or raise ``StopAsyncIteration``."""

    @abstractmethod
    async def aclose(self) -> None:
        """Unregister and release resources."""


class QueueSubscription(Subscription):
    """Subscription backed by an asyncio queue."""

    def __init__(
        self,
        queue: asyncio.Queue[DraftEvent | None],
        unregister: Callable[[], Awaitable[None]],
    ) -> None:
        self._queue = queue
        self._unregister = unregister
        self._closed = False

    async def __anext__(self) -> DraftEvent:
        """Await the next event."""
        if self._closed:
            raise StopAsyncIteration
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def aclose(self) -> None:
        """Unregister the queue."""
        if self._closed:
            return
        self._closed = True
        await self._unregister()


class GeneratorSubscription(Subscription):
    """Subscription wrapping an async generator, used by the Redis bus."""

    def __init__(
        self,
        source: AsyncGenerator[DraftEvent, None],
        unregister: Callable[[], Awaitable[None]],
    ) -> None:
        self._source = source
        self._unregister = unregister
        self._closed = False

    async def __anext__(self) -> DraftEvent:
        """Await the next event from the underlying generator."""
        if self._closed:
            raise StopAsyncIteration
        return await self._source.__anext__()

    async def aclose(self) -> None:
        """Close the generator and unregister."""
        if self._closed:
            return
        self._closed = True
        await self._source.aclose()
        await self._unregister()


class EventBus(ABC):
    """Publish/subscribe over per-draft channels."""

    @abstractmethod
    async def publish(self, event: DraftEvent) -> None:
        """Send an event to every subscriber of its draft."""

    @abstractmethod
    async def subscribe(self, draft_id: str) -> Subscription:
        """Register a subscriber and return its stream.

        Registration completes before this returns, so no event published after
        the call can be missed.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Release any resources held by the bus."""

    @property
    @abstractmethod
    def is_distributed(self) -> bool:
        """Whether events propagate across processes."""


class InProcessEventBus(EventBus):
    """Single-process bus backed by asyncio queues."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[DraftEvent | None]]] = {}
        self._lock = asyncio.Lock()

    @property
    def is_distributed(self) -> bool:
        """Always false: this bus cannot reach another process."""
        return False

    async def publish(self, event: DraftEvent) -> None:
        """Fan the event out to every subscriber of the draft."""
        async with self._lock:
            queues = list(self._subscribers.get(event.draft_id, ()))

        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # The subscriber is too slow. Dropping the event silently would
                # leave it with a plausible but wrong board, so it is told to
                # re-fetch canonical state instead.
                log.warning(
                    "subscriber_queue_full", draft_id=event.draft_id, sequence=event.sequence
                )
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(
                        DraftEvent(
                            draft_id=event.draft_id,
                            type=EventType.RESYNC_REQUIRED,
                            sequence=event.sequence,
                            payload={"reason": "subscriber fell behind"},
                        )
                    )

    async def subscribe(self, draft_id: str) -> Subscription:
        """Register a queue for a draft and return its stream."""
        queue: asyncio.Queue[DraftEvent | None] = asyncio.Queue(SUBSCRIBER_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.setdefault(draft_id, set()).add(queue)
        ACTIVE_STREAM_CLIENTS.inc()

        async def unregister() -> None:
            async with self._lock:
                subscribers = self._subscribers.get(draft_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        del self._subscribers[draft_id]
            ACTIVE_STREAM_CLIENTS.dec()

        return QueueSubscription(queue, unregister)

    async def aclose(self) -> None:
        """Signal every subscriber to finish."""
        async with self._lock:
            for queues in self._subscribers.values():
                for queue in queues:
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(None)
            self._subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        """Total subscribers across all drafts. Used by diagnostics and tests."""
        return sum(len(queues) for queues in self._subscribers.values())


class RedisEventBus(EventBus):
    """Cross-process bus backed by Redis pub/sub.

    Used when ``FHE_REDIS_URL`` is configured. Redis pub/sub is fire-and-forget,
    so this makes the same "detect the gap and re-sync" guarantee as the
    in-process bus rather than a stronger one it cannot keep.
    """

    CHANNEL_PREFIX = "fhe:draft:"

    def __init__(self, redis_url: str) -> None:
        # Imported lazily so the package is only required when Redis is used.
        import redis.asyncio as redis

        self._client = redis.from_url(redis_url, decode_responses=True)

    @property
    def is_distributed(self) -> bool:
        """Always true: Redis reaches every process."""
        return True

    def _channel(self, draft_id: str) -> str:
        """Channel name for a draft."""
        return f"{self.CHANNEL_PREFIX}{draft_id}"

    async def publish(self, event: DraftEvent) -> None:
        """Publish onto the draft's channel."""
        body = asdict(event)
        body["type"] = event.type.value
        body["emitted_at"] = event.emitted_at.isoformat()
        await self._client.publish(self._channel(event.draft_id), json.dumps(body, default=str))

    async def subscribe(self, draft_id: str) -> Subscription:
        """Subscribe to the draft's channel and return its stream.

        The Redis SUBSCRIBE completes before this returns, so the same
        no-missed-events guarantee holds as for the in-process bus.
        """
        channel = self._channel(draft_id)
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        ACTIVE_STREAM_CLIENTS.inc()

        async def events() -> AsyncGenerator[DraftEvent, None]:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                raw = json.loads(message["data"])
                yield DraftEvent(
                    draft_id=raw["draft_id"],
                    type=EventType(raw["type"]),
                    sequence=int(raw["sequence"]),
                    payload=raw.get("payload") or {},
                    emitted_at=datetime.fromisoformat(raw["emitted_at"]),
                )

        async def unsubscribe() -> None:
            await pubsub.unsubscribe(channel)
            # redis 8.x ships inline types but leaves PubSub.aclose unannotated,
            # so strict mode sees an untyped call into a typed context.
            await pubsub.aclose()  # type: ignore[no-untyped-call]
            ACTIVE_STREAM_CLIENTS.dec()

        return GeneratorSubscription(events(), unsubscribe)

    async def aclose(self) -> None:
        """Close the Redis client."""
        await self._client.aclose()


def create_event_bus(redis_url: str | None) -> EventBus:
    """Build the appropriate bus for the configuration.

    Falls back to the in-process bus when no Redis URL is set, which is what
    lets the demo run with nothing installed.
    """
    if redis_url:
        log.info("event_bus_selected", implementation="redis")
        return RedisEventBus(redis_url)
    log.info(
        "event_bus_selected",
        implementation="in_process",
        note="single-process only; set FHE_REDIS_URL for multi-worker deployments",
    )
    return InProcessEventBus()


class SequenceCounter:
    """Monotonic per-draft event sequence.

    The sequence is what lets a reconnecting client notice it missed events. It
    is deliberately per-draft, so one busy draft does not advance another's.
    """

    def __init__(self) -> None:
        self._values: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def next(self, draft_id: str) -> int:
        """Return the next sequence number for a draft."""
        async with self._lock:
            value = self._values.get(draft_id, 0) + 1
            self._values[draft_id] = value
            return value

    def current(self, draft_id: str) -> int:
        """The last issued sequence number, or 0."""
        return self._values.get(draft_id, 0)
