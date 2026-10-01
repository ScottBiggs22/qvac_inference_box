# QVAC Fabric — Phase 1 first pass (RTX 3090 PLACEHOLDER, not an A10)

**Date:** 2026-10-01. **Raw data:** `results/qvac-rtx3090-placeholder/`.
**Spend:** ~$0.30 (two instances, ~1.2 h total). **Scope:** does QVAC Fabric
build, serve, and hold up on a 24 GiB card, and is there any hope against the
vLLM baseline (`PHASE1_RESULTS.md`). A separate, rigorous A10 head-to-head is
being run elsewhere.

> **THE CARD IS NOT AN A10.** No A10 was rentable on vast.ai on 2026-10-01
> (searched `A10`, `A10G`, all ~24 GiB single-GPU offers: none). An RTX 3090
> (936 GB/s) was used instead of the A10 (~600 GB/s). Decode is bandwidth-bound,
> so the absolute tok/s below **flatter QVAC** and must not be compared to the
> vLLM A10 numbers as-is. Treat them as function-check marks, not KPIs.

## 1. Build / install (checklist item 1's prerequisite)

- No prebuilt CUDA binary exists. Tether's releases ship CPU and Vulkan Linux
  builds only. CUDA means building from source:
  `cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86`.
- Pinned: `tetherto/qvac-fabric-llm.cpp` `7bb2161c690b…` (`llama-server`
  0.1.2-dev, build 9240). Image `nvidia/cuda:12.4.1-devel-ubuntu22.04`.
- Build time **~10 min on 15 real CPUs**. The first box reported `nproc=16` but
  its cgroup allowed ~1.9 CPUs (`/sys/fs/cgroup/cpu.max`); the build was at 37%
  after 45 min and was abandoned. **Check `cpu.max`, not `nproc`, and filter
  offers on `cpu_cores_effective>=12`.**
- Model: `Qwen/Qwen3-8B-GGUF` @ `7c41481f…`, Q4_K_M, 5.03 GiB, sha256 in
  `box_provenance.txt`.

## 2. Corrections to `docs/QVAC_FORK.md` (from the real `--help`, `server_help.txt`)

These were wrong in the fork docs. Marked as corrections, not silently fixed.

| Doc claim | Reality |
|---|---|
| `--openai` flag | **Does not exist.** Stock `llama-server`; `/v1/*` is always on. `deploy/k8s/probes.yaml` args need fixing. |
| `--model=`, `--ctx-size=`, `--parallel=` | Real flags are `-m`, `-c/--ctx-size`, `-np/--parallel`. |
| Default context 1024 | Default `-c 0` = model's own context. |
| `-c` is per-request | `-c` is the **total across slots** (`n_ctx_slot = c / np`, `kv_unified=false`). 5×8k needs `-c 40960`. |
| FCFS fixed slots, no continuous batching | `-cb` is on by default; slots are decoded together in one batch. Scaling is still far worse than vLLM (§4). |
| No `/health`, no `/metrics` | `GET /health` exists (**no auth**, `{"status":"ok"}`). `--metrics` enables Prometheus `/metrics`; `/slots` is on by default. |
| No TLS | `--ssl-key-file` / `--ssl-cert-file` exist. Item 6 is reopened: native TLS may be possible (mTLS client-cert verification not checked). |
| `/v1/models` is the probe | It requires the API key; `/health` does not. This largely resolves item 7. |

## 3. Item 5b — slot memory (RESOLVED, measured)

Weights are shared; each extra slot costs one KV cache. `nvidia-smi`, 8k per
slot, F16 KV, Q4_K_M, FA auto:

| `-np` | `-c` | VRAM used |
|---|---|---|
| 1 | 8192 | 6018 MiB |
| 2 | 16384 | 7172 MiB |
| 5 | 40960 | 10628 MiB |
| 8 | 65536 | 14084 MiB |

Exactly **1152 MiB per 8k slot** (= 8192 × 144 KiB, matching 5a's per-token
figure). Linear, no duplication. Item 5a's capacity math holds: 8 × 8k fits in
14 GiB of 24, with room to spare.

## 4. Throughput (RTX 3090, direct to `llama-server`, 7000-token prompts, 512 out)

`scripts/loadgen.py`, `-np 8 -c 65536 --reasoning-format none`. Server counts
(not delta-frame) are not used; loadgen's delta-frame estimates apply.

| Conc | warm prefix tok/s/user | warm TTFT p50/p95 (ms) | cold prefix tok/s/user | cold TTFT p50/p95 (ms) |
|---|---|---|---|---|
| 1 | 117.2 | 76 / 77 | 116.6 | 1908 / 1942 |
| 3 | 70.3 | 367 / 1966 | 31.9 | 2009 / 4109 |
| 5 | **46.5** | 684 / 1612 | **23.0** | 2018 / 7286 |
| 8 | 32.8 | 129 / 2979 | 18.6 | 2866 / 11709 |

Aggregate tok/s, warm: 115 → 182 → 215 → 238 (2.1× for 8×). Cold: 80 → 131.

vLLM A10 baseline for context: 71.5 / 50.9 / (360 agg @ 8) warm, 28.9 cold @ 5.

**Read:** R1 floor (≥20 tok/s/user @ 5) is met on the 3090 in both cases, but
the cold-prefix margin is thin (23.0) and the 3090 has ~1.5× the A10's decode
bandwidth. Warm @ 5 is 46.5 here against vLLM's 50.9 on a slower card; the
aggregate ceiling (238 vs 360) is the clearest regression. **Unmeasured
estimate, flagged as such:** scaling decode by bandwidth (×0.64) puts the A10
near ~30 tok/s/user warm and ~15 cold at 5 concurrent. Cold would then be
**below** the 20 floor. The A10 head-to-head must confirm this; do not quote it.

## 5. Other checklist items

- **1 — start time:** warm boot to `/health` 200 in **3.2 s** (weights in page
  cache, 1/2/5/8 slots identical). **Cold first-load is not measured** (the
  download ran in parallel with the build, so the file was already cached).
  `startupProbe` timings remain a placeholder.
- **2 — freeze:** `kill -STOP` on the process with a request in flight:
  `/health`, `/v1/models`, `/slots` **all hung** (curl timeout at 5 s), unlike
  vLLM where the API stayed green. It is one process, so the HTTP threads freeze
  with the engine. After `-CONT` the in-flight request finished (1500 tokens,
  HTTP 200). Consequence: a plain probe timeout detects this freeze, so the
  vLLM-era worry ("a good probe is not sufficient") does not apply to a whole-
  process stop. A stuck decode loop with live HTTP threads was **not** tested.
  The gateway's `wedge.py` was **not** exercised (no gateway run this session).
- **4 — SSE shape:** first frame is `delta:{role:"assistant",content:null}`,
  then content deltas, a final `choices:[]` frame with `usage`, then
  `data: [DONE]`. `content:null` (not `""`) in the opening frame: check
  `gateway/routes/chat.py` and `stub/server.py` against this.
- **8 — logging:** a marker prompt did not appear in the server log at default
  verbosity (0 hits), and a filesystem grep found no other copy. No `--no-log-
  requests` analogue is needed at default settings; `-v` was not tested.
- **Qwen3 thinking:** by default output goes to `reasoning_content` with empty
  `content`. loadgen counts only `content`, so the runs used
  `--reasoning-format none` (think text inline), the closest match to vLLM with
  no reasoning parser. The gateway/clients must decide how to treat this.

## 6. Not done

Gateway round-trip, `wedge.py` against the real server, Vulkan backend, Q8_0,
quantized KV cache, cold-load timing, mTLS. Real-A10 numbers.
