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

## What QVAC Fabric's HTTP server actually offers

**Measured** (`docs/PHASE1_RESULTS_QVAC.md`, `docs/HEAD_TO_HEAD.md`,
`docs/QVAC_FIXES_RESULTS.md`): the fork's `llama-server` exposes
`POST /v1/chat/completions` (SSE and non-streaming) and `GET /v1/models` with no
extra flag, and also an unauthenticated `/health`. Passing `--metrics` adds a
Prometheus `/metrics`. Concurrency is llama.cpp slots (`-np N`) with continuous
batching on by default. `-c` is the **total** context across slots, and its default
is the model's own context, not 1024. Tool calls work. `response_format`: see
`docs/QVAC_FIXES_RESULTS.md`. TLS: `--ssl-key-file/--ssl-cert-file` exist, but the
stock CUDA build has no OpenSSL. There is no client-certificate (mTLS) option at
all; see item 6.

`config.parallel` (FCFS, default 1), `~/.qvac/kv-cache` and `--openai` belong to a
**different** server: `qvac serve --openai` from npm `@qvac/cli`, a Node wrapper
around `@qvac/sdk`. That is not what this repo deploys.

<details><summary>Original (pre-measurement) claims, kept for the record</summary>

~~Launched with `--openai`, it exposes a genuinely OpenAI-compatible surface:
`POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings`,
`GET /v1/models`, SSE streaming, `response_format` JSON-schema support, tool
calling. Concurrency is `config.parallel: N` fixed decode slots served FCFS —
**not** vLLM's dynamic continuous batching / PagedAttention. Default context
window is a documented **1024 tokens** (the PRD requires 8k). It documents
no health endpoint, no metrics endpoint, and no TLS/mTLS support of any kind.~~
</details>

## What changed vs. `bkn301-inference`

| Area | Disposition |
|---|---|
| `gateway/upstream/metrics_scraper.py` | **Deleted.** Scraped vLLM's proprietary `/metrics` Prometheus series to derive the wedge signal. (At fork time QVAC was believed to have no metrics endpoint; corrected: `--metrics` exists, not yet scraped.) |
| `gateway/upstream/wedge.py` | **New**, replaces it. Derives the same wedge signature from the gateway's own dispatch/settlement bookkeeping (`UpstreamPool._send_and_record` now reports every attempt) instead of polling the upstream. Consumed by `gateway/upstream/breaker.py`'s `record_wedge()` with **zero changes to breaker.py** — that method already took an abstract boolean and never knew its old producer was vLLM-specific. |
| `gateway/main.py`'s `_scrape_loop` | **Deleted outright**, not replaced by an equivalent timer. The new signal is event-driven (updated on every dispatch/settle), so `_breaker_prober`'s existing tick just reads it — one fewer background task than vLLM had. |
| `gateway/metrics.py` | `upstream_up`, `upstream_scrape_age_seconds`, `upstream_scrape_failures_total` gauges deleted (nothing left to describe a scrape that doesn't happen). `upstream_wedge_suspected`/`upstream_wedge_stall_seconds` kept — same names, new producer. |
| `deploy/k8s/probes.yaml` | Rewritten: `/v1/models` instead of vLLM's `/health` for all three probes (matches what `UpstreamPool.health()`/`.probe()` already used). Every timing number is an **unverified placeholder** — vLLM's 153s/45s cold/warm numbers do not transfer. |
| `stub/server.py` | Drops vLLM's `/metrics` and `/health` emulation. Adds QVAC's documented quirks: `context_length_exceeded` 400 at a configurable ctx_size (defaults to a *properly-configured* 8192, not QVAC's raw 1024 default, so unrelated tests aren't silently broken by the gateway's own `MAX_COMPLETION_TOKENS` injection), vision content silently dropped from chat, and `--stub-parallel-slots` FCFS queueing in place of vLLM's continuous-batching model. |
| `tests/test_upstream_metrics.py` | Replaced by `tests/test_wedge.py` (pure state machine + a live-stub integration test) and `tests/test_qvac_quirks.py` (context-length and vision-drop fixtures). |
| `results/phase1-vastai-a10/`, `results/phase2-l4-wedge-argon2/` | Moved to `results/vllm-baseline/` — kept as the numeric baseline this fork's own measurements get compared against, not deleted. |
| Everything else — auth, JWT, audit, quota/concurrency, retry/backoff, breaker mechanics, mTLS *wiring*, model allowlist, env-var-only config | **Unchanged in logic.** Comments/docstrings updated where they named vLLM specifically; see the commit history for the exact list. |

## What's degraded, and why it's expected

- ~~**No continuous batching.**~~ *Corrected:* llama-server batches continuously
  across `-np` slots, but each slot has a fixed KV reservation and no cross-slot
  prefix sharing. The measured per-GPU ceiling is still materially lower than
  vLLM's (`HEAD_TO_HEAD.md`).
- ~~**No documented health/metrics endpoint of any kind.**~~ *Corrected:* `/health`
  and `--metrics` exist; the gateway does not use them yet. The wedge-detection
  redesign (`gateway/upstream/wedge.py`) is honestly scoped: it can only catch
  a wedge that has **real traffic against it**. An idle engine that wedges with
  nobody calling it is invisible until a periodic synthetic probe is built —
  deliberately not done in this pass (see checklist item 9).
- **No mTLS at the engine layer** (server-side TLS flags exist; no client-CA
  option, stock build has no OpenSSL). `gateway/upstream/client.py`'s mTLS
  wiring is unchanged and still works against *something* — deferred (plain
  HTTP) for the initial vast.ai test, matching `bkn301-inference`'s own
  precedent; a sidecar in front of QVAC is the live option before any real
  deployment (checklist item 6).
- **Tiny default context window (1024 vs. vLLM's effectively-unbounded
  `--max-model-len=8192` reality).** `gateway/routes/chat.py` still injects
  `MAX_COMPLETION_TOKENS` (2048) whenever a caller omits `max_tokens` — on a
  real deployment left at QVAC's raw default, this alone would 400 nearly
  every request. `ctx_size` **must** be explicitly configured well above
  2048 + a real prompt.

> **UPDATE 2026-10-01:** a first pass ran on an RTX 3090 (no A10 was rentable);
> see `docs/PHASE1_RESULTS_QVAC.md`. **Several claims above are wrong** — there
> is no `--openai` flag, `/health` and `/metrics` (`--metrics`) exist, TLS flags
> exist (`--ssl-*-file`), continuous batching is on by default, and `-c` is the
> total across `--parallel` slots. Item **5b is resolved** (1152 MiB per 8k
> slot, weights shared). Items 1, 2, 4, 7, 8 have partial 3090 findings there.
> ~~`deploy/k8s/probes.yaml` and `README.md` still carry the old flags and are
> not yet corrected.~~ Corrected 2026-10-03 (below).

> **UPDATE 2026-10-03:** the head-to-head ran on a real A10, the same box as vLLM
> (`docs/HEAD_TO_HEAD.md`). Items 1, 2, 4, 5b, 7 and 8 are now measured, and
> item 6 has new facts. `deploy/k8s/probes.yaml` and `README.md` now carry the
> real flags and measured values. Headline: at 8 users, warm, exact 7000+512,
> QVAC Q4_K_M holds **20.0 tok/s/user against vLLM's 43.2**, and it **fails R1
> with a cold prefix** (about 16 at 5 users, vs vLLM's 27.8). Run it with
> `-cram 0`, never `-kvu`.

## Verification checklist — must be measured on real rented A10 hardware

Status as of 2026-10-03: items 1, 2, 4, 5b, 7 and 8 are measured on an A10
(`docs/HEAD_TO_HEAD.md`). Each item should be checked off here, with
a link to the phase-doc section that measured it, once real hardware time
happens — mirroring `bkn301-inference`'s own Phase 1/2 methodology (rent,
measure, capture raw output into `results/`, write it up, only then treat a
number as real).

- [x] **1. Cold-start / warm-restart time** for the actual chosen launch config
      (model, quant, `ctx_size`, `parallel`). Sets
      `deploy/k8s/probes.yaml`'s `startupProbe.failureThreshold`.
      **Measured:** Q4_K_M np8 boots in 1.52–1.61 s warm and 2.96–2.98 s with the
      weights evicted; Q8_0 takes 3.1 s and 10–12 s. Restart after SIGTERM 1.66 s.
      probes.yaml now uses 2 s × 15 (HEAD_TO_HEAD.md §8).
- [x] **2. Whether `GET /v1/models` stays green through an engine freeze.**
      **Measured: no.** QVAC is one process, so `kill -STOP` hangs `/health` and
      `/v1/models`, and the gateway's `/readyz` goes 503 within one probe. The
      gateway's `wedge.py` gauge stayed at 0 for 150 s against both engines; it
      misses mid-stream stalls (HEAD_TO_HEAD.md §9). Original question:
      Repeat `docs/PHASE2_L4_RESULTS.md`'s real `kill -STOP` experiment against
      QVAC Fabric. Determines whether `gateway/upstream/wedge.py`'s signal is
      load-bearing in practice (expected: yes, same "API server ≠ engine loop"
      structure), and separately whether `record_probe`'s "a good probe alone
      is not sufficient" reasoning still holds.
- [ ] **3. Actual concurrent-slot memory cost.** Superseded by, and now
      tracked as, **item 5b** below — same question, moved next to the
      capacity math it determines.
- [x] **4. Real SSE opening-frame shape/timing.** **Measured:** the opening
      frame is `delta.content: null` (vLLM sends `""`), the usage frame has
      `choices: []` plus `timings`, then `[DONE]`. loadgen and the gateway handle
      both shapes. Original question: Confirms
      `gateway/routes/chat.py`'s TTFT measurement and `scripts/loadgen.py`'s
      timing assumptions, both currently carried over from vLLM's shape
      unverified.
- [x] **5a. Model/quant choice — DECIDED 2026-09-30, unverified in practice.**
      `Qwen/Qwen3-8B-GGUF`, **Q4_K_M** (5.03 GiB) — the same model as
      `bkn301-inference`'s `Qwen/Qwen3-8B-AWQ`, re-quantized. PRD §3.5 already
      evaluated this exact HF repo and rejected it **for vLLM specifically**
      ("GGUF support in vLLM is highly experimental... might be incompatible
      with other features", slower than AWQ's tuned Marlin kernels) — neither
      reason applies to QVAC Fabric, where GGUF is the native format, not a
      bolted-on afterthought. Q4_K_M mirrors AWQ's INT4 choice and its
      reasoning (decode is memory-bandwidth-bound on an A10, so smaller
      weights win). `Q8_0` (8.71 GiB) is the documented fallback if Q4_K_M's
      quality looks worse than AWQ's in practice — same role BF16 played as
      config A's fallback in PRD §3.3's table.
      **Redone capacity math** (PRD §3.3's method, Q4_K_M's 5.03 GiB in place
      of AWQ's 5.6 GiB, same measured ~22.06 GiB usable from
      `PHASE1_RESULTS.md`): ~16 GiB left for KV after weights + ~1 GiB
      activation/compute-buffer estimate (no CUDA-graph analog assumed), at
      vLLM's confirmed 144 KiB/token (same attention geometry, F16 KV cache)
      → **~116,000 tokens, ~2.9× headroom for 5×8k** — *better* than vLLM's
      measured 92,544/2.26×, **on paper**. This is conditional on checklist
      item 5b below; if that assumption is wrong, this whole estimate is
      wrong with it.
      **Still open:** the exact file/revision to pin (mirroring AWQ's
      commit-hash pin, not a moving tag), and llama.cpp's optional quantized
      KV-cache (`--cache-type-k`/`-v`) is unused in this estimate — a real
      lever if 116k tokens turns out optimistic.
- [x] **5b. Whether QVAC Fabric's HTTP server shares weights across
      `config.parallel: N` slots** the way a standard `llama-server` does
      (one loaded model, N independent KV-cache buffers) — the assumption
      5a's capacity math depends on entirely. Distinct from, and not the same
      finding as, the "parallel duplicates a full model copy" result an
      unrelated team found — that was specific to QVAC's SDK's separate
      Bare-worker-spawning path for concurrent completions, not this HTTP
      server's slot mechanism, which is architecturally closer to vanilla
      llama.cpp. Plausible, not confirmed. **The single highest-priority item
      in this checklist** — everything else about the expected KPI
      regression's *size* depends on this number specifically.
      **Resolved:** weights are shared. On the A10: 14,051 MiB at np8 (f16 KV),
      18,659 MiB at np12, np16 f16 fails to load, np16/20/24 with q8_0 KV fit in
      15.1/17.7/20.2 GiB. But capacity is not the binding constraint;
      **per-slot decode is**. At 8 slots per-user decode is already 20.0 tok/s, and
      the 5a "2.9× headroom on paper" does not turn into throughput
      (HEAD_TO_HEAD.md §8).
- [x] **6. TLS/mTLS at the QVAC layer — DEFERRED for the initial vast.ai
      test, 2026-09-30.** Plain HTTP + bearer token on the rented box's
      private network, matching `bkn301-inference`'s own Phase 1 precedent
      exactly (its mTLS work was gateway-side only and explicitly scoped as
      "the security review has to run against the real deployment path," PRD
      §4.8 — never validated against a live vLLM either). Revisit before any
      real (non-test) deployment: ~~QVAC documents no TLS support of its own~~
      (corrected below: server-side TLS flags exist, no client-CA option),
      so a sidecar reverse proxy terminating TLS and forwarding plaintext to
      QVAC on localhost is the live option if mTLS is required — unbuilt,
      not attempted this pass.
      **Update 2026-10-03:** `--ssl-key-file/--ssl-cert-file` exist, but the
      documented CUDA build has no SSL: the server exits with `the server is built
      without SSL support`. There is no client-certificate option at all, so mTLS
      still needs the sidecar.
      **Decision 2026-10-13: match the sister repo.** Gateway-side wiring only
      (`UPSTREAM_CLIENT_CERT_PATH/KEY_PATH/CA_BUNDLE_PATH`,
      `_upstream_ssl_context()`, `tests/test_mtls.py`). This code is identical to
      `bkn301-inference`'s, and like there it has never been run against a live
      engine. **Sidecar design (unbuilt, design only):**
      - A TLS terminator (nginx or envoy) in the QVAC pod listens on a private
        port with `ssl_verify_client on` and `ssl_client_certificate <private CA>`.
        It proxies to `llama-server` bound to `127.0.0.1:8000`, so the engine is
        never reachable off-pod in plaintext.
      - SSE needs `proxy_buffering off`, `proxy_http_version 1.1` and a
        `proxy_read_timeout` at or above the gateway's `UPSTREAM_READ_TIMEOUT_SEC`
        (120 s). Otherwise streams are buffered or cut.
      - kubelet probes cannot present a client cert. Probe `/health` on a plain
        side port that serves only `/health`, or use an `exec` probe hitting
        loopback. `/health` is unauthenticated and carries no prompt data.
      - The server cert/key and CA bundle are a mounted Secret, and rotation is
        left to DevOps, exactly as the sister leaves it. The PRD §4.8 security
        review has to run against this real deployment path.
      - Alternative, also unbuilt: patch the fork to call
        `SSLServer(cert, key, client_ca)` (cpp-httplib supports it) and build with
        `-DLLAMA_OPENSSL=ON`. That is more invasive and adds a fork delta to carry.
- [x] **7. k8s probe auth-header mechanics.** **Resolved:** `/health` needs no
      auth (measured), and `/v1/models`, `/metrics` and `/slots` return 401 without
      the key. probes.yaml now probes `/health`, so no probe token is needed.
      Original question: QVAC requires bearer auth on
      non-loopback binds by default; a kubelet `httpGet` probe likely counts
      as non-loopback even within the same pod. `httpGet.httpHeaders` only
      accepts a literal string (no `secretKeyRef`) — decide between a static
      probe-scoped token in the manifest and an `exec` probe reading a mounted
      Secret.
- [x] **8. Request/output-logging parity.** **Measured:** a canary prompt was
      absent from the default log, the `-v` log and `--log-file`. But
      `--log-prompts-dir` exists and writes prompts to disk, so forbid it
      (probes.yaml says so). Original question: vLLM was required to run with
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
