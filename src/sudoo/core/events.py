"""Live events: what changed in the panel, pushed to browsers and other systems as it happens.

Any part of the panel publishes an event from any thread: `events.publish("job", job.id)`. The event stream
(GET /api/v1/events, web/v1/events.py) delivers each event to every connected client as Server-Sent Events, so a page
updates the moment a job advances or an instance changes, without asking again and again.

An event has a topic, a key (the instance name, the job id) and data. The data is the resource as the API shows it,
built once when the event is published by the topic's presenter (`present("job", job_resource)`), so the core does
not depend on the API's shapes and every client receives the same document it would get with GET.

The last events are kept, so a client that loses its connection asks for what it missed (Last-Event-ID) instead of
reloading everything. A client that cannot keep up is disconnected; it reconnects and resumes the same way.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Presenter = Callable[[str], Any]

_KEEP = 1000  # events kept for clients that reconnect
_QUEUE = 2000  # events waiting for one client before it counts as too slow


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    topic: str  # "instance", "instance.deleted", "job", "job.output", "metrics", "settings"
    key: str
    data: Any
    at: float = field(default_factory=time.time)


class Subscription:
    """One connected client: its events wait in an asyncio queue on the event loop that serves it."""

    def __init__(self, loop: asyncio.AbstractEventLoop, topics: frozenset[str] | None) -> None:
        self.loop = loop
        self.topics = topics
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=_QUEUE)
        self.closed = False

    def wants(self, event: Event) -> bool:
        return self.topics is None or event.topic in self.topics

    def deliver(self, event: Event) -> None:
        """Runs on the subscriber's loop."""
        if self.closed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:  # too slow: end the stream; the client reconnects with Last-Event-ID
            self.closed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._recent: deque[Event] = deque(maxlen=_KEEP)
        self._subscribers: set[Subscription] = set()
        self._presenters: dict[str, Presenter] = {}

    def present(self, topic: str, presenter: Presenter) -> None:
        """Build the data of `topic` events from their key, when the publisher gives none."""
        self._presenters[topic] = presenter

    def publish(self, topic: str, key: str = "", data: Any = None) -> Event | None:
        """Send an event to every subscriber. Safe from any thread; never raises (a failing presenter is logged)."""
        if data is None and topic in self._presenters and self._subscribers:  # with no one listening, it is
            try:  # built later, only if a client that reconnects asks for it (data_of)
                data = self._presenters[topic](key)
            except Exception as exc:  # noqa: BLE001 - publishing must never break the work that publishes
                print(f"event {topic} {key}: {exc!r}", flush=True)
                return None
        with self._lock:
            event = Event(self._next_id, topic, key, data)
            self._next_id += 1
            self._recent.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            if subscriber.wants(event):
                try:
                    subscriber.loop.call_soon_threadsafe(subscriber.deliver, event)
                except RuntimeError:  # its loop has closed
                    self.unsubscribe(subscriber)
        return event

    def subscribe(self, topics: frozenset[str] | None = None) -> tuple[Subscription, int]:
        """Subscribe from a running event loop; returns the subscription and the id of the last event before it."""
        subscription = Subscription(asyncio.get_running_loop(), topics)
        with self._lock:
            self._subscribers.add(subscription)
            return subscription, self._next_id - 1

    def missed(self, after: int, until: int, topics: frozenset[str] | None) -> list[Event] | None:
        """The kept events after `after` up to `until`, only the latest per topic and key (that one carries the
        current state). None when some of them are no longer kept: the client must reload everything."""
        with self._lock:
            if self._recent and after < self._recent[0].id - 1:
                return None
            chosen = [e for e in self._recent if after < e.id <= until and (topics is None or e.topic in topics)]
        latest: dict[tuple[str, str], Event] = {}
        for event in chosen:
            if event.topic == "job.output":  # log text adds up: every piece counts
                latest[(event.topic, str(event.id))] = event
            else:
                latest.pop((event.topic, event.key), None)
                latest[(event.topic, event.key)] = event
        return list(latest.values())

    def data_of(self, event: Event) -> Any:
        """The event's data; built now (the current state) when it was published while no one was listening."""
        if event.data is not None or event.topic not in self._presenters:
            return event.data
        try:
            return self._presenters[event.topic](event.key)
        except Exception:  # noqa: BLE001 - e.g. the instance no longer exists
            return None

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            self._subscribers.discard(subscription)

    @property
    def last_id(self) -> int:
        return self._next_id - 1

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


BUS = EventBus()
publish = BUS.publish
present = BUS.present
