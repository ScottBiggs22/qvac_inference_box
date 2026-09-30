# Health probe contract — QVAC Fabric and the inference gateway

**Status:** proposed, for DevOps review
**Date:** 2026-09-09 (vLLM original); fork-adapted for QVAC Fabric, see banner below
**Owner:** scott.biggs@bkn301.com
**Source:** `INFERENCE_SERVICE_PRD.md` §4.6, extracted so it can be reviewed on its own
**Applies to:** `deploy/k8s/probes.yaml`

> **FORKED FROM bkn301-inference (docs/QVAC_FORK.md).** Everything below was
> written against vLLM and is kept because most of the *reasoning* (§1, §3,
> most of §6) is engine-agnostic and still applies. What does NOT carry over
> unchanged: every endpoint name (vLLM's `/health` → QVAC Fabric's
> `/v1/models`, since QVAC documents no health endpoint of any kind) and
> every measured timing number (§5) — those were real measurements of vLLM on
> a real A10, and QVAC Fabric has no equivalent measurement yet. §4's
> real-freeze account is kept in full because it is the direct reason
> `gateway/upstream/wedge.py` exists as a dispatch-bookkeeping mechanism
> rather than a metrics scrape — see the note inserted there.

---

## 1. Why this needs its own document

DevOps asked for the probe internals and was right that they are not standard:

> "the way LLM inferencing works is different from general k8s semantics."

Two properties of an LLM server break the usual defaults, and they break them in
opposite directions:

| Property | What it breaks |
|---|---|
| Model load takes **30–60s** | A liveness probe with normal timings kills the pod before it ever serves |
| A legitimate generation takes **10s+** under load | A readiness or liveness probe tuned for a web app marks a *healthy, working* pod as failed |

Getting either wrong is expensive rather than merely noisy: a restart costs
another full model reload, and a restart loop costs the service.

---

## 2. The division of labour

| Probe | Endpoint | Checks | Timings | Why |
|---|---|---|---|---|
| `startupProbe` | `/v1/models` (was vLLM's `/health`; QVAC Fabric has no `/health` — docs/QVAC_FORK.md) | Engine initialised, assumed | `periodSeconds: 5`, `failureThreshold: 60` → **300s budget, UNVERIFIED for QVAC** | vLLM returned 200 here only once the engine was up — whether QVAC's `/v1/models` has the same "not ready until loaded" property is unconfirmed. **While this runs, Kubernetes suppresses the other two** — which is what makes them safe to tune tightly, if the timing is right. **vLLM's number was measured 2026-09-10, see §5; QVAC Fabric's is not.** |
| `readinessProbe` | `/v1/models` | Engine health **only** | `periodSeconds: 10`, `failureThreshold: 2` | Answers "is this instance broken", never "is it busy". See §3 — this reasoning is engine-agnostic and matters MORE under QVAC's FCFS `config.parallel: N` queueing, where "busy" is more common than it was under vLLM's continuous batching. |
| `livenessProbe` | `/v1/models` | Engine loop alive, assumed. **Never a test generation.** | `periodSeconds: 20`, `timeoutSeconds: 5`, `failureThreshold: 3` → ~60s | Restart costs an unmeasured reload time (30–60s was vLLM's), so this is the last resort. |

The gateway exposes the same split on its own process: `/healthz` for liveness,
`/readyz` for readiness. `/readyz` goes red only when **no** upstream replica is
reachable — one replica down is what having replicas is for, and belongs in an
alert rather than in this endpoint's verdict.

---

## 3. Do not put queue depth in the readiness probe

This is the single most important line in the document, because the wrong design
is the intuitive one.

If `/readyz` goes red when an instance is **busy**, then under a traffic spike
every replica reports not-ready at once, Kubernetes removes all of them from the
endpoint list, and the service fails **precisely at peak load** — the moment it
exists to handle. The failure is self-reinforcing: removing replicas concentrates
load on whatever is left, which makes them report busy too.

Readiness answers *"is this instance broken"*. Load-aware routing belongs in the
gateway, which already tracks in-flight requests per replica and can shed or
queue without removing anything from service discovery.

---

## 4. Detecting a genuinely wedged model

> **THIS SECTION IS HISTORICAL — read it for the real-freeze findings, not
> for the mechanism.** Everything below describes vLLM's `/metrics`-based
> detector, which `gateway/upstream/metrics_scraper.py` implemented and which
> this fork has **deleted**: QVAC Fabric exposes no metrics endpoint at all,
> so there is nothing to scrape. `gateway/upstream/wedge.py` replaces it with
> a signal derived from the gateway's own dispatch/settlement bookkeeping
> instead — see that module's docstring. The finding below (§4's corrected
> account, confirmed 2026-09-15) is the direct reason that replacement is
> shaped the way it is: it does not depend on trusting the engine's own
> internal counters, which is exactly the channel that turned out not to
> reflect a real freeze. Whether the NEW mechanism catches this same freeze
> shape on QVAC Fabric is docs/QVAC_FORK.md checklist item 2 — unverified,
> not assumed.

A wedged engine does not fail an HTTP probe — the process is alive and the
socket answers. vLLM's signature was a **relationship between two metrics
over time**, which is why no single endpoint could express it:

> `vllm:num_requests_waiting > 0` **and** `vllm:generation_tokens_total` has not
> advanced for N seconds.

Relevant series from vLLM's `/metrics` (historical — no QVAC equivalent exists):

| Metric | Use |
|---|---|
| `vllm:num_requests_waiting` | queued but not started |
| `vllm:num_requests_running` | in flight |
| `vllm:generation_tokens_total` | a counter — **flat while `waiting > 0` is the wedge** |
| `vllm:time_to_first_token_seconds` | the TTFT alert in PRD §7 Phase 4 |

Two mechanisms, with different roles — we recommend both:

1. **Gateway circuit breaker (primary).** ~~The gateway sees the stall
   first~~ — see the correction below; this is true for a *read-timeout*
   stall, not for the specific freeze shape measured on 2026-09-15. No
   restart, no dropped work, human in the loop.
2. **Sidecar backstop (automated).** Scrapes `/metrics` and fails `/healthz` if
   the signature holds for a **conservative** window — 120s, not 20s. This is
   what finally trips the `livenessProbe`.

The weighting is deliberate: **an automatic restart on a false positive is worse
than a late alert**, because it costs a minute of reload and can loop. Start with
the breaker and a generous sidecar window, then tighten once real failure data
exists.

> **Measured 2026-09-15 on a rented L4 (`docs/PHASE2_L4_RESULTS.md` §2b) — the
> signature above does not fire for a realistic freeze, at any window.**
>
> `kill -STOP` on the vLLM `EngineCore` process (leaving the API server
> process alive — precisely "the process is alive and the socket answers"
> from the opening sentence of this section) was held for **~201 seconds**
> with a genuinely pending chat request. Throughout: `generation_tokens_total`
> stayed exactly flat (confirming the freeze was real), but
> **`vllm:num_requests_waiting` never left 0** — the new request appears to
> block before reaching whatever internal structure vLLM increments into
> `waiting`, plausibly because admission itself needs a round trip to the same
> frozen process. `bkn301_gateway_upstream_wedge_stall_seconds` therefore never
> moved off 0, and neither would any window length: the precondition
> `waiting > 0` is simply never satisfied by this failure shape.
>
> The breaker is **not** blind to this outage — it is just slower than
> intended. `gateway/upstream/breaker.py`'s `FailureKind.READ_TIMEOUT` carries
> weight 3, tripping at 2, so a request that eventually times out against
> `UPSTREAM_READ_TIMEOUT_SEC` (120s) does count. But that needs **two** such
> timeouts, ~240s+ with jitter — not the faster, complementary role item 1
> above assigns it ("sees the stall first"). For this specific freeze shape,
> the ordering is inverted: the metrics-based detector (item 2) never fires at
> all, and the breaker (item 1) closes the loop later, via the exact
> read-timeout path it was meant to beat.
>
> **Separately confirmed, and good news on its own:** a real engine survived a
> 201-second freeze and resumed cleanly on `kill -CONT` — process state
> normal within 3s, the pending request completed with correct content, and
> `generation_tokens_total` advanced by exactly the requested token count. No
> corruption from an extended freeze well past the 120s window this design
> was built around.
>
> **Now built, in this fork, as `gateway/upstream/wedge.py`:** a signal that
> does not depend on the frozen engine's own counters — it tracks, per
> replica, whether a dispatched chat-completion attempt has been outstanding
> longer than the window with no completions recorded for that replica in the
> meantime. This is a direct implementation of the "not yet built" idea named
> here. What it does **not** yet do: a synthetic probe request sent through
> the same code path with zero real traffic against a replica — that remains
> unbuilt (docs/QVAC_FORK.md checklist item 9), because the new mechanism can
> only observe a stall that has a real dispatch to measure the age of. And
> whether it actually catches THIS freeze shape (`/v1/models` answering
> normally, same as `/health` and `/models` did here) on a real QVAC Fabric
> server is unverified — checklist item 2.

---

## 5. Which numbers are measured, and which are still estimates

> **All of this is the vLLM-era record (`results/vllm-baseline/`).** No QVAC
> Fabric equivalent measurement exists yet — see docs/QVAC_FORK.md's
> checklist items 1–3. A `docs/PHASE1_RESULTS_QVAC.md` following this same
> methodology is the thing that should eventually replace this section's
> numbers for THIS repo, the same way this table replaced an earlier guess.

Phase 1 (2026-09-10, full-card A10) measured one of the two and **not** the
other. The distinction is load-bearing, so it is tabulated rather than described:

| Number | Status | Value |
|---|---|---|
| Cold-start budget → `startupProbe.failureThreshold` | **MEASURED** | 45s warm, **153s cold** → `failureThreshold: 60` (300s) |
| Liveness/readiness timings | derived from the above | unchanged |
| **Wedge-detection window, false-positive side** (heavy legitimate load) | **MEASURED, 2026-09-15** | No flat-counter plateau beyond sub-poll-interval noise, even at 99.9% KV usage and a 27-deep queue for 40s straight — see `PHASE2_L4_RESULTS.md` §2a |
| **Wedge-detection window, true-positive side** (does the signature fire on a real freeze) | **MEASURED, 2026-09-15 — it does not** | `waiting` never left 0 across a 201s freeze; window length is moot when the precondition never becomes true — see §4's correction above and `PHASE2_L4_RESULTS.md` §2b |

**The cold-start figure corrects this document in the dangerous direction.** It
previously specified `failureThreshold: 24` — a 120s budget against a 153s
measured cold start. Kubernetes would have killed the pod at 120s, and the
replacement would also have started cold: a boot loop, not a slow start.
`deploy/k8s/probes.yaml` now carries 60, about 2× the measurement. Generous on
purpose — a loose startup probe costs a slower failure verdict, a tight one costs
a loop that never converges.

**The wedge window is no longer an unexamined guess, and the answer is not the
one this section originally expected.** Two things were true before
2026-09-15: the window's *value* (120s) was unvalidated, and — it turns out —
so was the unstated assumption that the signature would fire on a real wedge
at all. The false-positive side is now reassuring (§4's correction, heavy
load never produces a multi-second flat counter). The true-positive side is
not: for a real engine-process freeze with the API server alive, the
signature's precondition (`waiting > 0`) never activates, so no window length
would have caught it via this mechanism. The breaker still provides a
backstop via `READ_TIMEOUT` weighting, just a slower one (~240s+, two
timeouts) than the ~120s this section describes. See §4's correction and
`PHASE2_L4_RESULTS.md` §2 for the full account, including what remains to be
built.

---

## 6. Constraints that sit alongside the probes

From PRD §4.5, repeated here because they belong to whoever writes the manifest.
Same hardware class as the vLLM deployment (a cluster of A10s) throughout —
these are mostly hardware facts, not engine facts, so most carry over as-is;
where a constraint's REASON was specifically about vLLM's behavior, that's
flagged rather than silently kept:

1. **One whole GPU per pod** — `nvidia.com/gpu: 1`, never a fraction, kept as
   the conservative default. vLLM's REASON was KV-cache pre-allocation at
   startup, so a second vLLM pod on the same card **failed at boot** rather
   than degrading. Whether QVAC Fabric's memory model shares this property is
   **unverified** (docs/QVAC_FORK.md) — the constraint is kept because
   violating it was expensive under vLLM, not because it's confirmed true
   here.
2. **The A10 does not support MIG** (A100/A30/H100 only) — a hardware fact,
   unchanged. Partitioning means time-slicing or MPS, neither of which gives
   memory isolation, regardless of which engine sits on top.
3. **The card must be a full 24 GiB A10, not a vGPU partition.** Azure's
   `NVadsA10_v5` sizes start at one-sixth of a card with a 4 GiB frame buffer.
   A fractional size boots, serves, and would silently break an 8k context
   requirement under either engine — see PRD §4.4(a), which is the
   highest-priority open question and is unrelated to the vLLM→QVAC swap.
4. **Nothing else may share the card** — unchanged, same hardware.
5. **Tensor/parallel-slot count is UNVERIFIED, not "none."** vLLM ran with no
   tensor parallelism because an 8B AWQ model fit one card with no NVLink to
   split across anyway. QVAC Fabric's analogous knob, `config.parallel: N`
   (FCFS decode slots, not tensor parallelism), has its real memory cost on
   this hardware unmeasured — docs/QVAC_FORK.md checklist item 3, the single
   biggest unknown behind this fork's expected KPI regression.
