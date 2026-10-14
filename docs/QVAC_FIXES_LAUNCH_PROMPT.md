# Launch prompt — QVAC Fabric: fix JSON/thinking, align TLS with the sister repo, re-check parallelism

**Written:** 2026-10-13, after `docs/HEAD_TO_HEAD.md` landed.
**Purpose:** a fresh agent executes the plan below end to end. Start a new Claude Code
session from this repo and paste everything under the line:

```bash
cd "/Users/scottbiggsbkn301aact/Desktop/BKN Code 2/bkn301-qvac-inference"
claude
```

---

You are picking up `bkn301-qvac-inference`. Read, in order: `docs/HEAD_TO_HEAD.md`,
`docs/PHASE1_RESULTS_QVAC.md`, `docs/QVAC_FORK.md`, `docs/VAST_AI_OPERATIONS.md`. Then execute the
plan below. You may run the GPU work on vast.ai (the user has pre-authorized a small spend;
see Ground rules). Stop and ask the user if something here fails against reality.

# Plan — QVAC Fabric: re-check design, fix JSON/thinking, align TLS with the sister repo

## Context
The A10 head-to-head (`docs/HEAD_TO_HEAD.md`) is done: QVAC (llama-server, Q4_K_M) is far below
vLLM (8 users warm 20.0 vs 43.2 tok/s/user; cold at 5 users 15.9 vs 27.8, **fails R1**). Three
questions remain: (1) did we use QVAC's parallelism correctly or miss something in its docs; (2)
`response_format: json_schema` returns 400 on QVAC (0/5) and Qwen3 thinking needs a clean story;
(3) TLS/mTLS should match the vLLM sister repo (`../bkn301-inference`). Direction from the user:
expect QVAC to lose; the deliverable is an honest, correct measurement and a deployable config,
not a better number. TLS scope chosen: **match the sister only**. A short `qvac serve` side test
was approved.

**Bar correction:** the "48 tok/s/user @ 8" bar came from the old ~5.3k-token loadgen shape. At a
true 7000+512 prompt the vLLM bar is **~43.2** (`HEAD_TO_HEAD.md` §13 #1). Use 43.2.

## Findings so far (verified — don't re-derive)

### 1. Parallelism: which "QVAC parallelism" did we use?
- We ran the fork's **`llama-server`** with llama.cpp's own slots: `-np 8 -c 65536` (`-c` is the
  TOTAL, 8192/slot), continuous batching on by default, `-fa on`, `--reasoning-format none`.
  Source: `scripts/h2h_serve.sh:86-98`. Confirmed.
- That is **not** QVAC's documented `config.parallel`. That belongs to a different server,
  `qvac serve --openai` (npm `@qvac/cli`, Node, wraps `@qvac/sdk`; docs
  https://docs.qvac.tether.io/cli/http-server/). Its docs say: `config.parallel` defaults to 1;
  "the underlying addon runs one decode per native model context"; same-alias requests wait in an
  **FCFS queue**; no health/metrics/TLS endpoints; structured output cannot combine with `tools`;
  `reasoning_budget:false` disables thinking; chat prefixes are cached **on disk** under
  `~/.qvac/kv-cache` (4 GiB LRU, 24 h TTL) — a prompt-retention concern for PRD §5.4.
- Verdict: llama-server slots + continuous batching is the better path and the right one to
  measure. The docs do not change that. `qvac serve` is a plausible reading of "QVAC" by
  management, so we test it briefly (Part D) and expect it to be worse.
- Tuning **never tried** (candidate quick wins, all on llama-server): `--threads/-tb` (log showed
  48 threads on a ~23-core cgroup), `-b/-ub` (2048/512 defaults), `--cache-reuse`, `-sps`,
  `--cache-ram 0` (manifest has it; the "default" runs did not), speculative decoding
  (`--spec-*`), gateway session-affinity to beat the 1/N cross-slot cache hit rate. Run a small
  sweep (Part E); do not expect it to close a 2x gap.

### 2. JSON / thinking
- H2H §10: `response_format: json_schema` → HTTP 400 `Failed to initialize samplers:
  std::exception`, 0/5 on Q4_K_M and Q8_0 (`results/head-to-head-a10/qvac-q4km/quality.jsonl`,
  test at `scripts/h2h_quality.py:198-203`, sent `strict:true` + `enable_thinking:false`). Tool
  calls 5/5.
- **Leading hypothesis (H1): self-inflicted by `--reasoning-format none`**, which every H2H run
  used (to make loadgen count think text as `content`). Upstream reports exactly this error for
  any `response_format` under that flag (Hal0ai/hal0#2545).
- H2 (if H1 fails): upstream grammar/prefill bug fixed in ggml-org/llama.cpp PR #22302 (issue
  #21571); fork pin `7bb2161c6` (2026-09-24, build 9240) may predate it.
- H3: schema-specific grammar failure (`strict`, empty-object, big `maxLength` — issues
  #25923/#25746).
- Thinking: default output puts think text in `reasoning_content` with empty `content`; even with
  `enable_thinking:false` QVAC prepends an empty `<think>\n\n</think>` (0/40 raw agreement with
  vLLM, 30/40 stripped). `--reasoning off` gave no speed change. `b3_probe.py` was never run on
  QVAC; `--chat-template-kwargs`/`--reasoning-budget` never tried.
- The sister gateway is a pure passthrough (`gateway/routes/chat.py:82,177,358`); thinking is
  handled **client-side** (`enable_thinking:false` + `strip_think` in `baas-poc-templates`,
  `bkn301-inference/docs/HANDOFF.md:112-120`). Keep that contract.

### 3. TLS/mTLS
- **The sister repo has no engine-side TLS.** Its mTLS is gateway-side wiring only
  (`UPSTREAM_CLIENT_CERT_PATH/KEY_PATH/CA_BUNDLE_PATH`, `_upstream_ssl_context()` in
  `gateway/upstream/client.py`, `tests/test_mtls.py` against an in-process ssl socket) — never run
  against a live vLLM (`HANDOFF.md:658`). The fork already has a docstring-for-docstring copy
  (`gateway/upstream/client.py:67-90`, `gateway/config.py:138-148,293-308`, `tests/test_mtls.py`,
  `.env.example`). **No code change is needed to "match".**
- QVAC facts: build has no OpenSSL (`srv init: the server is built without SSL support`,
  `results/head-to-head-a10/logs/q4km-tls.log`); `--ssl-key-file/--ssl-cert-file` exist but there
  is no CA/client-verify flag and the source only does server-side TLS
  (`httplib::SSLServer(cert,key)`, `tools/server/server-http.cpp`). Real engine mTLS therefore
  needs a sidecar (or a fork patch) — out of scope this round; document the design only.

## Workstreams

### Part A — Diagnose and fix JSON/thinking (needs a GPU; correctness, not perf)
Any 24 GiB card is fine (an RTX 3090 ~$0.20/hr is OK; label results with the card). Reuse the H2H
harness: `scripts/h2h_serve.sh`, `scripts/h2h_quality.py`, `scripts/b3_probe.py`. Build pin first:
fork `7bb2161c6`, `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=<86|89>`.
1. **Matrix** (record status + body for each): server `--reasoning-format {none, deepseek(default)}`
   x `--reasoning {auto, off}` x request `enable_thinking {absent, false, true}` x
   `response_format` {`json_object`, `json_schema` with `strict` true/false, a trivial schema, the
   H2H schema}. Also run tool calls and a plain chat for regression.
2. If H1 confirms (works without `--reasoning-format none`): production flags drop it. Then:
   - Fix `scripts/loadgen.py` so TTFT / decode tok/s count the first delta carrying either
     `content` or `reasoning_content` (so perf and prod configs are the same). Add/extend
     `tests/test_loadgen.py`.
   - Re-run a short loadgen at 1 and 5 users on the new flags; confirm throughput is unchanged
     (state that the TTFT definition changed).
   - Update `deploy/k8s/probes.yaml:61-65`, `scripts/h2h_serve.sh`, `README.md`.
3. If H2: build the fork + cherry-pick PR #22302 (or test current ggml-org/llama.cpp master as a
   control to prove it is engine-level); if still failing, bisect schema (H3); fallback is a
   documented gateway/client workaround (`json_object`, or per-request `grammar`). Record the
   pin/patch needed.
4. Thinking contract: run `b3_probe.py` against QVAC; check empty-`<think>` wrapper behaviour with
   the default reasoning-format; confirm the client-side `strip_think` (other repo) copes; decide
   if a server default `--chat-template-kwargs` is wanted (default: no, keep vLLM parity).
5. Extend `stub/server.py` + `tests/test_qvac_quirks.py` to emulate the `reasoning_content` split
   and the empty-think wrapper so the gateway's passthrough is pinned by tests.

**Acceptance:** json_schema 5/5 on Q4_K_M under production flags; tool calls still 5/5; b3 results
tabulated vs vLLM's (`bkn301-inference/docs/PHASE1_RESULTS.md:326-376`); no throughput regression.

### Part B — TLS/mTLS: match the sister (docs/comments only, no GPU)
1. Remove or correct stale "QVAC documents no TLS" claims: `gateway/upstream/client.py:35-38`,
   `gateway/config.py:129-132`, `.env.example:31-33`, `tests/test_mtls.py:3-7`,
   `README.md:190,206,248,372`, `docs/QVAC_FORK.md:37,63-67,169-182`,
   `docs/QVAC_FABRIC_LAUNCH_PROMPT.md:32-34,90`, `docs/HANDOFF.md:812-820`. Replacement truth: flags
   exist; stock build lacks OpenSSL; no client-CA option; engine-side mTLS needs a sidecar, unbuilt
   — identical posture to the sister (gateway-side only; the security review must run on the real
   deployment path, PRD §4.8).
2. Write the sidecar design in `docs/QVAC_FORK.md` item 6 (design only): TLS terminator
   (nginx/envoy) with `ssl_verify_client on` on a private port -> `127.0.0.1:8000`,
   `proxy_buffering off` for SSE, kubelet probes on loopback / a plain side port (`/health` is
   unauthenticated), cert Secret + rotation left to DevOps like the sister.
3. Fix other stale claims found: `stub/server.py:11-18` and `docs/QVAC_FORK.md:28-37` (QVAC *does*
   have `/health`, `--metrics`, `response_format`; JSON claim updated after Part A).
4. Run `pytest tests/test_mtls.py tests/test_config.py` — expect unchanged and green.
5. Optional, only if cheap: confirm `-DLLAMA_OPENSSL=ON` (+ `libssl-dev`) yields a server that
   accepts `--ssl-*-file`; record as untested-in-production.

### Part C — Doc/data corrections
`docs/QVAC_FORK.md` (struck-through original claims; point to measured results),
`HEAD_TO_HEAD.md` §11/§12 items for JSON and TLS once Parts A/B land, the 48 -> 43.2 bar note in
README.

### Part D — `qvac serve --openai` side test (time-box ~1 h, ~$0.30)
On the same box: install Node + `@qvac/cli`; `qvac.config.json` with a Qwen3 alias (find the SDK
constant; if no Qwen3-8B Q4 constant exists, use the closest and mark results non-comparable).
Measure `config.parallel` 1 vs 5: does concurrent load queue sequentially (per-user ~
aggregate/N)? VRAM with parallel=N (does it duplicate weights, as an unrelated team reported?).
Check `~/.qvac/kv-cache` for retained prompt text (§5.4) and the `/health` absence. Same loadgen
shape. Report; do not adopt unless it surprises.

### Part E — llama-server tuning sweep (optional, A10 preferred)
`--threads/-tb`, `-b/-ub`, `--cache-reuse`, `-sps`, spec decoding; 5 and 8 users warm and cold.
Report deltas vs the `-cram 0` baseline (26.5 warm @5, 15.7 cold @5, 20.0 warm @8). Only a result
that rescues cold@5 >= 20 matters for R1.

## Ground rules (carried over)
Synthetic prompts only; throwaway keys; `scripts/pack_for_box.sh` for transfers; no push/merge;
local commits OK; report failures plainly and mark corrections as corrections; destroy the
instance + SSH key afterward and verify `vastai show instances/volumes` are `[]`. Check
`vastai show user` balance first; stop and ask at ~$1.50 spend. Box traps: filter offers on
`cpu_cores_effective>=12` and read `/sys/fs/cgroup/cpu.max` (nproc lies); launch scripts live as
files on the box; zsh does not word-split `$VAR` (`set -- $s` fails); `vastai destroy instance -y`.

## Deliverables
`docs/QVAC_FIXES_RESULTS.md` (findings, matrix table, decision per hypothesis, Part D/E numbers),
raw output under `results/qvac-fixes/`, corrected manifests/docs/tests, local commits.

## Verification
- `.venv/bin/pytest` full suite green (incl. new loadgen and quirks tests).
- Live: json_schema 5/5 + tools 5/5 on Q4_K_M with production flags; b3 table; loadgen sanity at 1/5.
- `grep -rn "documents no TLS\|no /health\|no /metrics" .` returns nothing stale.
- Teardown verified, spend reported.
