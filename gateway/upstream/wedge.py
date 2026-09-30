"""Deriving the wedge signature from the gateway's own dispatch bookkeeping.

WHY THIS EXISTS INSTEAD OF SCRAPING THE UPSTREAM'S OWN METRICS
================================================================
vLLM exposed a `/metrics` endpoint whose `num_requests_waiting` /
`generation_tokens_total` pair let `gateway/upstream/metrics_scraper.py` (now
deleted) tell "wedged" (API server up, engine loop frozen) apart from "idle".
QVAC Fabric's HTTP server documents no metrics endpoint, and no health endpoint
either -- there is nothing upstream to scrape. So this module derives the same
signal from data the gateway already has: the age of the oldest chat-completion
attempt it is currently waiting on against a given replica, and whether any
attempt against that replica has settled since.

THE HONEST LIMIT OF THIS APPROACH, STATED PLAINLY
==================================================
This can only see a wedge that has REAL TRAFFIC against it. vLLM's scraper
watched the engine's own counters continuously, independent of whether the
gateway happened to be sending it anything at that moment -- so it could in
principle catch an idle engine that wedged with nobody calling it. This module
has no such channel: with zero outstanding requests there is nothing to measure
staleness against, and `suspected()` is `False` by construction. Closing that
gap needs a periodic synthetic probe through the real completion path (a
`max_tokens=1` request sent on a timer) -- deliberately not built here, because
it costs a real decode slot every time it fires and needs rate-limiting design
of its own. See docs/QVAC_FORK.md's verification checklist.

WHY A SETTLED RESPONSE CLEARS THE STALL EVEN IF ITS STATUS WAS AN ERROR
========================================================================
A wedged engine's defining trait (PROBE_CONTRACT.md) is that a request goes in
and NOTHING comes back -- not even a fast error. A 500 in under a second is
proof the process is alive and answering; treating only 2xx as "alive" would
make an ordinary bad-request storm look like a wedge. Only two outcomes never
clear a stall: a connect failure (nothing reached the replica; that says
nothing about whether ITS engine loop is running, and is already the CONNECT
failure kind's job to flag) and a read timeout (no response arrived inside the
full budget -- the strongest evidence available, not a clearing event).
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field


@dataclass
class _ReplicaState:
    outstanding: dict[int, float] = field(default_factory=dict)
    last_settled_at: float | None = None

    def oldest_outstanding(self) -> float | None:
        return min(self.outstanding.values()) if self.outstanding else None


class WedgeMonitor:
    """Per-replica outstanding-dispatch tracking. Pure aside from `time.monotonic`
    at the call sites in `gateway/upstream/client.py`; every method here also
    accepts an explicit `now` so tests never have to sleep.
    """

    def __init__(self) -> None:
        self._replicas: dict[str, _ReplicaState] = {}
        self._tokens = itertools.count()

    def _state(self, url: str) -> _ReplicaState:
        if url not in self._replicas:
            self._replicas[url] = _ReplicaState()
        return self._replicas[url]

    def dispatched(self, url: str, now: float | None = None) -> int:
        """Record one attempt as sent. Returns a token to pass to `settled`."""
        token = next(self._tokens)
        self._state(url).outstanding[token] = now if now is not None else time.monotonic()
        return token

    def settled(self, url: str, token: int, *, alive: bool, now: float | None = None) -> None:
        """One attempt is no longer outstanding.

        `alive=True` for any received HTTP response, whatever its status code --
        see the module docstring for why an error response still counts.
        `alive=False` for a connect failure or a read timeout: the attempt is
        over (it must be removed from `outstanding` either way, or it would
        stall-count forever), but neither outcome is evidence the replica is
        answering.
        """
        state = self._state(url)
        state.outstanding.pop(token, None)
        if alive:
            state.last_settled_at = now if now is not None else time.monotonic()

    def stall_seconds(self, url: str, now: float | None = None) -> float:
        """0.0 if idle, if nothing is stuck, or if something settled more
        recently than the oldest still-outstanding dispatch began.
        """
        state = self._replicas.get(url)
        if state is None:
            return 0.0
        oldest = state.oldest_outstanding()
        if oldest is None:
            return 0.0
        if state.last_settled_at is not None and state.last_settled_at >= oldest:
            return 0.0
        now = now if now is not None else time.monotonic()
        return max(0.0, now - oldest)

    def suspected(self, url: str, window_sec: float, now: float | None = None) -> bool:
        return self.stall_seconds(url, now) >= window_sec


wedge_monitor = WedgeMonitor()
