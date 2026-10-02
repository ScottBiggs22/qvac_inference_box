#!/usr/bin/env python3
"""Concurrency and throughput load generator for the Phase 1 measurements.

WHY THIS EXISTS RATHER THAN REUSING THE APP'S BENCH HARNESS
===========================================================
`baas-poc-templates/.../scripts/bench_llm_backends.py` cannot produce any of the
three headline Phase 1 numbers, which the audit on 2026-09-09 established:

  * It has NO concurrency support at all -- no flag, no threads, no asyncio, no
    multiprocessing; every loop is strictly sequential. The runbook nonetheless
    instructed "run 1/3/5/8 in parallel and report tok/s per user at each
    level". It cannot.
  * It never measures TTFT, and it cannot: the vllm backend hardcodes
    `"stream": False`, so there is no first-token timestamp to take.
  * Its `decode_tok_s` is `completion_tokens / whole_request_latency`, which
    conflates prefill with decode. PRD §3.3's ~48 tok/s/user is a DECODE
    figure. The two numbers are not comparable, and reporting one as the other
    would have understated the card.

This script is also deliberately in the gateway repo rather than the app repo,
because it must run on a rented box and the app repo carries real data: its
eval suite retrieves from the real knowledge base and injects the chunks into
the prompt, so real content would travel in the request body regardless of how
the code got there (PRD §4.8).

WHAT IT MEASURES, AND THE DEFINITIONS IT USES
=============================================
  TTFT            request sent -> first content delta. Prefill plus queueing.
  decode tok/s    (completion_tokens - 1) / (last delta - first delta).
                  The -1 is not fussiness: the first token is produced BY
                  prefill, so counting it as decode inflates the rate, and at
                  the short completion lengths a benchmark tends to use, that
                  inflation is several percent.
  per-user        the MEDIAN of per-request decode rates at a level. This is
                  what R1 (">=20 output tok/s per user") is about, and what
                  DevOps multiplies. Reported alongside, never replaced by, the
                  aggregate.
  aggregate       total completion tokens / wall time of the level. Under
                  vLLM this rose with concurrency as continuous batching did
                  its job, while per-user fell. UNVERIFIED FOR QVAC FABRIC:
                  its FCFS `config.parallel: N` fixed decode slots (docs/
                  QVAC_FORK.md) are a fundamentally different queueing model
                  -- do not assume the same aggregate-vs-concurrency curve
                  shape until a real capture says otherwise. Both numbers
                  matter and they are different questions regardless of engine.

PREFIX CACHING IS MODELLED, NOT ACCIDENTALLY DEFEATED OR EXPLOITED
==================================================================
vLLM v1 had automatic prefix caching on by default (PRD §3.6). Sending N
identical prompts would measure the cache, not the model; sending N wholly
unrelated ones would measure a cache-miss path production never sees. Real
traffic is a large byte-identical system-and-knowledge block plus a short
unique query, so that is the default shape here: a shared padded prefix and a
per-request nonce. `--unique-prefix` forces total misses for a worst-case
prefill number. WHETHER QVAC FABRIC HAS AN ANALOGOUS PREFIX/KV CACHE AT ALL IS
UNVERIFIED (docs/QVAC_FORK.md) -- this modelling is kept as the same
conservative default, not because it is known to matter under QVAC too.

SYNTHETIC BY CONSTRUCTION
=========================
Prompts are filler assembled from the domain-flavoured vocabulary in
`stub/server.py`, which exists precisely so a human reading output can tell at
a glance it is not real. Nothing here reads a corpus, a fixture, or a log.

HEAD-TO-HEAD ADDITIONS (2026-10-02, docs/QVAC_COMPARISON_LAUNCH_PROMPT.md)
=========================================================================
The vLLM-vs-QVAC comparison found four gaps in the instrument above, and every
one is closed here without changing what the legacy flags do:

  * `--ctx-tokens` sizes by WORD count (x0.75), and "7000" actually sent ~5,300
    tokens in every recorded run, on both engines. `--prompt-tokens N` instead
    calibrates against the server's own `usage.prompt_tokens` (one
    `max_tokens: 1` request per probe) until the full templated prompt is N
    tokens within `--prompt-tolerance`. `--ctx-tokens` is unchanged, so the old
    ~5.3k shape can still be re-run for continuity.
  * Nothing server-side was recorded. Per request: llama.cpp's `timings`
    (prompt_n / prompt_ms / cache_n / predicted_*) and the OpenAI
    `usage.prompt_tokens_details.cached_tokens` (vLLM needs
    `--enable-prompt-tokens-details`). Per level, with `--metrics-url`: a
    /metrics diff, giving vLLM's prefix-cache hit rate and mean prefill / queue
    time, and llama.cpp's cached-vs-computed prompt tokens.
  * Decode had p50 and min only. Now p05/p95/mean too, plus inter-token
    latency (gap between content frames), end-to-end latency, TTFT p99, and a
    steady-state aggregate that excludes the ramp-up and tail.
  * Warmup was once, at concurrency 1, which primes ONE llama.cpp slot of
    eight. `--warmup-per-level --warmup-concurrency level` primes every slot.

Cache modes (`--cache-mode`, token-sized prompts only):
  warm     shared system block, unique short user turn (the production shape).
  cold     a unique 8-digit nonce at the very START of the system block. Both
           engines match prefixes from the first token, so this is a full miss,
           while keeping the token count constant (digits tokenise one each),
           which per-seed filler could not.
  partial  shared system block plus a long unique user turn
           (`--user-turn-tokens`), the "big knowledge block, fresh document"
           shape: a partial hit.
`--fresh-prefix-per-level` salts the shared block per level, so one level's
cache does not prime the next. With `--warmup-per-level --warmup 1
--warmup-concurrency 1 --requests 1` that is the cross-slot test: one slot
primed, N requests land, and per-request `cached_tokens` shows whether the
other slots benefited.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import random
import re
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import httpx

# Reused rather than redefined: this is the stub's filler vocabulary, and having
# one synthetic vocabulary across the stub and the load generator means output
# from either is recognisable as non-real by the same tell.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stub.server import _WORDS  # noqa: E402


def pct(values: list[float], q: float) -> float | None:
    """Interpolating percentile, matching the app harness's `pct` so numbers
    from the two tools are directly comparable rather than nearly so."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    frac = pos - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 2)


@dataclass
class RequestResult:
    level: int
    worker: int
    ok: bool
    status: int | None = None
    error: str | None = None
    ttft_ms: float | None = None
    decode_ms: float | None = None
    total_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    decode_tok_s: float | None = None
    usage_reported: bool = False
    # ── Head-to-head additions. All optional, so old jsonl stays readable. ──
    repeat: int = 0
    cache_mode: str | None = None
    prompt_tokens_target: int | None = None
    # OpenAI usage.prompt_tokens_details.cached_tokens (vLLM with
    # --enable-prompt-tokens-details; llama.cpp always).
    cached_tokens: int | None = None
    # llama.cpp `timings`, server-measured. prompt_ms is the true prefill time
    # for the tokens that were NOT cached; cache_n is how many were.
    server_prompt_n: int | None = None
    server_prompt_ms: float | None = None
    server_cache_n: int | None = None
    server_predicted_n: int | None = None
    server_predicted_ms: float | None = None
    # Gap between consecutive content frames, ms.
    itl_ms_p50: float | None = None
    itl_ms_p95: float | None = None
    itl_ms_max: float | None = None
    content_frames: int = 0
    # Seconds from the start of the level, for the steady-state window.
    start_s: float | None = None
    first_s: float | None = None
    last_s: float | None = None
    end_s: float | None = None
    itl_ms: list[float] | None = None

    @property
    def cached(self) -> int | None:
        """Cached prompt tokens, preferring the server's own timing record."""
        return self.server_cache_n if self.server_cache_n is not None else self.cached_tokens


@dataclass
class LevelSummary:
    kind: str = "level"
    concurrency: int = 0
    requests: int = 0
    failures: int = 0
    wall_s: float = 0.0
    # The R1 number. Median of per-request decode rates.
    per_user_decode_tok_s_p50: float | None = None
    per_user_decode_tok_s_min: float | None = None
    aggregate_decode_tok_s: float | None = None
    ttft_ms_p50: float | None = None
    ttft_ms_p95: float | None = None
    completion_tokens_total: int = 0
    prompt_tokens_total: int = 0
    usage_reported_by_server: bool = False
    errors: dict[str, int] = field(default_factory=dict)
    # ── Head-to-head additions ──
    tag: str | None = None
    repeat: int = 0
    cache_mode: str | None = None
    per_user_decode_tok_s_p05: float | None = None
    per_user_decode_tok_s_p95: float | None = None
    per_user_decode_tok_s_mean: float | None = None
    # Tokens produced while every worker was decoding: excludes the ramp-up
    # (first prefills) and the tail (stragglers), which wall-time aggregate
    # includes. Closer to what a continuously loaded server sustains.
    aggregate_steady_tok_s: float | None = None
    ttft_ms_min: float | None = None
    ttft_ms_p99: float | None = None
    ttft_ms_max: float | None = None
    itl_ms_p50: float | None = None
    itl_ms_p95: float | None = None
    itl_ms_p99: float | None = None
    e2e_ms_p50: float | None = None
    e2e_ms_p95: float | None = None
    server_prefill_ms_p50: float | None = None
    server_prefill_ms_p95: float | None = None
    prompt_tokens_p50: float | None = None
    prompt_tokens_min: int | None = None
    prompt_tokens_max: int | None = None
    cached_tokens_total: int | None = None
    cache_hit_ratio: float | None = None
    requests_cache_hit: int | None = None
    metrics_delta: dict | None = None
    metrics_derived: dict | None = None


# ── Prompt construction ──────────────────────────────────────────────────────

def build_prompt(shared_prefix: str, ctx_tokens: int, unique_prefix: bool) -> list[dict]:
    """A shared system block plus a unique user turn. LEGACY (--ctx-tokens).

    Word count, not token count, is what is controlled here. English runs about
    0.75 words per token, so the filler is sized accordingly and the true token
    count is confirmed from the server's own `usage.prompt_tokens` rather than
    guessed -- which is also why `--ctx-tokens` is documented as approximate.
    (Measured: "7000" is ~5,300 Qwen3 tokens. Use --prompt-tokens for exact.)
    """
    nonce = uuid.uuid4().hex
    prefix = _filler(int(ctx_tokens * 0.75), seed=nonce if unique_prefix else None) \
        if unique_prefix else shared_prefix
    return [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"Summarise position {nonce} in one paragraph."},
    ]


def _filler(n_words: int, seed: str | None = None) -> str:
    """Deterministic unless a seed is given. No corpus, no fixture, no log."""
    import hashlib

    material = (seed or "shared-prefix").encode()
    digest = hashlib.sha256(material).digest()
    out = [_WORDS[digest[i % len(digest)] % len(_WORDS)] for i in range(max(1, n_words))]
    return " ".join(out)


def _digits() -> str:
    """Eight digits: Qwen3 tokenises digits one each, so the count is constant
    whatever the value -- unlike a uuid hex, which tokenises unpredictably."""
    return f"{random.randrange(10**8):08d}"


@dataclass
class PromptShape:
    """Token-sized prompt (--prompt-tokens). `sys_words` is what calibration
    tunes; everything else is fixed-length so the total stays constant."""
    cache_mode: str = "warm"
    sys_words: int = 1
    user_words: int = 0
    salt: str | None = None

    def messages(self) -> list[dict]:
        nonce = _digits()
        head = f"Ref {nonce}. " if self.cache_mode == "cold" else ""
        salt = f"Set {self.salt}. " if self.salt else ""
        system = head + salt + _filler(self.sys_words)
        user = f"Summarise position {nonce} in one paragraph."
        if self.cache_mode == "partial" and self.user_words:
            user = (f"Ref {nonce}. " + _filler(self.user_words, seed="user-turn")
                    + "\n\n" + user)
        return [{"role": "system", "content": system},
                {"role": "user", "content": user}]


async def calibrate(client: httpx.AsyncClient, url: str, model: str,
                    shape: PromptShape, target: int, tolerance: int,
                    max_iter: int = 14) -> tuple[PromptShape, int, list[dict]]:
    """Find `sys_words` so the templated prompt is `target` tokens.

    Measured with the server's own count (usage.prompt_tokens on a
    max_tokens=1 request), so it is exact for whatever tokenizer and chat
    template the engine actually applies. Token count is monotone in word
    count (each filler word is its own pre-token), so a bracketed secant
    search converges in a handful of probes.
    """
    log: list[dict] = []

    async def measure(words: int) -> int:
        s = replace(shape, sys_words=words,
                    salt="0" * 8 if shape.salt is not None else None)
        body = {"model": model, "messages": s.messages(), "max_tokens": 1,
                "stream": False, "temperature": 0.0}
        r = await client.post(url, json=body)
        r.raise_for_status()
        n = int(r.json()["usage"]["prompt_tokens"])
        log.append({"words": words, "tokens": n})
        return n

    lo: tuple[int, int] | None = None   # (words, tokens) with tokens < target
    hi: tuple[int, int] | None = None   # tokens > target
    w = max(1, int(target * 0.75))
    best = (w, None)
    for _ in range(max_iter):
        n = await measure(w)
        if best[1] is None or abs(n - target) < abs(best[1] - target):
            best = (w, n)
        if abs(n - target) <= tolerance:
            return replace(shape, sys_words=w), n, log
        if n < target:
            lo = (w, n) if lo is None or w > lo[0] else lo
        else:
            hi = (w, n) if hi is None or w < hi[0] else hi
        if lo and hi:
            if hi[0] - lo[0] <= 1:
                break
            rate = (hi[1] - lo[1]) / (hi[0] - lo[0])
            nxt = lo[0] + round((target - lo[1]) / rate)
            w = min(max(nxt, lo[0] + 1), hi[0] - 1)
        else:
            rate = n / w
            w = max(1, w + round((target - n) / rate))
    w, n = best
    if n is None or abs(n - target) > tolerance:
        raise RuntimeError(f"calibration did not converge: best {n} tokens at {w} "
                           f"words for target {target} (log {log})")
    return replace(shape, sys_words=w), n, log


# ── /metrics ─────────────────────────────────────────────────────────────────

_PROM = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(\S+)")


def parse_prom(text: str) -> dict[str, float]:
    """Prometheus text format -> {name{labels}: value}. Buckets and `_created`
    timestamps are dropped: means come from _sum/_count, and bucket deltas
    would bloat every level record for nothing the report uses."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = _PROM.match(line)
        if not m:
            continue
        name = m.group(1)
        if name.endswith("_bucket") or name.endswith("_created"):
            continue
        try:
            out[name + (m.group(2) or "")] = float(m.group(3))
        except ValueError:
            continue
    return out


def diff_prom(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {k: round(v - before.get(k, 0.0), 6)
            for k, v in after.items() if v != before.get(k, 0.0)}


def derive_metrics(delta: dict[str, float]) -> dict:
    """Engine-specific headline numbers from a /metrics diff."""
    by_name: dict[str, float] = {}
    for k, v in delta.items():
        name = k.split("{", 1)[0]
        if "source=" in k:            # keep by-source series distinct
            name = name + "{" + k.split("source=", 1)[1].split(",")[0].strip('"}') + "}"
        by_name[name] = by_name.get(name, 0.0) + v
    d: dict = {}
    q = by_name.get("vllm:prefix_cache_queries_total")
    h = by_name.get("vllm:prefix_cache_hits_total")
    if q:
        d["vllm_prefix_cache_hit_rate"] = round((h or 0.0) / q, 4)
    cached = by_name.get("llamacpp:prompt_tokens_cached_total")
    computed = by_name.get("llamacpp:prompt_tokens_total")
    if cached is not None or computed is not None:
        tot = (cached or 0.0) + (computed or 0.0)
        if tot:
            d["llamacpp_prompt_cache_hit_rate"] = round((cached or 0.0) / tot, 4)
    for name, v in by_name.items():
        if name.endswith("_sum"):
            base = name[:-4]
            cnt = by_name.get(base + "_count")
            if cnt:
                mean = v / cnt
                if base.endswith("_seconds"):
                    d[f"mean_ms:{base}"] = round(mean * 1000.0, 2)
                else:
                    d[f"mean:{base}"] = round(mean, 3)
    return d


async def scrape(client: httpx.AsyncClient, url: str | None) -> dict[str, float] | None:
    if not url:
        return None
    try:
        r = await client.get(url, timeout=10.0)
        r.raise_for_status()
        return parse_prom(r.text)
    except Exception:                                        # noqa: BLE001
        return None


# ── Requests ─────────────────────────────────────────────────────────────────

async def one_request(client: httpx.AsyncClient, url: str, body: dict,
                      level: int, worker: int,
                      t_level0: float | None = None) -> RequestResult:
    """One streamed completion, timed at the frame level.

    Timing is taken on the first frame that carries actual CONTENT, not the
    first frame of any kind: vLLM (like the stub) sent an opening frame with
    an empty-string delta to establish the role, and treating that as the
    first token would report a TTFT of nearly zero. QVAC Fabric's opening
    frame carries `content: null` instead (PHASE1_RESULTS_QVAC.md), which the
    same rule handles.
    """
    res = RequestResult(level=level, worker=worker, ok=False)
    t0 = time.perf_counter()
    base = t_level0 if t_level0 is not None else t0
    res.start_s = round(t0 - base, 4)
    t_first: float | None = None
    t_last: float | None = None
    delta_count = 0
    usage: dict | None = None
    timings: dict | None = None
    gaps: list[float] = []

    try:
        async with client.stream("POST", url, json=body) as resp:
            res.status = resp.status_code
            if resp.status_code >= 400:
                await resp.aread()
                res.error = f"http_{resp.status_code}"
                return res
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):].strip()
                if payload == "[DONE]":
                    break
                try:
                    frame = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                # The usage frame arrives last and has an empty choices list.
                if frame.get("usage"):
                    usage = frame["usage"]
                # llama.cpp attaches server-side timings to its final frame(s).
                if frame.get("timings"):
                    timings = frame["timings"]
                choices = frame.get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("delta") or {}).get("content") or ""
                if content:
                    now = time.perf_counter()
                    if t_first is None:
                        t_first = now
                    else:
                        gaps.append((now - t_last) * 1000.0)
                    t_last = now
                    delta_count += 1
    except Exception as e:                                   # noqa: BLE001
        res.error = f"{type(e).__name__}"
        return res

    t_end = time.perf_counter()
    res.total_ms = (t_end - t0) * 1000.0
    res.end_s = round(t_end - base, 4)
    res.content_frames = delta_count

    if timings:
        res.server_prompt_n = timings.get("prompt_n")
        res.server_prompt_ms = timings.get("prompt_ms")
        res.server_cache_n = timings.get("cache_n")
        res.server_predicted_n = timings.get("predicted_n")
        res.server_predicted_ms = timings.get("predicted_ms")

    if t_first is None:
        # A completed stream that produced no content is the B3 failure shape
        # (an unterminated <think> block consuming the whole allowance), and it
        # must not be averaged away as a fast request.
        res.error = "empty_completion"
        return res

    res.ok = True
    res.ttft_ms = (t_first - t0) * 1000.0
    res.decode_ms = (t_last - t_first) * 1000.0
    res.first_s = round(t_first - base, 4)
    res.last_s = round(t_last - base, 4)
    if gaps:
        res.itl_ms = [round(g, 3) for g in gaps]
        res.itl_ms_p50 = pct(gaps, 0.50)
        res.itl_ms_p95 = pct(gaps, 0.95)
        res.itl_ms_max = round(max(gaps), 2)

    if usage:
        res.usage_reported = True
        res.prompt_tokens = usage.get("prompt_tokens")
        res.completion_tokens = usage.get("completion_tokens")
        details = usage.get("prompt_tokens_details") or {}
        if details.get("cached_tokens") is not None:
            res.cached_tokens = details.get("cached_tokens")
    if res.completion_tokens is None:
        # Fallback: count content frames. Approximate -- a frame is not
        # guaranteed to be exactly one token -- so it is flagged as unreported
        # rather than silently presented as the server's own count.
        res.completion_tokens = delta_count

    n = res.completion_tokens or 0
    if n > 1 and res.decode_ms and res.decode_ms > 0:
        res.decode_tok_s = round((n - 1) / (res.decode_ms / 1000.0), 2)
    return res


def steady_state_rate(ok: list[RequestResult], level: int) -> float | None:
    """Tokens/s while all `level` workers were decoding at once.

    Window: from the moment the LAST worker got its first token of its first
    request, to the moment the FIRST worker finished its last request. Each
    request's tokens are spread uniformly over its decode span and counted for
    the overlap. Returns None when the window is empty (e.g. one request per
    worker with very uneven prefills).
    """
    by_worker: dict[int, list[RequestResult]] = {}
    for r in ok:
        if r.first_s is None or r.last_s is None:
            continue
        by_worker.setdefault(r.worker, []).append(r)
    if len(by_worker) < level:
        return None
    ws = max(min(r.first_s for r in rs) for rs in by_worker.values())
    we = min(max(r.last_s for r in rs) for rs in by_worker.values())
    if we <= ws:
        return None
    tokens = 0.0
    for r in ok:
        if r.first_s is None or r.last_s is None or not r.completion_tokens:
            continue
        span = r.last_s - r.first_s
        if span <= 0:
            continue
        overlap = max(0.0, min(r.last_s, we) - max(r.first_s, ws))
        tokens += (r.completion_tokens - 1) * overlap / span
    return round(tokens / (we - ws), 2)


@dataclass
class RunConfig:
    base_url: str
    model: str
    max_tokens: int
    timeout: float
    legacy_ctx: int | None = None          # --ctx-tokens mode
    shape: PromptShape | None = None       # --prompt-tokens mode
    prompt_tokens_target: int | None = None
    unique_prefix: bool = False
    shared_prefix: str = ""
    sampling: dict = field(default_factory=lambda: {"temperature": 0.7, "top_p": 0.8})
    extra_body: dict = field(default_factory=dict)
    cache_mode: str = "warm"

    def messages(self, salt: str | None) -> list[dict]:
        if self.shape is not None:
            return replace(self.shape, salt=salt).messages() if salt is not None \
                else self.shape.messages()
        return build_prompt(self.shared_prefix, self.legacy_ctx or 0, self.unique_prefix)


async def run_level(base_url: str, headers: dict, model: str, level: int,
                    requests_per_worker: int, ctx_tokens: int, max_tokens: int,
                    unique_prefix: bool, timeout: float,
                    shared_prefix: str, cfg: RunConfig | None = None,
                    salt: str | None = None, repeat: int = 0,
                    ) -> tuple[LevelSummary, list[RequestResult]]:
    if cfg is None:
        cfg = RunConfig(base_url=base_url, model=model, max_tokens=max_tokens,
                        timeout=timeout, legacy_ctx=ctx_tokens,
                        unique_prefix=unique_prefix, shared_prefix=shared_prefix,
                        cache_mode="cold" if unique_prefix else "warm")
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    results: list[RequestResult] = []
    t_level0 = time.perf_counter()

    async def worker(idx: int, client: httpx.AsyncClient) -> None:
        for _ in range(requests_per_worker):
            body = {
                "model": cfg.model,
                "messages": cfg.messages(salt),
                "max_tokens": cfg.max_tokens,
                "stream": True,
                # Real token counts from the server. Also the exact plumbing the
                # Phase 2 per-key token budget needs, so this pre-validates it.
                "stream_options": {"include_usage": True},
                **cfg.sampling,
                **cfg.extra_body,
            }
            r = await one_request(client, url, body, level, idx, t_level0)
            r.repeat = repeat
            r.cache_mode = cfg.cache_mode
            r.prompt_tokens_target = cfg.prompt_tokens_target
            results.append(r)

    limits = httpx.Limits(max_connections=level + 4, max_keepalive_connections=level + 4)
    async with httpx.AsyncClient(
        headers=headers, limits=limits,
        timeout=httpx.Timeout(connect=10.0, read=cfg.timeout, write=30.0, pool=30.0),
    ) as client:
        t_level0 = time.perf_counter()
        await asyncio.gather(*(worker(i, client) for i in range(level)))
        wall = time.perf_counter() - t_level0

    return summarise(level, results, wall, repeat, cfg.cache_mode), results


def summarise(level: int, results: list[RequestResult], wall: float,
              repeat: int = 0, cache_mode: str | None = None) -> LevelSummary:
    ok = [r for r in results if r.ok]
    rates = [r.decode_tok_s for r in ok if r.decode_tok_s is not None]
    ttfts = [r.ttft_ms for r in ok if r.ttft_ms is not None]
    e2e = [r.total_ms for r in ok if r.total_ms is not None]
    itl = [g for r in ok for g in (r.itl_ms or [])]
    prefill = [r.server_prompt_ms for r in ok if r.server_prompt_ms is not None]
    prompts = [r.prompt_tokens for r in ok if r.prompt_tokens is not None]
    comp = sum(r.completion_tokens or 0 for r in ok)
    errors: dict[str, int] = {}
    for r in results:
        if not r.ok:
            errors[r.error or "unknown"] = errors.get(r.error or "unknown", 0) + 1

    with_cache = [r for r in ok if r.cached is not None and r.prompt_tokens]
    cached_total = sum(r.cached for r in with_cache) if with_cache else None
    prompt_total_c = sum(r.prompt_tokens for r in with_cache) if with_cache else 0

    return LevelSummary(
        concurrency=level,
        requests=len(results),
        failures=len(results) - len(ok),
        wall_s=round(wall, 3),
        per_user_decode_tok_s_p50=pct(rates, 0.50),
        per_user_decode_tok_s_min=round(min(rates), 2) if rates else None,
        aggregate_decode_tok_s=round(comp / wall, 2) if wall > 0 else None,
        ttft_ms_p50=pct(ttfts, 0.50),
        ttft_ms_p95=pct(ttfts, 0.95),
        completion_tokens_total=comp,
        prompt_tokens_total=sum(r.prompt_tokens or 0 for r in ok),
        usage_reported_by_server=any(r.usage_reported for r in ok),
        errors=errors,
        repeat=repeat,
        cache_mode=cache_mode,
        per_user_decode_tok_s_p05=pct(rates, 0.05),
        per_user_decode_tok_s_p95=pct(rates, 0.95),
        per_user_decode_tok_s_mean=round(statistics.fmean(rates), 2) if rates else None,
        aggregate_steady_tok_s=steady_state_rate(ok, level),
        ttft_ms_min=round(min(ttfts), 2) if ttfts else None,
        ttft_ms_p99=pct(ttfts, 0.99),
        ttft_ms_max=round(max(ttfts), 2) if ttfts else None,
        itl_ms_p50=pct(itl, 0.50),
        itl_ms_p95=pct(itl, 0.95),
        itl_ms_p99=pct(itl, 0.99),
        e2e_ms_p50=pct(e2e, 0.50),
        e2e_ms_p95=pct(e2e, 0.95),
        server_prefill_ms_p50=pct(prefill, 0.50),
        server_prefill_ms_p95=pct(prefill, 0.95),
        prompt_tokens_p50=pct(prompts, 0.50),
        prompt_tokens_min=min(prompts) if prompts else None,
        prompt_tokens_max=max(prompts) if prompts else None,
        cached_tokens_total=cached_total,
        cache_hit_ratio=round(cached_total / prompt_total_c, 4)
        if cached_total is not None and prompt_total_c else None,
        requests_cache_hit=sum(1 for r in with_cache if r.cached >= 0.5 * r.prompt_tokens)
        if with_cache else None,
    )


_AGG_FIELDS = ("per_user_decode_tok_s_p50", "per_user_decode_tok_s_p05",
               "aggregate_decode_tok_s", "aggregate_steady_tok_s",
               "ttft_ms_p50", "ttft_ms_p95", "itl_ms_p50", "itl_ms_p95",
               "server_prefill_ms_p50", "cache_hit_ratio")


def aggregate_repeats(summaries: list[LevelSummary]) -> list[dict]:
    """Across-repeat mean/stdev/min/max per level, so variance is reported
    rather than one lucky (or unlucky) run standing for the engine."""
    out = []
    for level in sorted({s.concurrency for s in summaries}):
        ss = [s for s in summaries if s.concurrency == level]
        rec: dict = {"kind": "aggregate", "concurrency": level, "repeats": len(ss),
                     "cache_mode": ss[0].cache_mode, "tag": ss[0].tag,
                     "failures": sum(s.failures for s in ss)}
        for f in _AGG_FIELDS:
            vals = [getattr(s, f) for s in ss if getattr(s, f) is not None]
            if not vals:
                continue
            rec[f] = {"mean": round(statistics.fmean(vals), 2),
                      "stdev": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
                      "min": round(min(vals), 2), "max": round(max(vals), 2)}
        out.append(rec)
    return out


async def amain(args: argparse.Namespace) -> int:
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    sampling = {"temperature": args.temperature, "top_p": args.top_p}
    extra = json.loads(args.extra_body) if args.extra_body else {}
    if args.ignore_eos:
        extra["ignore_eos"] = True
    cache_mode = args.cache_mode or ("cold" if args.unique_prefix else "warm")

    cfg = RunConfig(base_url=args.url, model=args.model, max_tokens=args.max_tokens,
                    timeout=args.timeout, sampling=sampling, extra_body=extra,
                    cache_mode=cache_mode, unique_prefix=args.unique_prefix)
    calibration: dict | None = None
    if args.prompt_tokens:
        shape = PromptShape(cache_mode=cache_mode,
                            user_words=int(args.user_turn_tokens * 0.75)
                            if cache_mode == "partial" else 0,
                            salt="" if args.fresh_prefix_per_level else None)
        async with httpx.AsyncClient(headers=headers, timeout=args.timeout) as c:
            shape, got, log = await calibrate(
                c, f"{args.url.rstrip('/')}/chat/completions", args.model,
                shape, args.prompt_tokens, args.prompt_tolerance)
        cfg.shape = shape
        cfg.prompt_tokens_target = args.prompt_tokens
        calibration = {"target": args.prompt_tokens, "measured": got,
                       "sys_words": shape.sys_words, "user_words": shape.user_words,
                       "probes": log}
        print(f"calibrated: {got} prompt tokens (target {args.prompt_tokens}) "
              f"with {shape.sys_words} filler words, {len(log)} probes")
    else:
        # Sized once so every request at every level shares byte-identical prefix
        # bytes -- which is what makes the prefix-cache behaviour the realistic one.
        cfg.legacy_ctx = args.ctx_tokens
        cfg.shared_prefix = _filler(int(args.ctx_tokens * 0.75))

    header = {
        "kind": "header",
        "tool": "loadgen.py",
        "target": args.url,
        "direct": args.direct,
        "model": args.model,
        "levels": levels,
        "requests_per_worker": args.requests,
        "ctx_tokens_requested": args.ctx_tokens,
        "prompt_tokens_target": args.prompt_tokens,
        "calibration": calibration,
        "max_tokens": args.max_tokens,
        "unique_prefix": args.unique_prefix,
        "cache_mode": cache_mode,
        "user_turn_tokens": args.user_turn_tokens if cache_mode == "partial" else None,
        "fresh_prefix_per_level": args.fresh_prefix_per_level,
        "warmup": args.warmup,
        "warmup_per_level": args.warmup_per_level,
        "warmup_concurrency": args.warmup_concurrency,
        "repeats": args.repeats,
        "sampling": sampling,
        "extra_body": extra,
        "metrics_url": args.metrics_url,
        "tag": args.tag,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # PRD §4.8: this must accompany the numbers wherever they go.
        "provenance_warning": (
            "Rented-box figures are a PROVISIONAL baseline. A vast.ai host has "
            "different CPU, PCIe topology and memory bandwidth from "
            "VM.GPU.A10.1. Re-confirm on the OCI card before anyone multiplies "
            "these for procurement."
        ),
    }

    def _f(v, unit=""):
        return f"{v}{unit}" if v is not None else "-"

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    summaries: list[LevelSummary] = []
    async with httpx.AsyncClient(headers=headers) as mclient:
        with out.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(header) + "\n")

            async def warm(level: int, salt: str | None) -> None:
                conc = level if args.warmup_concurrency == "level" else 1
                await run_level(args.url, headers, args.model, conc, args.warmup,
                                args.ctx_tokens, args.max_tokens, args.unique_prefix,
                                args.timeout, cfg.shared_prefix, cfg=cfg, salt=salt)

            if args.warmup and not args.warmup_per_level:
                print(f"warmup: {args.warmup} request(s), excluded from all statistics")
                await warm(1, None if not args.fresh_prefix_per_level else _digits())

            print()
            print(f"{'conc':>5} {'rep':>3}  {'per-user':>9} {'p05':>7} {'aggr':>8} "
                  f"{'steady':>8}  {'TTFT p50':>9} {'p95':>8}  {'prefill':>8} "
                  f"{'cache%':>6} {'fail':>4}")
            print("-" * 96)

            for rep in range(args.repeats):
                for i, level in enumerate(levels):
                    salt = _digits() if args.fresh_prefix_per_level else None
                    if args.warmup and args.warmup_per_level:
                        await warm(level, salt)
                    before = await scrape(mclient, args.metrics_url)
                    summary, results = await run_level(
                        args.url, headers, args.model, level, args.requests,
                        args.ctx_tokens, args.max_tokens, args.unique_prefix,
                        args.timeout, cfg.shared_prefix, cfg=cfg, salt=salt,
                        repeat=rep,
                    )
                    after = await scrape(mclient, args.metrics_url)
                    if before is not None and after is not None:
                        summary.metrics_delta = diff_prom(before, after)
                        summary.metrics_derived = derive_metrics(summary.metrics_delta)
                    elif args.metrics_url:
                        summary.metrics_derived = {"error": "scrape_failed"}
                    summary.tag = args.tag
                    summaries.append(summary)
                    for r in results:
                        rec = asdict(r)
                        if not args.record_itl:
                            rec.pop("itl_ms", None)
                        fh.write(json.dumps({"kind": "request", **rec}) + "\n")
                    fh.write(json.dumps(asdict(summary)) + "\n")
                    fh.flush()

                    cache = summary.cache_hit_ratio
                    md = summary.metrics_derived or {}
                    if cache is None:
                        cache = md.get("vllm_prefix_cache_hit_rate",
                                       md.get("llamacpp_prompt_cache_hit_rate"))
                    prefill = summary.server_prefill_ms_p50
                    if prefill is None:
                        prefill = md.get("mean_ms:vllm:request_prefill_time_seconds")
                    print(f"{level:>5} {rep:>3}  {_f(summary.per_user_decode_tok_s_p50):>9} "
                          f"{_f(summary.per_user_decode_tok_s_p05):>7} "
                          f"{_f(summary.aggregate_decode_tok_s):>8} "
                          f"{_f(summary.aggregate_steady_tok_s):>8}  "
                          f"{_f(summary.ttft_ms_p50):>9} {_f(summary.ttft_ms_p95):>8}  "
                          f"{_f(prefill):>8} "
                          f"{_f(round(cache * 100, 1) if cache is not None else None):>6} "
                          f"{summary.failures:>4}")
                    if summary.errors:
                        print(f"         errors: {summary.errors}")
                    last = rep == args.repeats - 1 and i == len(levels) - 1
                    if not last:
                        await asyncio.sleep(args.settle)

            if args.repeats > 1:
                for rec in aggregate_repeats(summaries):
                    fh.write(json.dumps(rec) + "\n")

    print()
    print(f"wrote {out}")

    # ── The gate, stated rather than left to the reader ──────────────────────
    # PRD §7 Phase 1 exit gate: >=20 tok/s/user at 5 concurrent with 8k context.
    gates = [s for s in summaries if s.concurrency == 5
             and s.per_user_decode_tok_s_p50 is not None]
    if gates:
        worst = min(s.per_user_decode_tok_s_p50 for s in gates)
        verdict = "MET" if worst >= 20 else "NOT MET"
        size = (f"prompt={args.prompt_tokens} tokens" if args.prompt_tokens
                else f"ctx~{args.ctx_tokens} requested (word-sized)")
        print(f"R1 gate (>=20 tok/s/user at 5 concurrent): {verdict} "
              f"(worst repeat {worst} tok/s/user, {size}, cache={cache_mode})")
    if not any(s.usage_reported_by_server for s in summaries):
        print("NOTE: the server never returned a usage frame; token counts are "
              "delta-frame estimates, not server counts. Flag this when reporting.")

    total_failures = sum(s.failures for s in summaries)
    if total_failures:
        print(f"WARNING: {total_failures} request(s) failed. Numbers above cover "
              f"successes only -- do not report them without the failure count.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Measure per-user decode tok/s and TTFT at 1/3/5/8 concurrent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Run this ON THE BOX, against the gateway on loopback. Running it\n"
            "from the laptop over the SSH tunnel inflates TTFT by the round\n"
            "trip, which is the caveat the old runbook had to attach to every\n"
            "latency figure it produced.\n"
        ),
    )
    ap.add_argument("--url", default="http://127.0.0.1:8080/v1",
                    help="Gateway /v1 base. Use the upstream's own /v1 with --direct.")
    ap.add_argument("--direct", action="store_true",
                    help="Recorded in the header: this run bypassed the gateway. "
                         "Pair with a gateway run to get the gateway's overhead.")
    ap.add_argument("--api-key", default="",
                    help="Throwaway gateway key, or the upstream's static --api-key.")
    ap.add_argument("--model", default="Qwen/Qwen3-8B-GGUF")
    ap.add_argument("--levels", default="1,3,5,8",
                    help="Concurrency levels, ascending. Default 1,3,5,8 (PRD §7).")
    ap.add_argument("--requests", type=int, default=4,
                    help="Requests per worker per level.")
    ap.add_argument("--ctx-tokens", type=int, default=7000,
                    help="LEGACY approximate prompt size, sized by words (x0.75); "
                         "'7000' measured ~5,300 Qwen3 tokens. Kept so the old "
                         "shape can be re-run. Ignored when --prompt-tokens is set.")
    ap.add_argument("--prompt-tokens", type=int, default=None,
                    help="Exact prompt size in server-counted tokens, calibrated "
                         "against usage.prompt_tokens before the run.")
    ap.add_argument("--prompt-tolerance", type=int, default=5,
                    help="Calibration tolerance in tokens (default 5).")
    ap.add_argument("--cache-mode", choices=("warm", "cold", "partial"), default=None,
                    help="Token-sized prompts: warm (shared prefix), cold (unique "
                         "leading nonce: full miss), partial (shared system + long "
                         "unique user turn). Defaults to cold with --unique-prefix.")
    ap.add_argument("--user-turn-tokens", type=int, default=1000,
                    help="Approximate unique user-turn size for --cache-mode partial.")
    ap.add_argument("--fresh-prefix-per-level", action="store_true",
                    help="Salt the shared prefix per level so levels do not prime "
                         "each other (cross-slot test).")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--ignore-eos", action="store_true",
                    help="Send ignore_eos so every request decodes exactly "
                         "max_tokens (both vLLM and llama.cpp honour it).")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--top-p", type=float, default=0.8)
    ap.add_argument("--extra-body", default=None,
                    help="JSON merged into every request body (recorded).")
    ap.add_argument("--model-len", type=int, default=8192,
                    help="Server's per-sequence context size (vLLM --max-model-len; "
                         "QVAC Fabric -c / -np), for the pre-flight check below.")
    ap.add_argument("--unique-prefix", action="store_true",
                    help="Defeat prefix caching for a worst-case prefill figure.")
    ap.add_argument("--warmup", type=int, default=2,
                    help="Unrecorded requests first. The first request pays CUDA "
                         "graph capture and lazy init, which would otherwise land "
                         "entirely on the level-1 numbers.")
    ap.add_argument("--warmup-per-level", action="store_true",
                    help="Warm before every level instead of once.")
    ap.add_argument("--warmup-concurrency", choices=("1", "level"), default="1",
                    help="'level' warms with N parallel workers, priming every "
                         "llama.cpp slot rather than one.")
    ap.add_argument("--repeats", type=int, default=1,
                    help="Run the whole level sweep R times; per-repeat summaries "
                         "plus an across-repeat aggregate record.")
    ap.add_argument("--metrics-url", default=None,
                    help="Server /metrics URL, scraped before and after each level.")
    ap.add_argument("--record-itl", action="store_true",
                    help="Keep every inter-token gap in the request records.")
    ap.add_argument("--tag", default=None, help="Free-text label, recorded.")
    ap.add_argument("--settle", type=float, default=3.0,
                    help="Seconds between levels, so one level's tail does not "
                         "batch with the next level's head.")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("-o", "--output", default="logs/loadgen.jsonl")
    args = ap.parse_args()

    # Pre-flight. Getting this wrong wastes paid box time on a run that fails
    # every request with a 400 nobody reads until the results are empty.
    size = args.prompt_tokens or args.ctx_tokens
    if size + args.max_tokens > args.model_len:
        print(f"ERROR: prompt ({size}) + max-tokens "
              f"({args.max_tokens}) = {size + args.max_tokens} exceeds "
              f"--model-len {args.model_len}. The upstream will reject every "
              f"request (QVAC Fabric: context_length_exceeded).",
              file=sys.stderr)
        return 2

    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
