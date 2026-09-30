"""A fake OpenAI-compatible server, for developing without a GPU.

WHY THIS EXISTS
===============
An M-series MacBook has no CUDA, and Docker Desktop on macOS has no GPU
passthrough for vLLM -- and while QVAC Fabric's Vulkan/Metal backends can in
principle run on this hardware, doing so is not yet part of this repo's
verified dev loop (docs/QVAC_FORK.md). So this still serves the parts of the
OpenAI surface the gateway actually touches, on the laptop, in-process,
deterministically:

    GET  /v1/models
    POST /v1/chat/completions   (streaming and non-streaming)

Notably absent, and deliberately so: `/health` and `/metrics`. QVAC Fabric's
HTTP server documents neither (docs/QVAC_FORK.md) -- there is nothing upstream
to scrape or probe beyond the OpenAI surface itself, which is exactly why
`gateway/upstream/client.py`'s readiness checks already hit `/v1/models` and
`gateway/upstream/wedge.py` derives its signal from dispatch bookkeeping rather
than a scrape. Emulating endpoints the real server doesn't have would test
nothing real.

DETERMINISTIC BY DESIGN
=======================
Responses are generated from a hash of the request, not sampled. That makes the
stub usable as a test fixture: the same request always yields the same bytes, so
a gateway test can assert on a response body without pinning a model. It is NOT
trying to be a language model -- it produces filler of a requested length. Any
test asserting on answer *quality* against this stub is testing nothing.

WHAT IT DELIBERATELY REPRODUCES
================================
Behaviours exist here only because the real thing has them (as documented;
none of this is yet verified against a live QVAC Fabric server -- see
docs/QVAC_FORK.md's checklist) and the gateway must cope:

  * `--stub-think` emits a Qwen3 <think> block, so the B3 stripper can be tested
    against the failure it exists for -- unchanged from bkn301-inference, since
    it is a model-family quirk, not an engine one.
  * `--stub-latency` adds a delay, so probe tuning and the gateway's separate
    connect/read budgets can be exercised. A legitimate generation can take
    10s+, and a naively tuned liveness probe must not kill a healthy pod over
    that.
  * `--stub-fail-after N` starts returning 503 after N requests, for the
    circuit breaker.
  * `--stub-ctx-size` (default 8192, a properly-configured deployment, not
    QVAC's raw server default of 1024 -- see StubState.ctx_size's docstring
    for why the stub does not default to the smaller number) rejects a
    request whose prompt-plus-completion budget exceeds it with a
    `context_length_exceeded` 400, the way an OpenAI-shaped
    server signals this.
  * Vision content in a chat message (an `image_url` part) is silently
    dropped rather than erroring or being described in the reply -- QVAC's
    documented behavior for its chat surface, and worth a fixture precisely
    because "silently" is the dangerous part: nothing here should let a test
    assume vision works.
  * `--stub-parallel-slots` (renamed from bkn301-inference's
    `--stub-max-concurrent`) models QVAC's FCFS `config.parallel: N` fixed
    decode slots, not vLLM's dynamic continuous batching -- same semaphore
    mechanism, different name because the queueing MODEL it stands in for is
    different.

Run:
    python -m stub.server --port 8001
    python -m stub.server --port 8001 --stub-think --stub-latency 2.0
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

STUB_MODEL = "Qwen/Qwen3-8B-AWQ"

# Filler vocabulary. Domain-flavoured so a human reading a test failure can tell
# at a glance that they are looking at stub output and not a real answer.
_WORDS = (
    "the", "asset", "portfolio", "position", "reported", "quarter", "value",
    "token", "supply", "investor", "compliance", "review", "figure", "record",
    "allocation", "yield", "treasury", "balance", "status", "summary",
)


class StubState:
    def __init__(self) -> None:
        self.think = False
        self.latency = 0.0
        self.fail_after: int | None = None
        self.requests = 0
        # When set, a chat request queues itself and then never completes: the
        # socket stays open and /v1/models keeps answering 200. That is what a
        # wedged engine looks like, and why no HTTP probe can see one --
        # gateway/upstream/wedge.py exists specifically to catch this from the
        # dispatch side instead.
        self.wedge = False
        # `fail_after` counts REQUESTS, which cannot express recovery: once the
        # gateway's breaker opens it stops sending chat requests, so a
        # request-counted heal never advances and half-open -> closed is
        # untestable. These two are driven by time and by an out-of-band control
        # call instead.
        self.fail_until: float | None = None
        self.control_enabled = False
        # Which surface fails. `chat` (the default, and the pre-existing
        # behaviour) leaves /v1/models answering 200 while chat 503s -- which is
        # not a test convenience but the fixture that proves a /models reclose
        # probe is weak, because a really wedged engine behaves exactly that
        # way: the API server is not the engine loop.
        self.fail_mode = "chat"
        self.fail_status = 503
        # A DIFFERENT shape from fail_after: "fail exactly the first N requests,
        # then succeed" rather than "fail forever once past N". This is the
        # gateway's retry logic's fixture -- a retry needs the SAME request to
        # fail once and then succeed, which a sustained failure can never
        # exercise without an external, precisely-timed /control call between
        # attempt 1 and attempt 2 (impractical: the gateway's own retry jitter
        # is under 2s).
        self.fail_first_n = 0
        # Admission control, so queueing is a consequence of concurrency rather
        # than a decoration -- models QVAC's fixed `config.parallel: N` decode
        # slots (FCFS beyond that), not vLLM's dynamic continuous batching.
        # Built lazily: a semaphore binds to the loop that first awaits it, and
        # StubState is constructed at import.
        self.parallel_slots = 0
        self._sem: asyncio.Semaphore | None = None
        # A PROPERLY CONFIGURED ctx_size, not QVAC's raw server default.
        #
        # QVAC's own documented default is 1024 -- deliberately tiny, meant to
        # be raised explicitly via serve.models.<alias>.config.ctx_size, the
        # same way vLLM's deployment set --max-model-len=8192 rather than
        # trusting an engine default. 8192 mirrors that PRD requirement so
        # every OTHER test (breaker, retries, quota, auth...) that has nothing
        # to do with context length keeps working unmodified: chat.py injects
        # settings.MAX_COMPLETION_TOKENS (2048) whenever a caller omits
        # max_tokens, and 1024 here would silently 400 nearly every one of
        # them. --stub-ctx-size explicitly opts a test INTO the tiny raw
        # default to exercise context_length_exceeded on purpose.
        self.ctx_size = 8192

    @property
    def sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.parallel_slots or 1024)
        return self._sem


state = StubState()
app = FastAPI(title="Stub OpenAI-compatible server", version="0.1.0")


def _deterministic_text(seed_material: str, n_tokens: int) -> str:
    """Filler of roughly n_tokens words, stable for identical input."""
    digest = hashlib.sha256(seed_material.encode("utf-8")).digest()
    out = []
    for i in range(max(1, n_tokens)):
        out.append(_WORDS[digest[i % len(digest)] % len(_WORDS)])
    text = " ".join(out)
    return text[0].upper() + text[1:] + "."


def _extract_text(content) -> str:
    """The text QVAC would actually see from a message's content.

    A vision part (`{"type": "image_url", ...}`) is silently absent from the
    result, exactly as QVAC's chat surface drops it -- not stripped out as an
    error, just never seen. A caller sending only an image and no text gets
    back the same output as an empty message, which is the point: nothing
    downstream should be able to tell an image was ever attached.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _seed_of(body: dict) -> str:
    texts = [_extract_text(m.get("content")) for m in body.get("messages", [])]
    return json.dumps(texts, ensure_ascii=False)


def _completion_text(body: dict, n_tokens: int) -> str:
    text = _deterministic_text(_seed_of(body), n_tokens)
    if not state.think:
        return text
    # Reproduce the exact shape that empties an answer: a reasoning block the
    # caller must strip. See openai_http.strip_think.
    return f"<think>\n{_deterministic_text('think' + _seed_of(body), 40)}\n</think>\n\n{text}"


class Control(BaseModel):
    """Out-of-band switches. Only mounted with --stub-control."""

    fail: bool | None = None
    fail_status: int | None = None
    fail_mode: str | None = None
    latency: float | None = None
    wedge: bool | None = None


@app.post("/control")
def control(body: Control):
    """Flip failure modes without sending traffic.

    Exists because the breaker stops sending requests the moment it opens, so a
    recovery triggered by request count can never fire. A test sets `fail` and
    then only has to wait for the prober.
    """
    if not state.control_enabled:
        raise HTTPException(status_code=404, detail="not found")
    if body.fail is not None:
        state.fail_after = 0 if body.fail else None
        state.fail_until = None
    if body.fail_status is not None:
        state.fail_status = body.fail_status
    if body.fail_mode is not None:
        state.fail_mode = body.fail_mode
    if body.latency is not None:
        state.latency = body.latency
    if body.wedge is not None:
        state.wedge = body.wedge
    # `requests` rides along here so a test can assert exactly how many
    # attempts the gateway actually made -- e.g. confirming a retry bound is
    # respected, or that a non-retryable status was NOT retried -- without a
    # dedicated introspection endpoint.
    return {"ok": True, "fail_after": state.fail_after, "wedge": state.wedge,
            "requests": state.requests}


def _should_fail() -> bool:
    if state.fail_until is not None and time.time() >= state.fail_until:
        state.fail_after = None
        state.fail_until = None
    if state.fail_after is not None and state.requests > state.fail_after:
        return True
    return state.fail_first_n > 0 and state.requests <= state.fail_first_n


@app.get("/v1/models")
def list_models():
    if state.fail_mode == "all" and _should_fail():
        raise HTTPException(status_code=state.fail_status, detail="stub: simulated outage")
    return {
        "object": "list",
        "data": [
            {
                "id": STUB_MODEL,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "stub",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    state.requests += 1

    if _should_fail():
        # 503 by default: the retryable status the gateway's bounded retry and
        # its breaker both key on. --stub-fail-status makes the differential
        # weighting (429 never trips, 5xx does) testable.
        raise HTTPException(status_code=state.fail_status,
                            detail="stub: simulated overload")

    if not body.get("messages"):
        raise HTTPException(status_code=400, detail="messages is required")

    max_tokens = int(body.get("max_tokens") or 64)
    prompt_tokens = sum(
        len(_extract_text(m.get("content")).split()) for m in body["messages"]
    )
    if prompt_tokens + max_tokens > state.ctx_size:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        f"This model's maximum context length is {state.ctx_size} "
                        f"tokens, but the request requires {prompt_tokens + max_tokens}."
                    ),
                    "code": "context_length_exceeded",
                }
            },
        )

    if state.wedge:
        # Register as queued, then never finish -- the socket stays open and
        # /v1/models keeps answering 200. gateway/upstream/wedge.py's dispatch
        # bookkeeping is what has to catch this; no HTTP probe can.
        await asyncio.Event().wait()  # never returns; the caller must time out

    text = _completion_text(body, max_tokens)
    completion_tokens = len(text.split())

    async with state.sem:
        if state.latency:
            await asyncio.sleep(state.latency)

    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }

    if body.get("stream"):
        # `stream_options.include_usage` is opt-in in the OpenAI protocol, and
        # the gateway's per-key token budget (PRD §5.3) depends on it: without
        # it a streamed request has no token counts and the audit record can
        # only log zeros. Reproduced here so BOTH paths -- server-reported
        # counts and the consumer's delta-counting fallback -- are testable
        # without a GPU.
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return StreamingResponse(
            _stream(cid, created, body.get("model", STUB_MODEL), text,
                    usage if include_usage else None),
            media_type="text/event-stream",
        )

    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": body.get("model", STUB_MODEL),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


async def _stream(cid: str, created: int, model: str, text: str,
                  usage: dict | None = None):
    """SSE in the OpenAI delta format, terminated by the literal [DONE].

    The gateway passes this through rather than buffering it, so the exact frame
    shape matters: a client that parses `data: ` lines and stops at [DONE] must
    work against the stub and against the real upstream without changes. This
    is currently an ASSUMPTION for QVAC Fabric, not a measurement -- see
    docs/QVAC_FORK.md's checklist item 4.
    """
    def frame(delta: dict, finish=None) -> str:
        payload = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield frame({"role": "assistant", "content": ""})
    for word in text.split():
        if state.latency:
            await asyncio.sleep(min(state.latency / 50.0, 0.05))
        yield frame({"content": word + " "})
    yield frame({}, finish="stop")
    if usage is not None:
        # The usage frame comes last and carries an EMPTY choices list. A
        # consumer that assumes every frame has a choices[0] crashes on it,
        # which is exactly the shape worth having a fixture for.
        final = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage,
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--stub-think", action="store_true",
                    help="Emit a Qwen3 <think> block, to exercise the B3 stripper.")
    ap.add_argument("--stub-latency", type=float, default=0.0,
                    help="Seconds of simulated decode time per request.")
    ap.add_argument("--stub-fail-after", type=int, default=None,
                    help="Return 503 after this many requests, for the breaker.")
    ap.add_argument("--stub-control", action="store_true",
                    help="Mount POST /control. Off by default: this server has "
                         "no authentication of any kind.")
    ap.add_argument("--stub-fail-seconds", type=float, default=None,
                    help="Heal this many seconds after failures begin. Time-based "
                         "because a breaker stops sending traffic once it opens.")
    ap.add_argument("--stub-fail-mode", choices=("chat", "all"), default="chat",
                    help="'chat' leaves /v1/models green, as a wedged engine "
                         "does. 'all' fails every surface.")
    ap.add_argument("--stub-fail-status", type=int, default=503,
                    help="Status to fail with (503, 429, 500).")
    ap.add_argument("--stub-fail-first-n", type=int, default=0,
                    help="Fail exactly the first N chat requests, then succeed "
                         "-- for testing that a retry recovers, as opposed to "
                         "--stub-fail-after's sustained failure for the breaker.")
    ap.add_argument("--stub-wedge", action="store_true",
                    help="Accept chat requests, queue them, and never answer -- "
                         "the wedge signature gateway/upstream/wedge.py exists "
                         "to catch.")
    ap.add_argument("--stub-parallel-slots", type=int, default=0,
                    help="QVAC-style fixed decode slots (config.parallel: N); "
                         "0 means effectively unbounded.")
    ap.add_argument("--stub-ctx-size", type=int, default=8192,
                    help="Context window; a request whose prompt+completion "
                         "exceeds it gets a context_length_exceeded 400. Pass "
                         "1024 to exercise QVAC's raw, unconfigured default.")
    args = ap.parse_args()

    state.think = args.stub_think
    state.latency = args.stub_latency
    state.fail_after = args.stub_fail_after
    state.wedge = args.stub_wedge
    state.control_enabled = args.stub_control
    state.fail_mode = args.stub_fail_mode
    state.fail_status = args.stub_fail_status
    state.fail_first_n = args.stub_fail_first_n
    if args.stub_fail_seconds is not None:
        state.fail_until = time.time() + args.stub_fail_seconds
    state.parallel_slots = args.stub_parallel_slots
    state.ctx_size = args.stub_ctx_size

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
