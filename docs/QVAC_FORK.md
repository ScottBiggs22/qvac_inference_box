# Why this repo exists, and what changed

## Why

`bkn301-qvac-inference` is a fork of `bkn301-inference`, which fronts a
self-hosted **vLLM** server. This fork swaps that upstream for **QVAC Fabric**
(Tether's Vulkan/CUDA/Metal/OpenCL-capable llama.cpp fork). This is a
management-directed business requirement, not a technical improvement:
`bkn301-inference`'s `main` branch is already deployed in production and
already meets or exceeds every PRD KPI on vLLM. Expect this fork's real-world
KPIs to be **worse**, not better — the goal here is to do the swap correctly
and safely, then measure the actual gap once real hardware numbers exist, not
to pretend the gap doesn't exist.

Same hardware class throughout: a cluster of cloud A10 GPUs, exactly as
`bkn301-inference` already runs. Only the inference engine changes.

## What this repo is, architecturally

Like `bkn301-inference`, this repo does **not** embed the inference engine. It
is a pure `httpx`-based reverse proxy speaking the OpenAI wire protocol to
whatever sits behind `UPSTREAM_URLS` — `gateway/upstream/client.py`'s
`UpstreamPool` is the single concrete client, and every route handler goes
through it. That single fact is why this fork is mostly a matter of finding
the handful of places that assumed something *specifically* about vLLM,
rather than a rewrite of the gateway itself.

## What QVAC Fabric's HTTP server actually offers (as documented — none of this
## is yet verified against a live server; see the checklist below)

Launched with `--openai`, it exposes a genuinely OpenAI-compatible surface:
`POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings`,
`GET /v1/models`, SSE streaming, `response_format` JSON-schema support, tool
calling. Concurrency is `config.parallel: N` fixed decode slots served FCFS —
**not** vLLM's dynamic continuous batching / PagedAttention. Default context
window is a documented **1024 tokens** (the PRD requires 8k). It documents
**no `/health`, no `/metrics`, and no TLS/mTLS support of any kind.**

## What changed vs. `bkn301-inference`

| Area | Disposition |
|---|---|
| `gateway/upstream/metrics_scraper.py` | **Deleted.** Scraped vLLM's proprietary `/metrics` Prometheus series to derive the wedge signal. QVAC exposes no metrics endpoint at all — nothing to scrape. |
| `gateway/upstream/wedge.py` | **New**, replaces it. Derives the same wedge signature from the gateway's own dispatch/settlement bookkeeping (`UpstreamPool._send_and_record` now reports every attempt) instead of polling the upstream. Consumed by `gateway/upstream/breaker.py`'s `record_wedge()` with **zero changes to breaker.py** — that method already took an abstract boolean and never knew its old producer was vLLM-specific. |
| `gateway/main.py`'s `_scrape_loop` | **Deleted outright**, not replaced by an equivalent timer. The new signal is event-driven (updated on every dispatch/settle), so `_breaker_prober`'s existing tick just reads it — one fewer background task than vLLM had. |
| `gateway/metrics.py` | `upstream_up`, `upstream_scrape_age_seconds`, `upstream_scrape_failures_total` gauges deleted (nothing left to describe a scrape that doesn't happen). `upstream_wedge_suspected`/`upstream_wedge_stall_seconds` kept — same names, new producer. |
| `deploy/k8s/probes.yaml` | Rewritten: `/v1/models` instead of vLLM's `/health` for all three probes (matches what `UpstreamPool.health()`/`.probe()` already used). Every timing number is an **unverified placeholder** — vLLM's 153s/45s cold/warm numbers do not transfer. |
| `stub/server.py` | Drops vLLM's `/metrics` and `/health` emulation. Adds QVAC's documented quirks: `context_length_exceeded` 400 at a configurable ctx_size (defaults to a *properly-configured* 8192, not QVAC's raw 1024 default, so unrelated tests aren't silently broken by the gateway's own `MAX_COMPLETION_TOKENS` injection), vision content silently dropped from chat, and `--stub-parallel-slots` FCFS queueing in place of vLLM's continuous-batching model. |
| `tests/test_upstream_metrics.py` | Replaced by `tests/test_wedge.py` (pure state machine + a live-stub integration test) and `tests/test_qvac_quirks.py` (context-length and vision-drop fixtures). |
| `results/phase1-vastai-a10/`, `results/phase2-l4-wedge-argon2/` | Moved to `results/vllm-baseline/` — kept as the numeric baseline this fork's own measurements get compared against, not deleted. |
| Everything else — auth, JWT, audit, quota/concurrency, retry/backoff, breaker mechanics, mTLS *wiring*, model allowlist, env-var-only config | **Unchanged in logic.** Comments/docstrings updated where they named vLLM specifically; see the commit history for the exact list. |

## What's degraded, and why it's expected

- **No continuous batching.** QVAC's FCFS `config.parallel: N` slots are a
  fundamentally different concurrency model from vLLM's dynamic, memory-shared
  batching. Expect a materially lower concurrent-user ceiling per GPU.
- **No documented health/metrics endpoint of any kind.** The wedge-detection
  redesign (`gateway/upstream/wedge.py`) is honestly scoped: it can only catch
  a wedge that has **real traffic against it**. An idle engine that wedges with
  nobody calling it is invisible until a periodic synthetic probe is built —
  deliberately not done in this pass (see checklist item 9).
- **No TLS/mTLS at the engine layer.** `gateway/upstream/client.py`'s mTLS
  wiring is unchanged and still works against *something* — whether that's
  QVAC directly or a sidecar in front of it is unresolved (checklist item 6).
- **Tiny default context window (1024 vs. vLLM's effectively-unbounded
  `--max-model-len=8192` reality).** `gateway/routes/chat.py` still injects
  `MAX_COMPLETION_TOKENS` (2048) whenever a caller omits `max_tokens` — on a
  real deployment left at QVAC's raw default, this alone would 400 nearly
  every request. `ctx_size` **must** be explicitly configured well above
  2048 + a real prompt.

## Verification checklist — must be measured on real rented A10 hardware

None of these are resolved by this pass. Each should be checked off here, with
a link to the phase-doc section that measured it, once real hardware time
happens — mirroring `bkn301-inference`'s own Phase 1/2 methodology (rent,
measure, capture raw output into `results/`, write it up, only then treat a
number as real).

- [ ] **1. Cold-start / warm-restart time** for the actual chosen launch config
      (model, quant, `ctx_size`, `parallel`). Sets
      `deploy/k8s/probes.yaml`'s `startupProbe.failureThreshold`.
- [ ] **2. Whether `GET /v1/models` stays green through an engine freeze.**
      Repeat `docs/PHASE2_L4_RESULTS.md`'s real `kill -STOP` experiment against
      QVAC Fabric. Determines whether `gateway/upstream/wedge.py`'s signal is
      load-bearing in practice (expected: yes, same "API server ≠ engine loop"
      structure), and separately whether `record_probe`'s "a good probe alone
      is not sufficient" reasoning still holds.
- [ ] **3. Actual concurrent-slot memory cost.** Does raising `parallel` share
      weights cheaply (KV-cache-only increment, like vLLM's paging), or cost
      near-full duplicate memory per slot? This is the number that determines
      QVAC's real concurrent-user ceiling on one A10 — the single biggest
      unknown behind the expected KPI regression.
- [ ] **4. Real SSE opening-frame shape/timing.** Confirms
      `gateway/routes/chat.py`'s TTFT measurement and `scripts/loadgen.py`'s
      timing assumptions, both currently carried over from vLLM's shape
      unverified.
- [ ] **5. Final quantized-model choice** (GGUF, or QVAC's own
      TQ2_0/TBQ3_0/TBQ4_0/PQ3_0/PQ4_0) at 8k `ctx_size` with N parallel slots
      fitting in 24 GiB, and its real tok/s/user — the actual KPI-gap number
      management asked to see. `ALLOWED_MODELS`'s `"Qwen/Qwen3-8B-AWQ"`
      placeholder (an AWQ/CUDA format QVAC's engine cannot load at all) needs
      replacing with whatever alias this model is actually served under.
- [ ] **6. TLS/mTLS at the QVAC layer, or where it must move.** Two live
      options, neither chosen: (a) plaintext HTTP + bearer token on the
      private subnet, accepted as an explicit security-posture regression; or
      (b) a sidecar reverse proxy terminating mTLS and forwarding plaintext to
      QVAC on localhost — verify it doesn't reintroduce SSE buffering or break
      QVAC's non-loopback-bearer-auth default in an unexpected way.
- [ ] **7. k8s probe auth-header mechanics.** QVAC requires bearer auth on
      non-loopback binds by default; a kubelet `httpGet` probe likely counts
      as non-loopback even within the same pod. `httpGet.httpHeaders` only
      accepts a literal string (no `secretKeyRef`) — decide between a static
      probe-scoped token in the manifest and an `exec` probe reading a mounted
      Secret.
- [ ] **8. Request/output-logging parity.** vLLM was required to run with
      `--no-enable-log-requests --no-enable-log-outputs` so it kept no second
      copy of every prompt (PRD §5.4) — `gateway/audit.py`'s whole
      metadata-only design depends on the upstream not silently retaining
      prompt text. QVAC Fabric's launch flags have not been audited for an
      analogous setting.
- [ ] **9. (Follow-up, not a blocker) A synthetic wedge probe.** Closes
      `wedge.py`'s honest gap — a periodic minimal (`max_tokens=1`) completion
      through the real path, to catch a wedge with zero real traffic against
      it. Costs a real decode slot per probe; needs its own rate-limiting
      design before it's worth building.
