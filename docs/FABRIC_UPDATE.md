# Current QVAC Fabric vs our benchmarked build: A10 and T4 (2026-11-18)

**Question.** Every QVAC number in this repo comes from `qvac-fabric-llm.cpp` @ `7bb2161`
(build 9240, 2026-09-24). QVAC now ships a CUDA module in npm (`@qvac/fabric-linux-x64` 0.31.0,
fabric tag `v11739.4.0`). Does the current engine change the A10 picture, and the R1 floor
(≥20 tok/s/user at 5 users) in particular?

**Answer.** Yes, for concurrent decode. **Per-user decode at 8 users warm goes from 20.1 to
30.8 tok/s (+53%), and aggregate from 159 to 242 tok/s.** Single-user speed and prefill are
unchanged. With a **cold prefix at 5 users it still fails R1** (about 18 tok/s, no change). The gain is
in the engine source, not in npm's NVRTC fusion option, which is worth 0%. The default
host-RAM prompt cache still adds 2.5–4.3 s of TTFT, so `-cram 0` is still required. Quality is
unchanged.

## Setup

| Item | Value |
|---|---|
| Box | vast.ai machine **123534** (Illinois), the same A10 as the HEAD_TO_HEAD main run. A10 23,028 MiB, driver 595.91.07, CUDA 13.2. Image `vllm/vllm-openai:v0.28.0` (nvcc 13.0.88). Contract 61277463, ~2.4 h, ≈ $0.60. |
| Old | `7bb2161` → `version 0.1.2-dev (build 9240)`. `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86` |
| New | `v11739.4.0` @ `2aa8dbec` → `version 0.7.0-dev (build 12847)`. The same flags **+ `-DGGML_CUDA_GRAPHS=ON -DGGML_CUDA_FA=ON -DGGML_CUDA_NVRTC_FUSION=ON`**, which are the npm module's CUDA options (`tetherto/qvac-registry-vcpkg` `ports/qvac-fabric/portfile.cmake`) |
| Both | `-DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF`. The prebuilt web-UI download now fails on both tags, and a partial download aborts the build. The UI has no effect on inference. |
| Model / flags | Qwen3-8B Q4_K_M (sha256 `d98cdcbd…5785`), `-np 8 -c 65536 -ngl 99 -fa on --reasoning-format auto`, `-cram 0` unless noted |
| Harness | `scripts/h2h_fabric_update.sh` → `h2h_bench.sh` suites `A_warm_short` / `A_cold_short` (7000+512, levels 1/5/8, 6 req/worker), the same as HEAD_TO_HEAD §3 |
| Order | ABBA: old → new → new → old, so drift or thermal effects would show up as an old-vs-old gap. There was none. |

## Results (per-user decode tok/s p50; each cell lists the two repeats)

| | Old (9240) | New (12847) | Change |
|---|---|---|---|
| Warm, 1 user | 70.0 / 69.6 | 71.1 / 71.1 | +2% |
| **Warm, 5 users** | 29.7 / 29.9 | **39.2 / 38.9** | **+31%** |
| **Warm, 8 users** | 20.1 / 20.1 | **30.9 / 30.7** | **+53%** |
| Warm aggregate at 8 (tok/s) | 159 / 159 | 242 / 241 | +52% |
| Warm TTFT p50 at 1 / 8 users | 140 / 354–361 ms | 88–91 / 310–313 ms | faster |
| Cold, 1 user | 69.8 / 69.7 | 71.1 / 71.0 | +2% |
| **Cold, 5 users** | 18.1 / 18.2 | **19.9 / 16.7** | none (noisy) |
| Cold, 8 users | 12.7 / 12.7 | 16.8 / 16.6 | +31% |
| Cold prefill of 7000 tokens, 1 user | 2,531 ms | 2,517 ms | none |

Variants of the new build, warm, one run each:

| Variant | 1 / 5 / 8 users | TTFT p50 at 8 | Reading |
|---|---|---|---|
| `GGML_CUDA_NVRTC_FUSION=0` (fusion off at run time) | 71.2 / 39.1 / 30.8 | 286 ms | **fusion contributes nothing** here |
| Default flags (host-RAM cache on, no `-cram 0`) | 71.1 / 39.2 / 30.8 | **4,334 ms** (2,524 at 5) | decode unchanged, TTFT penalty **still present** |

**Where the gain comes from.** At 8 users the inter-token gap drops from about 50 ms to 32.5 ms.
At 1 user it is unchanged (14.1–14.4 ms), and so is prefill. So the change is in batched,
multi-sequence decode. That fits with cold at 5 users not moving: there, slots are repeatedly
stalled by other requests' 3-second prefills, and prefill did not get faster.

**Quality** (`h2h_quality.py compare` against `head-to-head-a10/qvac-q4km/quality.jsonl`): answers
32/40 → **32/40**, tools 5/5 → 5/5. json_schema 0/5 → 5/5, but the old 0/5 came from the
October `--reasoning-format none` (see `QVAC_FIXES_RESULTS.md`), not the engine. Raw exact-text
agreement is 0/40 for the same reason: the old outputs carry the empty `<think>` wrapper.

## What it changes in HEAD_TO_HEAD §1

| | vLLM (unchanged) | QVAC old `-cram 0` | **QVAC new `-cram 0`** |
|---|---|---|---|
| R1, 5 users warm | 45.9 | 26.5 ±4.8 † | **≈39** |
| R1, 5 users cold | 27.8 | 15.7 | **≈18, still NOT met** |
| 8 users warm, per user / aggregate | 43.2 / 340 | 20.0 / 159 | **30.8 / 242** |
| 8 users cold, per user | 19.6 | 12.3 | **16.7** |
| Cost per 1M output tokens, 8 users warm ($0.28/hr) | $0.23 | $0.49 | **$0.32** |

† The October figure; today's old-build runs on the same box gave 29.7–29.9 (the October repeats ranged widely at 5 users).
vLLM was not re-run, so its column is the October measurement on the same box and image.

**Bottom line.** The vLLM lead at 8 users warm shrinks from 2.2× to 1.4×. Cold-prefix traffic
at 5 users remains the blocker for QVAC on an A10, because prefill speed did not change. If QVAC is
adopted, pin the new tag. Building from source needs `cuda-nvrtc-dev-13-0` when NVRTC fusion is
enabled, but fusion can be left off with no loss. Keep `-cram 0`. The Dockerfile pin
(`SECURITY.md`, `REPLACE_ME/qvac-fabric:UNPINNED`) should move to `2aa8dbec`.

**Caveats.** Single A10 and single runs per variant. The 5-user cold cell is noisy (16.7–19.9).
The npm `qvac serve` server itself was not tested: these are `llama-server` numbers built from the
same source and flags as the npm CUDA module.

Raw data: `results/fabric-update-a10/` (tables regenerated by `scripts/h2h_report.py` into
`REPORT.md`; build versions in `build_provenance.txt`).

## T4 follow-up (2026-11-18, same day): the gain mostly does not carry over

Same script, `ARCH=75 TRIM=1`: a native sm_75 build of both trees, the same flags, `-cram 0`. vast.ai
machine **155649** (Texas), the Tesla T4 that worked for the T4 sessions. Driver 580.178.04,
**7.7 effective cores**. **Stopped after round 2 for budget**, so there is one old round and one new
round (no ABBA repeat). T4 cost ≈ $0.73: GPU $0.16/hr plus disk $0.08/hr, plus $0.24 of download
charges. This host bills bandwidth.

| T4, per-user decode tok/s p50 | Old (9240) | New (12847) | Change |
|---|---|---|---|
| Warm, 1 / 5 / 8 users | 22.6 / 10.6 / 7.5 | 25.9 / 11.7 / 8.1 | +14% / +10% / +8% |
| Warm aggregate at 8 (tok/s) | 59.4 | 63.9 | +8% |
| Cold, 1 / 5 / 8 users | 24.9 / 5.5 / 4.4 | 25.2 / 6.1 / 4.7 | +1% / +11% / +7% |
| Cold prefill of 7000 tokens, 1 user | 7,487 ms | 7,622 ms | none |
| Inter-token gap at 8 users, warm | 133 ms | 124 ms | −7% (A10: −35%) |

- **The gain on the T4 is +7–14%, against +31–53% on the A10.** That is consistent with the
  newer batched-decode work mostly targeting Ampere and newer kernel paths. This is not verified.
- **It is within box-to-box noise.** This box's old build is 5–9% below the earlier T4 session's
  old-build figures at 7000 tokens (7.5 vs 7.9 at 8 users, 10.6 vs 11.6 at 5). That box had about 34 cores and
  driver 595. Only this same-box pair is a fair comparison, and it is one run each.
- **Nothing changes in `T4_RESULTS.md`'s decision guide.** QVAC at 7000 tokens and 5 users is
  ~12 tok/s with either build, far from 20. On a T4 the lever is still prompt length, not the engine version.

Raw data: `results/fabric-update-t4/` (`REPORT.md` from `h2h_report.py`). Round 3 was killed
9 s into its warm-up, and that empty fragment was discarded.
