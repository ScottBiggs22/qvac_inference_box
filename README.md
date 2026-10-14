# bkn301-qvac-inference

An authenticating, OpenAI-compatible gateway that sits in front of a
self-hosted **QVAC Fabric** server on a single 24 GiB NVIDIA A10 GPU. It
exists because an OpenAI-compatible engine server typically has exactly one
authentication primitive at most — a single static shared secret — and **no
per-tenant identity, no rate limiting, no quotas, no per-key audit trail and
no request-size limits**. The engine must never be the edge; everything
security-shaped for this service lives here instead.

**This is a fork of `bkn301-inference`**, which fronts vLLM instead. That
swap is a management-directed business requirement, not a technical
improvement — `bkn301-inference`'s `main` already meets every PRD KPI on
vLLM, and this fork should be expected to regress on capacity/concurrency
until proven otherwise. **Read [`docs/QVAC_FORK.md`](docs/QVAC_FORK.md)
first** — it has the full disposition of what changed vs. the vLLM version,
what's degraded, and the checklist of what still needs measuring on real
hardware.

The gateway is consumed by a fintech platform ("FirstData")'s C#/.NET API
(`baas-poc-templates`) as a third LLM backend, alongside `llamacpp` and
`qvac`. It handles investor/KYC-adjacent data, which is why the security and
audit posture below is stricter than a typical internal proxy.

This doc is the onboarding entry point for DevOps and new developers. For
design rationale and full history, see [Where to go deeper](#where-to-go-deeper).

---

## Architecture

```
Client (C# API / Web / Mobile)
  │  TLS 1.3, short-lived JWT or API key
  ▼
Edge — Cloudflare Tunnel or LB + WAF
  ▼
Gateway  ← this repo
  │  authN/authZ · request validation · model allowlist
  │  metadata-only audit · SSE passthrough
  ▼  private subnet
QVAC Fabric replicas (no public IP)
```

The gateway itself is a single async FastAPI app (`gateway/main.py`), CPU/IO
bound — it does not need a GPU. QVAC Fabric is the GPU-bound piece it fronts.

| Path | Responsibility |
|---|---|
| `gateway/main.py` | App factory, lifespan-managed startup/shutdown; runs background tasks for breaker probing and event-loop lag monitoring. |
| `gateway/config.py` | Env-vars-only settings (no cloud SDK, ever — see [Configuration](#configuration)). |
| `gateway/auth/keys.py` | Argon2id-hashed API keys (`bkn_live_<keyid>_<secret>`), expiry, revocation, per-key token budget and concurrency limits. |
| `gateway/auth/jwt.py` | Asymmetric-only JWT validation (RS256) — the gateway can verify tokens but never mint them. |
| `gateway/routes/chat.py` | Chat completions (SSE + non-streaming), request validation, quota/concurrency gating. |
| `gateway/routes/health.py`, `models.py`, `metrics.py` | `/healthz`, `/readyz`, `/v1/models`, `/metrics`. |
| `gateway/upstream/client.py` | Replica pool, shared `httpx.AsyncClient`, streaming passthrough, bounded retries with jitter, mTLS wiring. |
| `gateway/upstream/breaker.py` | Per-replica circuit breaker (weighted consecutive-failure score, not a rate). |
| `gateway/upstream/wedge.py` | Derives a "wedge" (frozen-engine) signal from the gateway's own dispatch/settlement bookkeeping — written when QVAC Fabric was believed to have no `/metrics`; it has one (`--metrics`), not yet scraped. |
| `gateway/quota.py` | Token-budget and in-flight-concurrency enforcement, wired into `chat.py`. |
| `gateway/audit.py` | Metadata-only audit log writer — never prompt/completion text. |
| `gateway/metrics.py` | Prometheus metric definitions + ASGI middleware. |
| `stub/server.py` | Deterministic, unauthenticated fake OpenAI-compatible upstream — lets the whole gateway run with no GPU. Excluded from the packaged distribution and the production image. |
| `scripts/` | Ops tooling: `pack_for_box.sh` (audited transfer to a rented GPU box), `verify_box.sh` (first-boot GPU + data-hygiene gate), `loadgen.py` (tok/s and TTFT benchmarking), `b3_probe.py` (Qwen3 `<think>`-block probe), `dev_key.sh` (mint a local dev key). |
| `deploy/` | `Dockerfile.gateway` (multi-stage: build → runtime → dev), `docker-compose.yml`, `k8s/probes.yaml`. |
| `docs/` | Design docs, session history, operational runbooks — see [Where to go deeper](#where-to-go-deeper). |

**Design choices worth knowing up front:**

- **Streaming is passed through, not buffered.** At ~20 tok/s a 200-token
  answer takes ten seconds; buffering it to inspect the body would mean ten
  seconds of blank screen. The gateway cannot post-process a streamed answer
  — validators needing the whole text live at the consuming end.
- **The audit log is metadata only** — timestamp, key id, model, token
  counts, latency, status. Never prompt or completion text. That's the
  difference between an audit trail and a PII store on a platform handling
  investor/KYC data.
- **JWT validation is asymmetric only**, and a symmetric algorithm is refused
  at construction, so compromising the gateway cannot forge caller identity.

---

## Deployment model

Carried over unverified from the vLLM version: a **whole 24 GiB A10 GPU per
replica** — no MIG, no fractional/vGPU sharing. vLLM's version of this rule
came from KV-cache pre-allocation at startup (a second pod on the same card
failed at boot); whether QVAC Fabric's memory model forces the same
constraint is unverified (`docs/QVAC_FORK.md`). The gateway itself is IO-bound
and runs anywhere; scale it via replicas, not worker processes (`--workers 1`
in the shipped image).

### Local development, no GPU needed

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# 1. the fake upstream
.venv/bin/python -m stub.server --port 8001

# 2. a throwaway key
export API_KEY_PEPPER=local-dev-pepper
.venv/bin/python -c "
from gateway.auth.keys import mint_key, KeyStore
s = KeyStore('keys.json'); pt, rec = mint_key(scopes=['chat'], label='dev')
s.add(rec); s.save(); print(pt)"

# 3. the gateway
.venv/bin/python -m uvicorn gateway.main:app --port 8080
```

```bash
curl -H "Authorization: Bearer $KEY" http://127.0.0.1:8080/v1/models
```

Or the whole stack in containers, with no local Python at all:

```bash
bash scripts/dev_key.sh          # mints deploy/local-keys.json — do this FIRST
docker compose -f deploy/docker-compose.yml up --build
```

(`dev_key.sh` is a prerequisite, not a convenience: compose bind-mounts
`deploy/local-keys.json` read-only, and Docker turns a missing read-only bind
into a directory, which leaves the gateway unable to authenticate anybody.)

**Docker image:** two build targets — `runtime` (ships, gateway only) and
`dev` (`runtime` + `stub/`, for compose). The default/final stage is
explicitly pinned to alias `runtime`: a past bug had `dev` as the last stage,
so a plain `docker build` with no `--target` shipped the unauthenticated stub
as the default image. CI now asserts the stub isn't importable in the runtime
image, no compiler survives into it, and it doesn't run as root.

**vast.ai** is used only as a temporary rented-GPU box for dev/benchmarking
(never production — see `docs/VAST_AI_RUNBOOK.md` / `docs/VAST_AI_OPERATIONS.md`).

### Provisioning a GPU host (staging/production)

This is what DevOps needs to have, do, or run — the specifics come from
DevOps's own answers recorded in PRD §4.1 and §4.4, not from guesswork.

- **Shape/SKU.** OCI: `VM.GPU.A10.1` — confirmed and quota-approved. Baseline
  count is 1 dev / 1 staging / 2 prod, adjusted by measured throughput
  (`docs/PHASE1_RESULTS.md`). Azure (currently the staging plan): the
  `NVadsA10_v5` series is **SR-IOV partitioned by default**, from 1/6 of a
  card (4 GiB frame buffer) up to a full 24 GiB card — a fractional size
  boots and serves normally but **silently breaks the 8k-context
  requirement**. The SKU must be confirmed as a full-card size (believed
  `NV36ads_A10_v5` or larger — verify against current Azure docs before
  provisioning). This is the single highest-priority open question with
  DevOps (PRD §8, Q1).
- **One whole GPU per pod, never a fraction.** `nvidia.com/gpu: 1` in the pod
  spec. A10 has no MIG (that's A100/A30/H100 only), so nothing else —
  no second container, no graphics workload, no other `nvidia-smi`-visible
  tenant — may share the card.
- **Driver/backend.** UNVERIFIED for QVAC Fabric (`docs/QVAC_FORK.md`
  checklist): which backend build (Vulkan vs. CUDA, both supported on this
  same NVIDIA hardware) and which driver combination is actually chosen and
  tested has not been decided. vLLM's own requirement here was a CUDA 13.0
  build needing driver 595.84+/CUDA 13.2 — informative history, not a QVAC
  requirement.
- **Disk.** ≥15 GB free — `Qwen/Qwen3-8B-GGUF` Q4_K_M is ~5 GB (vs. vLLM's
  AWQ weights at ~5.6 GB plus its larger HF-cache/compile-cache footprint),
  plus image layers. See `docs/QVAC_FORK.md` checklist item 5a.
- **Network.** Replicas get **no public IP** — private subnet only,
  reachable solely by the gateway.
- **Fault-domain placement (OCI).** Put the dev VM in a different fault
  domain from every production VM, and the two production VMs in different
  fault domains from each other — two prod replicas on one physical node is
  not actually HA. Staging can share a fault domain with dev (lower risk
  pairing). Fault domains are free and chosen at launch time; confirm with
  DevOps that they're actually being placed this way (PRD §8, Q2).
- **Which environment is the performance reference.** State explicitly that
  OCI (once its "functional but not in use" staging is exercised) is the
  capacity/throughput reference, and Azure staging is application-integration
  only — a green Azure run should not be read as evidence the tok/s target is
  met if its card isn't production-identical (PRD §4.4c, §8 Q3).

### Running QVAC Fabric

Measured on a rented A10 on 2026-10-02/03, the same box as vLLM. The full comparison is
`docs/HEAD_TO_HEAD.md`, and the remaining open items are in `docs/QVAC_FORK.md`'s checklist.

- **Headline:** at 8 concurrent users, exact 7000+512, cached prompt, QVAC Q4_K_M
  holds **20.0 tok/s/user against vLLM's 43.2** on the same card, at about 2× the cost
  per token. With an uncached prompt it **fails R1** (about 16 tok/s/user at 5 users).
  The bar is 43.2, not the older "~48 tok/s/user at 8": that figure came from a loadgen
  that actually sent ~5.3k-token prompts (`HEAD_TO_HEAD.md` §1, §4).
  It starts in 1.5–3 s (vLLM takes 41–90 s).
- Image/tag: still not pinned, because no prebuilt CUDA image exists. The measured
  binary was built from `tetherto/qvac-fabric-llm.cpp` @ `7bb2161c690b…` with
  `cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86` (CUDA 13.0). That
  build is not hermetic and has no SSL support.
- Model: `Qwen/Qwen3-8B-GGUF` @ `7c41481f…`, `Qwen3-8B-Q4_K_M.gguf`
  (sha256 `d98cdcbd…5785`). Q8_0 measured as a control: slightly faster at 8
  users (22.9), slower at 1 user (48.6), and no answer-quality gain.
- Launch flags (`deploy/k8s/probes.yaml`): `-m <gguf> -c 65536 -np 8 -ngl 99 -fa on
  -cram 0 --metrics --alias qwen3-8b --api-key …`. **Do not add `--reasoning-format none`**:
  it breaks `json_schema` with `enable_thinking:false` (HTTP 400), so leave the default `auto`
  (`docs/QVAC_FIXES_RESULTS.md`). `-c` is the
  **total** context across slots (8192 per slot). **`-cram 0` is required**: the default
  host-RAM prompt cache adds 2.5–4.4 s of TTFT under concurrency. **Never use
  `-kvu`.** There is no `--openai` flag.
- Logging: no prompt text in the default or `-v` logs (checked with a canary).
  Never set `--log-prompts-dir`.
- Probes: `deploy/k8s/probes.yaml` probes `/health`, which exists and needs no auth.
  Timings are sized to the measured boot (startup 2 s × 15). QVAC exits 0.6 s after
  SIGTERM and **cuts in-flight streams**, so the manifest drains with a `preStop`
  sleep. Mechanism and reasoning are in `docs/PROBE_CONTRACT.md`.
- Not supported on this build: cross-slot prefix-cache sharing, native TLS (needs an SSL
  build) and mTLS (no option exists). Tool calls and `response_format: json_schema` work
  (5/5 each). `json_object` is not enforced when thinking is off, so use `json_schema`.
- HA target: ≥2 production replicas, in different fault domains, listed
  together in the gateway's `UPSTREAM_URLS` — unchanged from the vLLM plan,
  but QVAC's real per-replica concurrent-user capacity (checklist item 3) is
  what determines how many replicas that target actually needs.

### Running the gateway

- Build `deploy/Dockerfile.gateway --target runtime` explicitly and push to
  whichever registry the environment uses (OCIR on OCI, ACR on Azure — same
  shape, no image reuse across environments). CI's `image` job builds both
  targets and asserts the stub isn't in the runtime image, no compiler
  survived into it, and it doesn't run as root — treat a failure there as a
  blocker, not a warning.
- Run one process per pod, `--workers 1` (already the image's `CMD`) — the
  gateway is IO-bound, so scale by adding replicas, not workers. Exposes
  `8080`. Already runs non-root.
- **No Kubernetes manifest exists yet for the gateway itself** —
  `deploy/k8s/probes.yaml` is the upstream engine's only. Whoever deploys
  this needs to write a Deployment + Service (+ Ingress/edge wiring) for the
  gateway, with its liveness/readiness pointed at the gateway's **own**
  `/healthz` and `/readyz` — not the upstream's `/v1/models` (a mix-up the
  PRD explicitly calls out as having happened once already, back when the
  upstream endpoint in question was vLLM's `/health`).
- Edge: TLS termination and rate-limiting/WAF sit in front of the gateway
  (Cloudflare Tunnel or LB+WAF) — the gateway itself should have no public
  exposure beyond that edge. `/metrics` is unauthenticated by design, so it
  needs a network policy restricting who can reach it.

### Secrets and config to provision

Everything below arrives as an **environment variable** — no cloud SDK is
baked into the image, so whatever secret store the platform uses (OCI Vault +
instance principal, or Azure Key Vault + managed identity) just needs to land
as env vars or mounted files the env vars point at:

| Variable | What it is |
|---|---|
| `UPSTREAM_API_KEY` | The upstream's static shared secret (QVAC Fabric's `--api-key`) — private-subnet-only, never leaves it |
| `API_KEY_PEPPER` | Argon2id pepper, generated per environment, never committed, never stored beside the key store |
| `JWT_PUBLIC_KEY` | The issuing API's asymmetric signing public key (RS256) — the gateway must never hold a private or symmetric key |
| `UPSTREAM_CLIENT_CERT_PATH` / `UPSTREAM_CLIENT_KEY_PATH` / `UPSTREAM_CA_BUNDLE_PATH` | mTLS material for gateway→upstream — sourced from whatever CA issues certs on the private subnet. Gateway-side wiring only, same as `bkn301-inference`. QVAC's `llama-server` has server-side TLS flags (stock build lacks OpenSSL) but no client-CA option, so engine-side mTLS needs a sidecar — design only, `docs/QVAC_FORK.md` item 6 |
| `API_KEY_STORE_PATH` | Writable path for the per-environment API key registry (generated, not committed) |
| `AUDIT_LOG_PATH` | Writable/persisted path for the metadata-only audit log |

### Observability to wire up

- Prometheus scrape target: the gateway's own `/metrics` (behind a network
  policy). QVAC Fabric's `llama-server` also serves a Prometheus `/metrics`
  (`--metrics`, set in `probes.yaml`) that can be scraped like vLLM's was.
  The wedge signal, though, is still derived from the gateway's own
  dispatch/settlement bookkeeping (`gateway/upstream/wedge.py`), not scraped.
- Alerts worth having from day one: tok/s/user below target, TTFT p95,
  circuit-breaker trips, and `bkn301_gateway_upstream_wedge_stall_seconds`
  crossing its (currently estimated, not measured) window. (KV-cache
  utilization was a vLLM-specific series with no QVAC equivalent to alert on.)
- The old PRD recommended a small sidecar scraping vLLM's `/metrics` as an
  automated backstop behind the gateway's own circuit breaker. That specific
  design has no QVAC equivalent to build (nothing to scrape) — the honest gap
  it leaves (a wedge with zero real traffic against it is invisible) is
  tracked as `docs/QVAC_FORK.md` checklist item 9 instead.

### Open questions this repo can't close alone

These need a DevOps (or product) answer before staging/production sizing and
placement are real — see PRD §8 for the full list:

- Is the Azure staging SKU actually a full 24 GiB card, or a fractional
  `NVadsA10_v5` partition? (Highest priority.)
- Can the four VMs (dev/staging/2×prod) actually be placed in different
  fault domains, or do they land on the same physical node?
- Has the chosen engine been validated on an Azure vGPU (NVads/GRID) profile
  at all? Not yet, as of this writing (true of vLLM before, and doubly true
  of QVAC Fabric, which has no production track record on this stack at all)
  — treat it as a spike, not a given, if Azure is firm.
- What's the network path from the consuming app to the gateway — same-VCN,
  or a tunnel/VPN/cross-region hop? This is currently the single largest
  unknown in the latency budget.
- When will production cloud (OCI vs. Azure) actually be decided?

---

## Configuration

**Environment variables are the only config surface** — no cloud SDK, no
instance principal, no managed identity anywhere in the image, because the
target cloud is still undecided and every candidate platform supports "inject
an env var." `tests/test_config.py` enforces this. Full reference:
[`.env.example`](.env.example) and `gateway/config.py`.

Knob categories:

| Category | Examples |
|---|---|
| Server | `GATEWAY_HOST`, `GATEWAY_PORT` |
| Upstream QVAC Fabric | `UPSTREAM_URLS` (comma-separated replicas), `UPSTREAM_API_KEY`, connect/read timeouts, mTLS cert/key/CA paths |
| Resilience | `BREAKER_ENABLED`, `BREAKER_OPEN_SEC`, `BREAKER_PROBE_TICK_SEC` |
| Authentication | `API_KEY_STORE_PATH`, `API_KEY_PEPPER`, `AUTH_VERIFY_CONCURRENCY`, JWT public key/issuer/audience |
| Request validation | `ALLOWED_MODELS`, `MAX_BODY_BYTES`, `MAX_COMPLETION_TOKENS`, `MAX_PROMPT_TOKENS` |
| Audit | `AUDIT_LOG_PATH`, `AUDIT_INCLUDE_PROMPT_TEXT` (must stay `false`) |
| Observability | `METRICS_ENABLED`, `UPSTREAM_WEDGE_WINDOW_SEC` |

`/metrics` is **unauthenticated on the main port**, like any Prometheus
target — restrict it with a network policy. That's why no label carries a
`keyid`, an upstream URL, or any caller-supplied string.

---

## Testing

Framework: **pytest** (config lives entirely in `pyproject.toml`, no
Makefile). No GPU or external services are needed anywhere in the suite —
`stub/server.py` fakes the OpenAI surface, so it's fully laptop-runnable.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

This is exactly what CI runs (`.github/workflows/ci.yml`, `check` job):
`ruff check .` then `pytest`. A separate `image` job builds both Docker
targets and asserts the image-hygiene properties above.

| Test file | Covers |
|---|---|
| `test_auth.py` | Key format/roundtrip, revoked/expired/unknown-key rejection, JWT verifier (asymmetric-only, algorithm-confusion incl. `alg=none`), missing/garbage credentials → 401. |
| `test_auth_concurrency.py` | Argon2id verification runs off the event loop and is bounded. |
| `test_audit_no_prompt_text.py` | The audit log never contains prompt/completion text, asserted with a canary string against the real writer. |
| `test_request_validation.py` | Malformed requests return 4xx, never 500. |
| `test_config.py` | Env-var-only config surface: `UPSTREAM_URLS` list parsing, mTLS cert/key/CA validation. |
| `test_breaker.py` | Pure breaker state machine (injected clock): trip thresholds, wedge-signal confirmation, open/reclose backoff, probation. |
| `test_breaker_routing.py` | Breaker as the router sees it over a real socket: all-replicas-open fails fast (not a 120s hang), `/readyz` behavior, half-open recovery. |
| `test_retries.py` | Bounded retries with jitter on 429/502/503/504 — connect errors retried, read timeouts never retried, `Retry-After` handling, `MAX_ATTEMPTS == 2`. |
| `test_metrics.py` | `/metrics` endpoint format, cardinality bounds, request instrumentation. |
| `test_wedge.py` | Wedge-signal state machine (pure, clock-injected) plus a live-stub integration test proving the real dispatch path produces the signature — replaces `bkn301-inference`'s `test_upstream_metrics.py` (written when QVAC was believed to have no `/metrics`; it has one, not yet scraped). |
| `test_qvac_quirks.py` | QVAC-documented behaviors the stub fakes: `context_length_exceeded` at a configured ctx_size, vision content silently dropped from chat. |
| `test_mtls.py` | Gateway→upstream mTLS wiring: rejects missing/wrong-CA client certs, succeeds with a correct chain, fails closed if not configured. |
| `test_quota.py` | Per-key token budgets and concurrency caps, including that a streamed response holds its concurrency slot for the life of the stream. |
| `test_streaming_status.py` | The streaming path reports the upstream's real HTTP status (regression test for a prior bug where a 503 reached the caller as 200). |
| `test_loadgen.py` | `scripts/loadgen.py` end-to-end: concurrent workers genuinely overlap, TTFT/decode-rate reporting. |
| `test_pack_for_box.py` | `pack_for_box.sh`'s deny-list refuses to ship data/credential decoys. |
| `test_verify_box.py` | `verify_box.sh`'s data-hygiene scan (`--self-test`/`--scan-only` — GPU checks can't run on a laptop). |

A handful of tests (anything using the `live_stub` or `live_gateway`
fixtures — loadgen, breaker-routing's real-socket cases, retries-over-a-real-stub,
quota concurrency, streaming-status, mTLS) spawn a **real subprocess** and
health-poll it against a 30-second wall-clock deadline. **If the laptop
sleeps mid-run, this class of test can fail spuriously** (the poll loop
burns its budget without the subprocess getting real wall-clock time to
boot) — that's a stall artifact, not a regression, if you see it.

---

## Current status

This fork's code-level swap is done and tested: the wedge signal is now
derived from gateway-side dispatch bookkeeping instead of scraping vLLM's
`/metrics` (`gateway/upstream/wedge.py`), the stub emulates QVAC's documented
quirks instead of vLLM's, and `deploy/k8s/probes.yaml` targets `/v1/models`.
Full suite green, laptop-only, no GPU — see `docs/QVAC_FORK.md` for the
complete disposition.

**Nothing about this fork has been run against a real QVAC Fabric server
yet.** `docs/QVAC_FORK.md`'s verification checklist — cold-start timing,
whether `/v1/models` survives an engine freeze, real concurrent-slot memory
cost, real SSE framing, the actual model/quant choice, TLS/mTLS placement —
is entirely open. Nothing in `deploy/k8s/probes.yaml` or the PRD's capacity
math should be treated as tuned until that checklist closes.

`bkn301-inference`'s own vLLM-era status (PRD Phase 0–2 complete, KPIs met on
a real A10, Azure full-card-SKU question still open) is preserved for
reference in `docs/HANDOFF.md`'s history and is **not** this fork's current
status.

---

## Where to go deeper

| Doc | What it is |
|---|---|
| `docs/QVAC_FORK.md` | **Read this first.** Why this fork exists, what changed vs. `bkn301-inference`, what's degraded, and the open verification checklist. |
| `docs/QVAC_FABRIC_LAUNCH_PROMPT.md` | Paste-ready prompt for the first real QVAC Fabric session on a rented A10 — working the verification checklist on real hardware. |
| `docs/HANDOFF.md` | Session history and current state — read next if picking this up cold. |
| `docs/INFERENCE_SERVICE_PRD.md` | Design source of truth: requirements, stack feasibility, deployment contract, security design, phased plan. |
| `docs/PROBE_CONTRACT.md` | Health-probe design, extracted for DevOps. |
| `docs/VAST_AI_RUNBOOK.md` | Getting onto a rented A10 for dev/benchmarking. |
| `docs/VAST_AI_OPERATIONS.md` | vast.ai booking/session-management mechanics. |
| `docs/PHASE1_RESULTS.md`, `docs/PHASE2_L4_RESULTS.md` | Pre-fork vLLM baseline measurements (`results/vllm-baseline/`) — the numbers this fork's own QVAC measurements get compared against, not this repo's current numbers. |
| `docs/NEXT_SESSION_PROMPT.md` | Paste-ready prompt for picking up a fresh working session. |
