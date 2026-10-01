"""Deriving the wedge signature from dispatch/settlement bookkeeping.

Replaces bkn301-inference's test_upstream_metrics.py: QVAC Fabric exposes no
/metrics to scrape (docs/QVAC_FORK.md), so there is no vLLM-exposition parser
left to test here. The state machine (`WedgeMonitor`) is pure and clock-
injected, same as its predecessor `WedgeDetector`; the integration class below
proves the real dispatch path (`gateway/upstream/client.py`'s
`_send_and_record`) actually produces the signature end to end, using the
stub's `--stub-wedge` fixture the same way the old suite did.
"""
from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

from gateway.upstream.client import UpstreamPool
from gateway.upstream.wedge import WedgeMonitor


def _body(**over):
    b = {"model": "Qwen/Qwen3-8B-GGUF",
         "messages": [{"role": "user", "content": "hello"}]}
    b.update(over)
    return b


class TestWedgeMonitor:
    """Pure state machine, injected clock, no sleeping and no sockets."""

    def test_an_outstanding_dispatch_trips_after_the_window(self):
        m = WedgeMonitor()
        m.dispatched("u", now=0.0)
        assert not m.suspected("u", window_sec=10.0, now=5.0)
        assert m.suspected("u", window_sec=10.0, now=11.5)
        assert m.stall_seconds("u", now=11.0) == pytest.approx(11.0)

    def test_idle_is_not_wedged(self):
        """No outstanding dispatch at all is a quiet replica, not a broken one."""
        m = WedgeMonitor()
        assert not m.suspected("u", window_sec=10.0, now=1000.0)
        assert m.stall_seconds("u", now=1000.0) == 0.0

    def test_a_settled_alive_response_clears_the_stall(self):
        m = WedgeMonitor()
        token = m.dispatched("u", now=0.0)
        assert m.stall_seconds("u", now=5.0) == pytest.approx(5.0)
        m.settled("u", token, alive=True, now=5.0)
        assert m.stall_seconds("u", now=100.0) == 0.0
        assert not m.suspected("u", window_sec=10.0, now=100.0)

    def test_an_error_response_still_counts_as_alive(self):
        """A fast 500 is proof the process answers -- see wedge.py's docstring.

        Only a connect failure or a read timeout (alive=False) fail to clear
        the stall; every actually-received response does, whatever its status.
        """
        m = WedgeMonitor()
        token = m.dispatched("u", now=0.0)
        m.settled("u", token, alive=True, now=1.0)
        assert m.stall_seconds("u", now=50.0) == 0.0

    def test_a_connect_failure_or_read_timeout_does_not_clear_the_stall(self):
        """alive=False removes the attempt from outstanding but proves nothing.

        With nothing else outstanding, stall_seconds correctly reads 0 -- the
        attempt is over, not still stuck -- but note this is the strongest
        evidence available, not a "the replica is fine" signal, which is why
        classify()/FailureKind (a separate, already-engine-agnostic mechanism)
        is what actually trips the breaker on a real read timeout.
        """
        m = WedgeMonitor()
        token = m.dispatched("u", now=0.0)
        m.settled("u", token, alive=False, now=120.0)
        assert m.stall_seconds("u", now=121.0) == 0.0

    def test_other_completions_do_not_clear_a_DIFFERENT_stuck_dispatch(self):
        """One replica genuinely stuck, another attempt on it completing
        AFTER the stuck one began is what should clear it -- proven above.
        A settlement that happened BEFORE the still-outstanding one began must
        not retroactively clear it.
        """
        m = WedgeMonitor()
        early_token = m.dispatched("u", now=0.0)
        m.settled("u", early_token, alive=True, now=1.0)
        stuck_token = m.dispatched("u", now=2.0)  # noqa: F841 - held open deliberately
        assert m.stall_seconds("u", now=20.0) == pytest.approx(18.0)

    def test_replicas_are_tracked_independently(self):
        m = WedgeMonitor()
        m.dispatched("a", now=0.0)
        token_b = m.dispatched("b", now=0.0)
        m.settled("b", token_b, alive=True, now=1.0)
        assert m.suspected("a", window_sec=10.0, now=20.0)
        assert not m.suspected("b", window_sec=10.0, now=20.0)

    def test_settling_an_unknown_token_does_not_raise(self):
        """Defensive only: every real dispatch always settles exactly once via
        _send_and_record's try/except, but a monitor that could be crashed by
        a duplicate or stale token would be worse than one that no-ops.
        """
        m = WedgeMonitor()
        m.settled("u", 999, alive=True, now=0.0)  # no prior dispatched() call


class TestWedgeMonitorAgainstLiveStub:
    """Proves the real dispatch path produces the signature end to end.

    A wedged engine still answers /v1/models -- that is exactly why no HTTP
    probe alone can see one, and exactly the case gateway/upstream/wedge.py
    exists to catch from the gateway's own dispatch bookkeeping instead.
    """

    async def test_stub_wedge_produces_the_signature(self, live_stub):
        from gateway.upstream.wedge import wedge_monitor

        base = live_stub("--stub-wedge")
        url = f"{base}/v1"
        pool = UpstreamPool([url])
        await pool.start()
        task = asyncio.create_task(pool.request("/chat/completions", json=_body()))
        try:
            await asyncio.sleep(0.1)
            assert wedge_monitor.suspected(url, window_sec=0.05), (
                "a dispatch that has been outstanding for 0.1s did not trip a "
                "0.05s window"
            )
            assert httpx.get(f"{base}/v1/models", timeout=1.0).status_code == 200, (
                "a wedged engine still answers /v1/models -- if this fails, the "
                "stub is simulating a crash rather than a wedge"
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            await pool.stop()
