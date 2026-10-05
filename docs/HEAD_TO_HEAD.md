# Head-to-head: vLLM vs QVAC Fabric on one A10

> **Status (2026-10-03): complete.** vLLM (AWQ-INT4) and QVAC Fabric (Q4_K_M
> plus six configuration variants) on one A10. QVAC Q8_0 was measured on a second
> A10, anchored by a Q4_K_M re-run on that same box (§6b). The first Q8_0 pass
> actually served Q4_K_M because of a harness bug (§13). Its data are quarantined in
> `results/head-to-head-a10/qvac-q8-INVALID-served-q4km/` and must not be quoted.
>
> Raw data: `results/head-to-head-a10/` (main box) and
> `results/head-to-head-a10-rerun/` (Q8_0 box). Regenerate tables with
> `.venv/bin/python scripts/h2h_report.py <dir>`.
> These are rented-box figures, so treat them as a provisional baseline.
> Re-confirm on the OCI `VM.GPU.A10.1` before anyone uses them for production sizing.

## 1. Verdict

Same A10, same session, same harness, byte-identical loadgen invocations.
Workload: Qwen3-8B, **exact 7000-token prompt + 512 output tokens**, 8192-token
context per sequence. Quantization differs: vLLM runs **AWQ-INT4**, QVAC runs
**Q4_K_M**. Per-user figures are per-user decode tok/s p50, mean across repeats.

| Question | vLLM (AWQ-INT4) | QVAC best config (Q4_K_M, `-cram 0`) | QVAC default (Q4_K_M) | QVAC Q8_0 † |
|---|---|---|---|---|
| **R1 floor: ≥20 tok/s/user at 5 users, warm prefix** | **met, 45.9** | met, 26.5 ±4.8 | met, 25.9 ±3.5 | met, 28.9 (`-cram 0`) |
| **R1 floor at 5 users, cold prefix** (every request a full prefill) | **met, 27.8** | **NOT met, 15.7** | **NOT met, 15.9** | **NOT met, 17.4** (default) |
| 8 users, warm (the bar, restated: see §13) | **43.2** | 20.0 | 20.1 | 22.9 (`-cram 0`) |
| 8 users, cold | 19.6 | 12.3 | 11.3 | 12.0 (default) |
| Aggregate tok/s at 8 users, warm / cold | **340 / 133** | 159 / 88 | 137 / 80 | 182 / 84 |
| TTFT p50 at 8 users, warm | 194 ms | 225 ms | **4,404 ms** | 291 ms (`-cram 0`) |
| Cost per 1M output tokens at 8 users, warm ($0.28/hr box) | **$0.23** | $0.49 | $0.57 | $0.43 |

† Q8_0 was measured on a second A10 (vast.ai machine 152062) in single runs. The same box's Q4_K_M anchor
reproduced the main box within 2% (68.7 / 30.0 / 20.0 tok/s/user at 1/5/8 users, against 69.4 / 29.4 / 20.05), so
the comparison holds (§6b).

**Bottom line:**
- At 1 user QVAC matches vLLM: 69.5 vs 68.7 tok/s, essentially identical.
- From 2 users up QVAC scales far worse. At 8 users it delivers **46% of vLLM's per-user decode and 40–47% of its aggregate throughput**. Warm, it lands at almost exactly 20 tok/s/user at 8.
- With a cold prefix **QVAC fails the R1 floor at 5 users** (about 16 tok/s/user) in every configuration tried. vLLM passes it (27.8).
- With default flags, QVAC also adds **2.5–4.4 s of warm TTFT** at 5–8 users, caused by its host-RAM prompt cache (§7). Turning that cache off (`-cram 0`) fixes TTFT but not throughput.
- **Quantization is not the cause.** Q8_0, which avoids the 4-bit kernels, is slower than Q4_K_M at 1 user (48.6 tok/s) and only slightly faster at 8 users (22.9, aggregate 182). That is still 53% of vLLM per user, and it also fails R1 cold (17.4).

**What QVAC does better:**
- **Start-up in about 1.5–3 s**, against vLLM's 41 s warm and 90 s cold.
- **SIGTERM exit in 0.6 s**, against vLLM's 117 s with a request in flight.
- **8-slot VRAM footprint of 14 GB**, against vLLM's 20.5–21.6 GB at 0.90 utilisation.
- **More 8k slots on one card with q8_0 KV**: 24 slots loaded in 20.2 GB.
- **Quality:** Q4_K_M answers as well as AWQ (32/40 vs 31/40), and Q8_0 scores 30/40. Tool calls work on both.

**What QVAC can't do (on this build)** is listed in §12. The headline items: no structured
output (`response_format: json_schema` returns 400), no cross-slot prefix
sharing, no native TLS in the documented build, and no mTLS at all.

## 2. Setup and provenance

| Item | Value |
|---|---|
| Host | vast.ai machine 123534 (Illinois), single NVIDIA A10 23,028 MiB, driver 595.84, CUDA 13.2, 23 effective CPU cores (cgroup `cpu.max`) |
| Box check | `scripts/verify_box.sh` passed clean (`results/head-to-head-a10/verify_box.out`); full provenance in `box_provenance.txt` |
| Scheduling | Both engines on the same box, **sequentially, never concurrently**. GPU confirmed idle (<500 MiB) before every launch. |
| Session | 2026-10-02 17:18 → 2026-10-03 02:25 UTC, $2.53 |
| Q8_0 box | vast.ai machine 152062 (California), NVIDIA A10 23,028 MiB, driver 580.178.04, CUDA 13.0, 15.4 effective cores; same image, same QVAC commit and build flags (12:28 build on 15 cores); 2026-10-03 05:26 → 06:45 UTC, $0.48. `results/head-to-head-a10-rerun/` |

| | vLLM | QVAC Fabric |
|---|---|---|
| Build | `vllm/vllm-openai:v0.28.0` image | `tetherto/qvac-fabric-llm.cpp` @ `7bb2161c690befa1bb44ff9b20e4eea2da6c22b8` (build 9240), built on the box: CUDA 13.0 nvcc, sm_86, `-DGGML_CUDA=ON`, 3:00 wall on 23 cores (clone 148 s, configure 4 s) |
| Hermetic build? | pinned image | **No.** The build downloads a web-UI bundle; that download failed and the build continued. Built without OpenSSL (§11). |
| Model | `Qwen/Qwen3-8B-AWQ` @ `4da05a8edb55c6046cce958586c33b61da07bb79` | `Qwen/Qwen3-8B-GGUF` @ `7c41481f57cb95916b40956ab2f0b139b296d974`; Q4_K_M sha256 `d98cdcbd…5785` (matches pin), Q8_0 `408b9555…83d6` |
| **Quantization** | **AWQ INT4** | **Q4_K_M** (§6); **Q8_0** (§6b, second box) |
| **Context window** | 8192 per sequence (`--max-model-len 8192`), KV shared dynamically across sequences | 8192 **per slot** (`-c 65536 -np 8`; `-c` is the total). Variants: np12 (`-c 98304`), np16/20/24 with q8_0 KV |
| KV capacity | 92,544 tokens (fresh compile) / 99,856 (warm boot) | 8 × 8192 = 65,536 tokens (fixed slots) |
| Flags | `--gpu-memory-utilization 0.90 --no-enable-log-requests --no-enable-log-outputs --served-model-name qwen3-8b --api-key … --enable-prompt-tokens-details` (+ `--enable-auto-tool-choice --tool-call-parser hermes` for the quality run only) | `-ngl 99 -fa on --metrics --reasoning-format none --alias qwen3-8b --api-key …` |

**On quantization:** AWQ-INT4 and Q4_K_M are both ~4-bit, but they use different schemes and different kernels. Q8_0 is the control. It closes very little of the gap (22.9 vs 20.0 tok/s/user at 8 users; vLLM 43.2), so the gap is the engine, not the quantization (§6b).

## 3. Methodology

The harness is `scripts/loadgen.py` (definitions in its module docstring), run **on the box**.
Suites are in `scripts/h2h_bench.sh`, engine launch is in `scripts/h2h_serve.sh`, and the
QVAC sequence is in `scripts/h2h_qvac_all.sh`.

| Term | Definition |
|---|---|
| TTFT | request sent → first content delta (queueing + prefill) |
| decode tok/s | `(completion_tokens − 1) / (last delta − first delta)` |
| per-user | **median** of per-request decode rates at a level (what R1 measures) |
| aggr wall / steady | total completion tokens / level wall time; steady = only the window when every worker is decoding |
| ITL | gap between content deltas |
| prefill / cache hit | vLLM: `/metrics` counter diffs per level (`request_prefill_time`, `prefix_cache_hits/queries`). QVAC: per-request `timings` (`prompt_ms` = server prefill of uncached tokens, `cache_n`). |

| Parameter | Value |
|---|---|
| Main shape | **exact 7000 prompt tokens**, calibrated per run against the server's own `usage.prompt_tokens`, + 512 output with `ignore_eos` (every request decodes exactly 512) |
| Sampling | temperature 0.7, top_p 0.8 |
| Warm | shared system prefix + per-request nonce; **each level warmed at its own concurrency** (primes every QVAC slot) |
| Cold | unique 8-digit nonce at the very start of the prompt: a full prefix miss on both engines, with constant token count |
| Repeats | A suites: 3 repeats (QVAC `-cram 0`: 2); F_cross 3; everything else single runs with no variance estimate |

Suites: A_warm / A_cold (1,2,3,4,5,8 users, 6 req/worker) · B_legacy (the historical
~5.3k-token shape) · C_short (500+256) · D_long (7600+512) · E_sat (12/16 users, not
in the verdict) · F_cross (fresh prefix primed by ONE request, then N concurrent) ·
F_partial (shared system + ~1000-token unique turn) · G_gateway (through the real gateway).

## 4. Side by side (main shape, 7000+512)

**Warm prefix**, per-user tok/s p50 (± sd across repeats) and TTFT p50:

| Users | vLLM | QVAC `-cram 0` | QVAC default | vLLM TTFT | QVAC `-cram 0` TTFT | QVAC default TTFT |
|---|---|---|---|---|---|---|
| 1 | 68.7 ±0.1 | 69.6 ±0.0 | 69.5 ±0.2 | 117 ms | 88 ms | 1,185 ms |
| 2 | 61.4 ±0.1 | 49.2 ±7.9 | 39.7 ±12.8 | 127 ms | 114 ms | 1,795 ms |
| 3 | 56.7 ±0.1 | 39.5 ±5.3 | 33.3 ±8.1 | 133 ms | 150 ms | 2,520 ms |
| 4 | 54.8 ±0.1 | 28.8 ±8.6 | 27.5 ±6.0 | 145 ms | 148 ms | 2,749 ms |
| 5 | **45.9 ±0.1** | **26.5 ±4.8** | **25.9 ±3.5** | 163 ms | 182 ms | 3,473 ms |
| 8 | **43.2 ±0.2** | **20.0 ±0.0** | **20.1 ±0.0** | 194 ms | 225 ms | 4,404 ms |

**Cold prefix** (full 7000-token prefill on every request):

| Users | vLLM | QVAC `-cram 0` | QVAC default | vLLM TTFT p50 / p95 | QVAC default TTFT p50 / p95 |
|---|---|---|---|---|---|
| 1 | 68.5 ±0.1 | 69.7 ±0.1 | 69.5 ±0.0 | 1.96 s / 1.97 s | 3.26 s / 5.47 s |
| 2 | 54.9 ±0.0 | 41.1 ±12.3 | 47.9 ±3.0 | 3.03 / 3.86 | 5.10 / 6.48 |
| 3 | 43.4 ±0.0 | 29.2 ±6.4 | 32.0 ±5.9 | 3.91 / 5.45 | 6.60 / 9.61 |
| 4 | 36.1 ±0.1 | 20.3 ±0.3 | 18.8 ±1.3 | 4.23 / 6.14 | 6.53 / 11.80 |
| 5 | **27.8 ±0.2** | **15.7 ±1.1** | **15.9 ±0.2** | 4.61 / 7.20 | 5.31 / 12.76 |
| 8 | 19.6 ±0.2 | 12.3 ±0.0 | 11.3 ±0.1 | 4.70 / 11.50 | 6.09 / **18.69** |

Server-measured prefill of 7000 uncached tokens at 1 user: **vLLM 1.89 s, QVAC 2.53 s**.

**Variance:** vLLM's per-user decode varies by ±0.1–0.2 tok/s between repeats. QVAC varies by
±3–13 tok/s at 2–5 users, in every configuration. Under concurrency, QVAC's
throughput is not just lower but less predictable.

## 5. Results — vLLM (AWQ-INT4, 8192 ctx)

Column notes: tok/s/user is per-user decode p50 (±sd across repeats); TTFT, ITL, prefill, queue in ms.

#### A_warm — 7000+512, warm prefix

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 68.7 ±0.1 | 68.7 | 67.8 | 67.8 | 117 | 122 | 123 | 14.6 | 14.9 | 36 | 99.7% | 0 | 0 |
| 2 | 3 | 61.4 ±0.1 | 61.4 | 121.1 | 121.3 | 127 | 150 | 152 | 16.3 | 16.7 | 40 | 99.7% | 0 | 0 |
| 3 | 3 | 56.7 ±0.1 | 56.6 | 167.8 | 168.0 | 133 | 173 | 179 | 17.7 | 18.1 | 48 | 99.7% | 0 | 0 |
| 4 | 3 | 54.8 ±0.1 | 54.7 | 216.0 | 216.5 | 145 | 197 | 203 | 18.3 | 18.8 | 56 | 99.7% | 0 | 0 |
| 5 | 3 | 45.9 ±0.1 | 45.8 | 226.1 | 226.6 | 163 | 245 | 246 | 21.8 | 22.3 | 61 | 99.7% | 0 | 0 |
| 8 | 3 | 43.2 ±0.2 | 42.9 | 339.5 | 340.6 | 194 | 314 | 343 | 23.0 | 23.7 | 76 | 99.7% | 0 | 0 |

Takeaway: R1 is met with more than 2x headroom at 5 users. There is a step down between 4 and 5 users (54.8 -> 45.9 per user, aggregate flat at 216 -> 226).

#### A_cold — 7000+512, every request a prefix miss

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 68.5 ±0.1 | 68.5 | 54.4 | 56.2 | 1963 | 1966 | 1966 | 14.6 | 15.0 | 1887 | 0.0% | 0 | 0 |
| 2 | 3 | 54.9 ±0.0 | 50.2 | 82.4 | 85.6 | 3029 | 3860 | 3866 | 16.9 | 17.2 | 2460 | 0.0% | 486 | 0 |
| 3 | 3 | 43.4 ±0.0 | 38.0 | 97.8 | 101.6 | 3909 | 5446 | 5770 | 19.6 | 20.0 | 2683 | 0.0% | 895 | 0 |
| 4 | 3 | 36.1 ±0.1 | 31.8 | 111.4 | 116.1 | 4232 | 6138 | 7466 | 21.0 | 21.5 | 2806 | 0.0% | 1162 | 0 |
| 5 | 3 | 27.8 ±0.2 | 24.8 | 111.3 | 115.8 | 4607 | 7195 | 9323 | 26.2 | 26.8 | 2912 | 0.0% | 1237 | 0 |
| 8 | 3 | 19.6 ±0.2 | 18.0 | 132.6 | 138.8 | 4698 | 11500 | 15089 | 29.4 | 30.6 | 3109 | 0.0% | 1533 | 0 |

Takeaway: a 7000-token prefill costs about 1.9 s alone. Prefill interleaving with decode drags per-user decode to 27.8 @5, which still meets R1, and to **19.6 @8, which misses 20**. TTFT p99 reaches 15 s @8.

#### B_legacy — historical shape (~5.3k actual prompt tokens), warm

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.0 | 71.0 | 70.2 | 70.2 | 101 | 110 | 111 | 14.1 | 14.5 | 35 | 99.0% | 0 | 0 |
| 3 | 1 | 60.8 | 60.5 | 177.4 | 179.8 | 132 | 179 | 190 | 16.4 | 17.0 | 46 | 99.1% | 0 | 0 |
| 5 | 1 | 50.8 | 50.5 | 246.6 | 252.1 | 134 | 229 | 257 | 19.6 | 20.2 | 57 | 99.2% | 0 | 0 |
| 8 | 1 | 48.0 | 47.8 | 371.6 | 380.6 | 154 | 320 | 353 | 20.6 | 21.2 | 71 | 99.2% | 0 | 0 |

Prompt tokens actually sent: 5296-5303. Takeaway: this reproduces the historical 48.29 @8 (PHASE1_RESULTS.md) at 48.0, which confirms that the old "7000-token" bar was a ~5.3k-token bar.

#### C_short — 500+256, warm

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 80.7 | 80.7 | 79.7 | 79.6 | 50 | 51 | 52 | 12.4 | 12.7 | 33 | 96.0% | 0 | 0 |
| 5 | 1 | 75.0 | 74.4 | 366.6 | 367.3 | 81 | 92 | 96 | 13.3 | 13.7 | 38 | 96.0% | 0 | 0 |
| 8 | 1 | 73.4 | 72.8 | 570.5 | 572.4 | 110 | 124 | 125 | 13.6 | 14.0 | 42 | 96.0% | 0 | 0 |

Takeaway: with short context, per-user decode barely degrades with concurrency (80.7 -> 73.4). Attention over long KV is what costs at 7000.

#### D_long — 7600+512 (8112 of 8192), warm

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 67.8 | 67.8 | 66.8 | 67.0 | 127 | 131 | 132 | 14.8 | 15.1 | 38 | 99.6% | 0 | 0 |
| 5 | 1 | 44.4 | 44.2 | 218.5 | 219.6 | 185 | 229 | 239 | 22.6 | 23.1 | 66 | 99.8% | 0 | 0 |

Takeaway: at the context ceiling, @5 is about 3% below the 7000 shape (44.4 vs 45.9).

#### E_sat — 7000+512 at 12/16, warm (not part of verdict)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 12 | 1 | 32.5 | 32.4 | 383.2 | 387.5 | 320 | 430 | 445 | 30.7 | 31.6 | 102 | 99.7% | 0 | 0 |
| 16 | 1 | 30.9 | 30.7 | 483.1 | 489.0 | 352 | 490 | 507 | 32.3 | 33.5 | 127 | 99.7% | 0 | 0 |

Takeaway: with a warm prefix, 16 concurrent users still get 30.9 tok/s/user and nothing queues (queue 0). The shared prefix blocks fit easily in the 99,856-token KV.

#### F_cross — fresh prefix per level, primed by one request, then N concurrent

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 3 | 61.5 ±0.0 | 61.5 | 120.9 | 123.1 | 156 | 160 | 160 | 16.3 | 16.7 | 52 | 99.7% | 0 | 0 |
| 4 | 3 | 54.9 ±0.1 | 54.8 | 214.6 | 219.5 | 216 | 229 | 230 | 18.3 | 18.9 | 62 | 99.7% | 0 | 0 |
| 8 | 3 | 43.4 ±0.0 | 43.2 | 338.1 | 347.0 | 300 | 351 | 352 | 23.1 | 23.9 | 89 | 99.7% | 0 | 0 |

Takeaway: one priming request is enough for every concurrent request to hit (99.7%), because vLLM's prefix cache is global, not per-slot. Decode matches A_warm.

#### F_partial — shared system block + ~1000-token unique user turn

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 68.6 | 68.6 | 65.9 | 66.4 | 322 | 326 | 327 | 14.6 | 14.9 | 240 | 88.7% | 0 | 0 |
| 5 | 1 | 43.9 | 41.7 | 203.7 | 207.3 | 935 | 1308 | 1310 | 22.1 | 22.7 | 720 | 88.7% | 8 | 0 |
| 8 | 1 | 41.1 | 37.5 | 293.0 | 300.0 | 1548 | 2042 | 2076 | 23.4 | 24.1 | 1041 | 88.7% | 91 | 0 |

Prompt tokens actually sent: 6998, 2 short of the 7000 target. Takeaway: this is probably the most realistic shape. @5 = 43.9, and TTFT p50 rises to about 0.9-1.5 s.

#### G_gateway — main shape through the gateway

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 68.7 | 68.6 | 67.0 | 67.1 | 206 | 209 | 210 | 14.6 | 14.9 | 40 | 99.7% | 0 | 0 |
| 5 | 1 | 45.7 | 45.5 | 224.2 | 224.8 | 209 | 297 | 312 | 21.9 | 22.5 | 54 | 99.7% | 0 | 0 |
| 8 | 1 | 43.3 | 43.2 | 339.6 | 340.7 | 236 | 354 | 373 | 23.0 | 23.6 | 68 | 99.7% | 0 | 0 |

Takeaway: decode is identical to direct. TTFT p50 is +89 / +46 / +42 ms vs A_warm at 1/5/8.

## 6. Results — QVAC Fabric (Q4_K_M, 8192 per slot)

### A_warm — default config (`-cram 8192`, the fork's default)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 69.5 ±0.2 | 69.4 | 60.7 | 61.9 | 1185 | 2228 | 2427 | 14.3 | 14.5 | 23 | 99.8% | - | 0 |
| 2 | 3 | 39.7 ±12.8 | 39.7 | 76.6 | 77.2 | 1795 | 2900 | 2902 | 26.6 | 27.0 | 43 | 99.8% | - | 0 |
| 3 | 3 | 33.3 ±8.1 | 30.9 | 91.5 | 93.6 | 2520 | 3331 | 3336 | 31.1 | 35.2 | 52 | 99.8% | - | 0 |
| 4 | 3 | 27.5 ±6.0 | 26.5 | 100.3 | 102.6 | 2749 | 3576 | 3583 | 39.3 | 40.2 | 70 | 99.8% | - | 0 |
| 5 | 3 | 25.9 ±3.5 | 21.0 | 107.6 | 110.0 | 3473 | 3804 | 3812 | 40.8 | 51.8 | 84 | 99.8% | - | 0 |
| 8 | 3 | 20.1 ±0.0 | 20.1 | 137.2 | 140.4 | 4404 | 4465 | 4476 | 49.7 | 50.9 | 118 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Takeaway: the three repeats differ materially. For example, 5 users gave 29.4 / 22.4 / 26.0, and
TTFT rose by repeat because the 8 GiB host-RAM prompt cache fills and starts evicting (§7).
A long-running server sits at the degraded end.

### A_cold — default config

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 69.5 ±0.0 | 69.5 | 46.2 | 50.8 | 3257 | 5468 | 6054 | 14.4 | 14.5 | 2537 | 0.1% | - | 0 |
| 2 | 3 | 47.9 ±3.0 | 39.8 | 63.8 | 66.8 | 5102 | 6481 | 6486 | 18.2 | 22.7 | 3054 | 0.1% | - | 0 |
| 3 | 3 | 32.0 ±5.9 | 24.1 | 65.4 | 68.2 | 6600 | 9612 | 9698 | 27.1 | 35.2 | 3328 | 0.1% | - | 0 |
| 4 | 3 | 18.8 ±1.3 | 17.0 | 60.7 | 63.2 | 6531 | 11795 | 12738 | 42.8 | 43.6 | 3472 | 0.1% | - | 0 |
| 5 | 3 | 15.9 ±0.2 | 13.5 | 66.2 | 69.1 | 5312 | 12760 | 15174 | 44.6 | 54.8 | 3234 | 0.1% | - | 0 |
| 8 | 3 | 11.3 ±0.1 | 10.9 | 80.1 | 83.8 | 6094 | 18688 | 23369 | 50.1 | 52.3 | 3867 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Takeaway: **R1 not met at 5 users (15.9)**. Aggregate throughput tops out at 60–80 tok/s from 3 users
up, and 8 users see TTFT p99 of 23 s.

### A_warm_r2 / A_cold_r2 — `-cram 0` (host prompt cache off): QVAC's best config

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 69.6 ±0.0 | 69.6 | 68.9 | 68.9 | 88 | 94 | 94 | 14.3 | 14.5 | 23 | 99.8% | - | 0 |
| 2 | 2 | 49.2 ±7.9 | 43.5 | 94.5 | 94.5 | 114 | 137 | 142 | 22.4 | 24.8 | 35 | 99.8% | - | 0 |
| 3 | 2 | 39.5 ±5.3 | 32.3 | 112.3 | 112.4 | 150 | 169 | 169 | 27.1 | 35.0 | 54 | 99.8% | - | 0 |
| 4 | 2 | 28.8 ±8.6 | 25.5 | 113.5 | 113.5 | 148 | 207 | 213 | 39.2 | 45.5 | 53 | 99.8% | - | 0 |
| 5 | 2 | 26.5 ±4.8 | 24.0 | 132.8 | 132.8 | 182 | 214 | 218 | 38.4 | 44.5 | 67 | 99.8% | - | 0 |
| 8 | 2 | 20.0 ±0.0 | 20.0 | 159.1 | 159.2 | 225 | 294 | 324 | 49.8 | 51.0 | 97 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2 | 69.7 ±0.1 | 69.6 | 51.4 | 53.7 | 2619 | 2621 | 2621 | 14.3 | 14.4 | 2533 | 0.1% | - | 0 |
| 2 | 2 | 41.1 ±12.3 | 36.5 | 62.5 | 64.9 | 4274 | 5214 | 5216 | 24.5 | 24.8 | 3091 | 0.1% | - | 0 |
| 3 | 2 | 29.2 ±6.4 | 21.7 | 67.0 | 69.4 | 4570 | 7577 | 7811 | 29.4 | 41.3 | 3191 | 0.1% | - | 0 |
| 4 | 2 | 20.3 ±0.3 | 18.6 | 67.1 | 69.4 | 5390 | 9383 | 10305 | 39.6 | 40.3 | 3113 | 0.1% | - | 0 |
| 5 | 2 | 15.7 ±1.1 | 14.0 | 68.9 | 71.2 | 4506 | 10756 | 12549 | 49.3 | 54.8 | 3142 | 0.1% | - | 0 |
| 8 | 2 | 12.3 ±0.0 | 11.8 | 88.3 | 92.0 | 4836 | 15341 | 19995 | 50.1 | 52.2 | 3245 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Takeaway: with `-cram 0`, warm TTFT matches vLLM (88–225 ms), but per-user decode is the same as the
default, and **cold at 5 users is still 15.7**. The scaling limit is QVAC's batched decode,
not its cache: 8 parallel sequences take about 50 ms per step, which pins per-user decode at 20.0 tok/s.

### B_legacy — historical shape (~5.3k actual tokens), warm, default config

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.8 | 71.8 | 70.8 | 71.0 | 106 | 111 | 111 | 13.8 | 14.1 | 26 | 99.3% | - | 0 |
| 3 | 1 | 29.9 | 28.2 | 82.5 | 89.1 | 152 | 3956 | 3956 | 33.5 | 33.9 | 62 | 82.8% | - | 0 |
| 5 | 1 | 23.6 | 23.4 | 113.8 | 117.9 | 178 | 1436 | 1437 | 42.3 | 43.1 | 80 | 99.3% | - | 0 |
| 8 | 1 | 21.3 | 20.0 | 154.7 | 164.8 | 679 | 2718 | 2718 | 46.2 | 48.0 | 116 | 99.3% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [5295, 5297, 5299, 5302, 5304]_

At the old ~5.3k shape: QVAC 21.3 vs vLLM 48.0 at 8 users. The first pass reported 32.8 at 8
users for QVAC, but that was on an RTX 3090, which has ~1.5× the A10's bandwidth.

### C_short — 500+256, warm, default config

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 80.5 | 80.5 | 79.4 | 79.3 | 55 | 55 | 55 | 12.3 | 12.7 | 21 | 96.7% | - | 0 |
| 5 | 1 | 41.2 | 41.1 | 195.7 | 196.5 | 362 | 376 | 377 | 24.2 | 25.2 | 51 | 96.7% | - | 0 |
| 8 | 1 | 28.5 | 28.5 | 214.7 | 216.2 | 593 | 624 | 627 | 34.9 | 36.4 | 93 | 96.7% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [500]_

Even with short context, QVAC falls from 80.5 to 28.5 tok/s/user at 8 users. vLLM holds 73.4.

### D_long — 7600+512, warm, default config

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 68.7 | 68.7 | 67.8 | 68.0 | 113 | 113 | 113 | 14.5 | 14.7 | 23 | 99.8% | - | 0 |
| 5 | 1 | 29.3 | 29.2 | 125.7 | 131.5 | 2846 | 3033 | 3037 | 34.1 | 35.1 | 85 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7600]_

### E_sat — 12 and 16 users against 8 slots, default config (not in verdict)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 12 | 1 | 20.1 | 20.1 | 141.3 | 161.0 | 4292 | 33958 | 33962 | 49.6 | 50.9 | 111 | 99.8% | - | 0 |
| 16 | 1 | 20.1 | 20.1 | 137.6 | 148.1 | 34054 | 34141 | 34144 | 49.7 | 50.9 | 116 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

When there are more users than slots, QVAC **queues and does not reject**. Admitted requests still decode at
20.1 tok/s, and waiting requests see TTFT up to 34 s. No errors at any level.

### F_cross — one priming request, then N concurrent (default config)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 3 | 37.9 ±9.7 | 36.1 | 65.4 | 75.8 | 2232 | 2633 | 2669 | 26.7 | 26.9 | 1491 | 49.9% | - | 0 |
| 4 | 3 | 28.2 ±0.1 | 23.6 | 90.5 | 114.5 | 4314 | 7666 | 7973 | 28.7 | 29.5 | 3088 | 25.0% | - | 0 |
| 8 | 3 | 15.0 ±0.0 | 12.1 | 92.7 | 123.9 | 9812 | 18197 | 18868 | 49.8 | 51.2 | 3211 | 12.5% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Takeaway: the cache hit rate is **exactly 1/N** (49.9% / 25.0% / 12.5%). Only the request that lands on the
primed slot hits; every other slot pays a full cold prefill. The same holds with `-cram 0` and with
`-kvu` (§7). Compare vLLM's 99.7% (global prefix cache).

### F_partial — shared system block + ~1000-token unique turn

Default config:

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.5 | 69.4 | 66.0 | 66.7 | 401 | 404 | 405 | 14.3 | 14.5 | 313 | 88.8% | - | 0 |
| 5 | 1 | 29.8 | 28.5 | 114.8 | 119.8 | 4841 | 6193 | 6218 | 33.4 | 34.3 | 840 | 88.8% | - | 0 |
| 8 | 1 | 19.4 | 18.7 | 125.9 | 131.9 | 6124 | 7139 | 7174 | 49.8 | 50.9 | 930 | 88.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [6998]_

`-cram 0`:

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.6 | 69.6 | 66.1 | 66.9 | 398 | 399 | 399 | 14.3 | 14.5 | 312 | 88.8% | - | 0 |
| 5 | 1 | 22.0 | 21.2 | 104.3 | 105.5 | 1254 | 1750 | 1753 | 44.7 | 45.4 | 891 | 88.8% | - | 0 |
| 8 | 1 | 19.4 | 18.4 | 145.2 | 147.5 | 1941 | 2803 | 2851 | 49.7 | 50.9 | 896 | 88.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [6998]_

### G_gateway — through the real gateway, default config

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.4 | 69.4 | 68.0 | 68.2 | 160 | 178 | 182 | 14.3 | 14.6 | 24 | 99.8% | - | 0 |
| 5 | 1 | 29.9 | 29.7 | 130.1 | 132.6 | 2556 | 2604 | 2610 | 33.5 | 34.6 | 77 | 99.8% | - | 0 |
| 8 | 1 | 20.1 | 20.1 | 137.3 | 140.4 | 4401 | 4440 | 4451 | 49.7 | 50.9 | 116 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Decode is unchanged compared with direct. TTFT through the gateway is +93 ms at 1 user. At 5–8 users
it is dominated by QVAC's own cache-copy queueing.

### Configuration variants (Q4_K_M)

`-kvu` (unified KV buffer):

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.6 | 69.5 | 68.6 | 68.7 | 110 | 110 | 110 | 14.3 | 14.5 | 23 | 99.8% | - | 0 |
| 5 | 1 | 29.3 | 29.0 | 76.2 | 82.9 | 16650 | 16765 | 16774 | 34.1 | 35.1 | 81 | 99.8% | - | 0 |
| 8 | 1 | 19.3 | 19.3 | 73.6 | 80.6 | 29173 | 29573 | 29575 | 51.6 | 53.1 | 174 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.4 | 69.4 | 48.5 | 50.9 | 3199 | 3212 | 3213 | 14.4 | 14.5 | 2523 | 0.0% | - | 0 |
| 5 | 1 | 13.9 | 12.6 | 54.3 | 55.7 | 10120 | 22180 | 23462 | 33.9 | 35.4 | 6701 | 0.0% | - | 0 |
| 8 | 1 | 6.5 | 6.1 | 45.3 | 46.6 | 15697 | 28475 | 39926 | 51.0 | 53.7 | 10192 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 3 | 48.4 ±0.1 | 42.2 | 82.0 | 96.8 | 1666 | 3083 | 3209 | 18.0 | 18.3 | 1587 | 49.9% | - | 0 |
| 4 | 3 | 27.2 ±4.1 | 22.1 | 77.7 | 110.8 | 7075 | 11215 | 11564 | 28.8 | 29.8 | 4273 | 24.9% | - | 0 |
| 8 | 3 | 11.5 ±0.8 | 8.7 | 65.5 | 100.8 | 17535 | 34687 | 36281 | 51.0 | 53.1 | 6444 | 12.5% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

**`-kvu` is a trap.** Warm TTFT reaches 16.7 s at 5 users and 29 s at 8, aggregate throughput halves, and cold at 8
users falls to 6.5 tok/s/user. It gives no cross-slot sharing either (still 1/N).

`-ctk q8_0 -ctv q8_0` (quantized KV, 8 slots):

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 63.7 | 63.7 | 63.2 | 63.2 | 72 | 74 | 74 | 15.6 | 16.0 | 28 | 99.8% | - | 0 |
| 5 | 1 | 24.0 | 23.9 | 112.6 | 113.5 | 1412 | 1451 | 1455 | 41.7 | 43.1 | 104 | 99.8% | - | 0 |
| 8 | 1 | 15.8 | 15.8 | 117.9 | 119.1 | 2456 | 2535 | 2549 | 63.1 | 65.4 | 176 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 63.8 | 63.8 | 46.6 | 48.7 | 2979 | 2991 | 2992 | 15.7 | 15.9 | 2573 | 0.1% | - | 0 |
| 5 | 1 | 16.5 | 14.0 | 66.8 | 68.9 | 5408 | 11442 | 13173 | 49.3 | 52.7 | 3406 | 0.1% | - | 0 |
| 8 | 1 | 10.1 | 9.8 | 73.2 | 76.1 | 5643 | 17365 | 22187 | 63.4 | 66.3 | 3706 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Quantized KV costs **8–21% of decode** (63.7 vs 69.5 at 1 user, 15.8 vs 20.1 at 8) and halves VRAM per slot. Warm TTFT is lower
because the RAM-cache copies are half the size.

`--reasoning off` vs `--reasoning-format none` at 5 users (perf parity check):

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 1 | 29.9 | 29.9 | 131.1 | 134.9 | 2443 | 2525 | 2530 | 33.4 | 34.5 | 84 | 99.7% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Same throughput as `--reasoning-format none` (29.9 vs 29.4 in A_warm repeat 0). The reasoning mode has no
effect on speed when output length is fixed.

## 6b. QVAC Q8_0 (second A10, Q4_K_M-anchored)

Same image, same QVAC commit, same flags, same loadgen invocations. Machine 152062 instead of 123534.
Every launch was checked against the GGUF it was asked for (`results/head-to-head-a10-rerun/logs/cmds/`).
These are single runs, so there is no variance estimate.

**Anchor: Q4_K_M on this box, default config:**

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 68.7 | 68.1 | 67.6 | 67.6 | 106 | 138 | 144 | 14.3 | 14.9 | 34 | 99.8% | - | 0 |
| 5 | 1 | 30.0 | 29.9 | 127.4 | 130.5 | 3071 | 3108 | 3109 | 33.2 | 34.4 | 88 | 99.8% | - | 0 |
| 8 | 1 | 20.0 | 20.0 | 133.1 | 136.5 | 5258 | 5344 | 5360 | 49.9 | 51.2 | 124 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.0 | 68.9 | 47.4 | 49.9 | 3385 | 3421 | 3426 | 14.5 | 14.7 | 2547 | 0.1% | - | 0 |
| 5 | 1 | 17.9 | 14.9 | 70.6 | 72.9 | 5458 | 12599 | 13638 | 43.0 | 45.4 | 3309 | 0.1% | - | 0 |
| 8 | 1 | 11.0 | 10.6 | 78.0 | 81.7 | 6364 | 19211 | 23927 | 50.5 | 53.4 | 4005 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

The anchor reproduces the main box: warm 68.7 / 30.0 / 20.0 vs 69.4 / 29.4 / 20.05 (repeat 0), cold 69.0 / 17.9 / 11.0
vs 69.6 / 16.1 / 11.3.

**Q8_0, default config:**

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 48.6 | 48.5 | 48.2 | 48.2 | 99 | 102 | 102 | 20.5 | 21.0 | 38 | 99.8% | - | 0 |
| 5 | 1 | 29.1 | 27.6 | 113.6 | 121.8 | 3883 | 9338 | 9339 | 34.3 | 35.8 | 100 | 99.8% | - | 0 |
| 8 | 1 | 23.2 | 22.6 | 145.5 | 150.8 | 6161 | 6562 | 6579 | 42.8 | 45.2 | 149 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 48.6 | 48.5 | 37.0 | 38.4 | 3329 | 3361 | 3368 | 20.6 | 20.9 | 2524 | 0.1% | - | 0 |
| 5 | 1 | 17.4 | 13.1 | 66.6 | 68.6 | 5413 | 11622 | 13288 | 52.2 | 54.1 | 3345 | 0.1% | - | 0 |
| 8 | 1 | 12.0 | 11.5 | 84.2 | 88.3 | 6283 | 18961 | 23585 | 43.6 | 46.3 | 3945 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

**Q8_0, `-cram 0`:**

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 48.8 | 48.7 | 48.4 | 48.4 | 97 | 98 | 98 | 20.5 | 20.8 | 38 | 99.8% | - | 0 |
| 5 | 1 | 28.9 | 28.7 | 143.7 | 143.8 | 152 | 254 | 277 | 34.3 | 35.8 | 69 | 99.8% | - | 0 |
| 8 | 1 | 22.9 | 22.8 | 181.7 | 181.7 | 291 | 349 | 363 | 43.2 | 45.1 | 175 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

Takeaway: Q8_0 decodes **slower at 1 user** (48.6 vs 68.7: twice the weight bytes on a bandwidth-bound card)
and **slightly faster at 8 users** (22.9 vs 20.0 with `-cram 0`; aggregate 182 vs 159). It still sits at
53% of vLLM per user at 8 users, and **fails R1 cold** (17.4 at 5 users). VRAM is 17,265 MiB at 8 slots.

## 7. Cache behaviour

| | vLLM | QVAC |
|---|---|---|
| Mechanism | Automatic prefix caching, **global** across requests (paged blocks) | **Per-slot** KV prefix reuse (slot chosen by LCP similarity) + a host-RAM prompt cache (`-cram`, default 8 GiB) with `--cache-idle-slots` |
| Warm 7000-token prefix | 99.7% hit, server prefill 36–76 ms | 99.8% hit within the slot, server prefill 23–118 ms |
| Cross-slot (F_cross) | 99.7% (one primed request serves everyone) | **1/N**: 49.9% / 25.0% / 12.5% at 2/4/8, with `-cram` default, `-cram 0` and `-kvu` alike |
| Partial (~1000 unique tokens) | 88.7% | 88.8% |
| Side effects | none observed | **Default `-cram`:** every arriving request triggers a ~1,056 MiB slot-state copy into host RAM (`making room for prompt cache entry…` in the server log), serially, about 0.6 s each. This queues TTFT (2.5 s at 5 users, 4.4 s at 8 users warm) and degrades across repeats once the 8 GiB is full. **`-cram 0` removes it.** |

**Recommendation if QVAC is deployed anyway:** run with `-cram 0` and without `-kvu`, and expect the full cold
prefill (2.5–3.9 s) the first time each slot sees a given prefix.

## 8. Start-up, capacity and VRAM

| Scenario | vLLM (AWQ-INT4) | QVAC Q4_K_M (np8) | QVAC Q8_0 (np8, second box) |
|---|---|---|---|
| Fresh pod (incl. weight download) | 148.2 s (51.8 s download) | not measured end-to-end. GGUF download 46 s + boot ~3 s ≈ 49 s, plus the binary (no prebuilt CUDA build exists: 3 min build) | download 77 s (main box) |
| Warm restart | 40.8–41.9 s (4 boots) | **1.52–1.61 s** (4 boots) | 3.12–3.18 s (2) |
| Weights evicted from page cache (mincore-verified) | 41.8–42.8 s (3) | **2.96–2.98 s** (3) | 10.3–12.0 s (2) |
| Evicted + compile caches cleared | 89.5–89.9 s (3) | n/a (no JIT) | n/a |
| `/health` → first token | +0.13 s | +0.14 s | +0.17 s |
| Restart after SIGTERM | 41.2 s | **1.66 s** | — |
| VRAM after load | 20,541–21,621 MiB (0.90 util) | 14,051 MiB | 17,265 MiB |

`startupProbe` implication: vLLM needs ≥ 120 s of budget (cold-compile 90 s + margin); QVAC needs ~10 s.

**Capacity** (Q4_K_M, 8192 tokens per slot; VRAM after load):

| Slots | KV type | VRAM | Loads? | Notes |
|---|---|---|---|---|
| 8 | f16 | 14,051 MiB | yes | main config |
| 8 | q8_0 | 9,931 MiB | yes | −8–21% decode (above) |
| 12 | f16 | 18,659 MiB | yes | E_sat: 20.3 tok/s/user at 12 users; 16 users queue (TTFT p95 76 s) |
| 16 | f16 | — | **no** | `failed to create context` (out of VRAM) |
| 16 | q8_0 | 15,083 MiB | yes | E_sat: 15.9 at 12 users, 12.6 at 16; aggregate 180 tok/s (QVAC's highest) |
| 20 | q8_0 | 17,659 MiB | yes | not load-tested |
| 24 | q8_0 | 20,235 MiB | yes | 196,608 tokens of slots. Not load-tested. |

vLLM holds 92–100k tokens of shared KV. At 16 users on vLLM, all sequences ran with no queueing
(30.9 tok/s/user warm). QVAC can *hold* more 8k sequences with q8_0 KV, but each additional slot lowers
per-user decode further. 16 slots already sit below the 20 tok/s floor.

## 9. Failure, wedge and gateway behaviour

Sources: `results/head-to-head-a10/{vllm,qvac-q4km}/failure.txt`. In the vLLM file, the first block
(`vllm-fail`) had a probe-helper bug. Its storm "000 timeout" lines and doubled codes are harness
artifacts; the `vllm-fail2/3` blocks are the corrected runs.

| Probe | vLLM | QVAC |
|---|---|---|
| SSE shape | opening frame `delta.content: ""`; usage frame `choices: []`; `[DONE]` | opening frame `delta.content: null`; usage frame `choices: []` with `timings`; `[DONE]` |
| Gateway SSE passthrough | unchanged | unchanged |
| Oversized (9k-token prompt) | 400, context-length message; gateway rejects first with 400 `prompt_too_long` | 400 `exceed_context_size_error` with `n_prompt_tokens`/`n_ctx`; gateway 400 `prompt_too_long` first |
| Disconnect storm (16 streams cut at 1 s) | healthy; next request 200 in 124 ms; 0 running | healthy; next request 200 in 674 ms; 0 of 8 slots busy (slots released) |
| SIGSTOP whole process | `/health` and `/v1/models` hang; gateway `/readyz` 503 within one probe; recovers on SIGCONT | same: `/health`, `/v1/models` hang; `/readyz` 503; recovers. QVAC is a single process, so a stuck decode loop always looks like this. |
| SIGSTOP vLLM EngineCore only | **silent wedge**: `/health`, `/v1/models`, `/readyz` all 200 for 150 s while the stream stalls | n/a (single process) |
| Gateway wedge detector during freeze | `wedge_stall_seconds` stayed **0** for 150 s (window 120 s) | stayed **0** for 150 s |
| SIGTERM, stream in flight | exit after **116.7 s**; client gets no `[DONE]` | exit after **0.63 s**; stream cut, no `[DONE]` |
| Restart to `/health` | 41.2 s | 1.66 s |

**`gateway/upstream/wedge.py` did not fire against either real engine.** For QVAC this is mostly
harmless, because a stuck QVAC also fails `/readyz`. For vLLM's EngineCore-only stall, nothing in the
stack notices. Hypothesis: a stream that has started counts as "responded", so a mid-stream stall is
invisible to it. This needs a fix and a regression test.

## 10. Quality (temperature 0, thinking disabled per request)

Source: `head-to-head-a10/{vllm,qvac-q4km}/quality.jsonl`, `head-to-head-a10-rerun/qvac-q8/quality.jsonl`. Re-score with `python3 scripts/h2h_quality.py compare …`.

| Test | vLLM AWQ-INT4 | QVAC Q4_K_M | QVAC Q8_0 |
|---|---|---|---|
| Short answers (40) | 31/40 | **32/40** | 30/40 |
| `response_format: json_schema` (5) | 5/5 | **0/5: every request returns 400 `Failed to initialize samplers: std::exception`** | **0/5**, same error |
| Tool calls (5) | 5/5 (needs `--enable-auto-tool-choice --tool-call-parser hermes`) | 5/5 (jinja on by default) | 5/5 |
| Exact-text agreement with vLLM | — | 30/40 (after stripping QVAC's empty `<think>\n\n</think>` wrapper; 0/40 raw) | 32/40 (32/40 with Q4_K_M) |

The misses on both engines are genuine no-think arithmetic and string-reversal errors.
Even with `enable_thinking: false`, QVAC prefixes every answer with an empty `<think></think>` block,
and clients or the gateway must strip it.

## 11. Observability, security, operability

| Area | vLLM | QVAC |
|---|---|---|
| Metrics | rich `/metrics` (prefix-cache hits/queries, prefill/queue/TTFT/ITL histograms, KV usage, preemptions) | `--metrics`: 15 series (prompt tokens, cached tokens, predicted tokens, timings, busy slots). No latency histograms or queue-time series. `/slots` gives per-slot state. Both need the API key (401 without). |
| Equivalent alerts? | — | TTFT and queue-time alerts must come from the gateway's own metrics. Cache hit rate is derivable (`prompt_tokens_cached_total`). |
| Prompt text in logs | off (`--no-enable-log-requests`) | **none found**: a canary prompt was absent from the default log, from the `-v` log (1,770 lines) and from `--log-file`. But `--log-prompts-dir` exists and would write prompts to disk, so forbid it in deployment. |
| Native TLS | — | **Not in the documented build**: with `--ssl-*-file` the server exits with `the server is built without SSL support`. Needs an OpenSSL-enabled build (untested). |
| mTLS (client cert verification) | — | **no option exists** (no CA / verify flag in `--help`) |
| Image / artifact | 1 pinned image (≈10 GB) | build from source; binary depends on the CUDA toolkit used (13.0 here) |

## 12. What QVAC cannot do that vLLM can (this build, measured)

1. **Meet R1 with a cold prefix**: about 16 tok/s/user at 5 users in every config, against vLLM's 27.8.
2. **Scale decode with concurrency**: 20 tok/s/user at 8 users (vLLM 43.2). Aggregate tops out at about 160 tok/s, against vLLM's 340 (or 483 at 16 users).
3. **Share a cached prefix across concurrent requests**: per-slot caches only, so the hit rate is 1/N.
4. **Structured output**: `response_format: json_schema` fails with HTTP 400.
5. **Predictable latency** with default flags: the host-RAM cache serializes ~1 GiB copies, adding up to 4.4 s TTFT at 8 users and degrading over time.
6. **Native TLS** (needs a different build) and **mTLS** (no support at all).
7. **Latency metrics**: no TTFT, queue or ITL histograms server-side.

Where QVAC is better: start-up (1.5–3 s vs 41–90 s), SIGTERM exit, restart time, VRAM per
8-slot deployment, and the slot count reachable with q8_0 KV.

## 13. Corrections to earlier docs, and to this session

| # | Earlier claim | Correction | Evidence |
|---|---|---|---|
| 1 | The "7000-token" runs (PHASE1_RESULTS.md §5, PHASE1_RESULTS_QVAC.md) | They actually sent **~5,300 tokens** (loadgen sized by words × 0.75). B_legacy reproduces vLLM 48.0 at 8 users, against 48.29 originally. **The bar at a true 7000+512 is ~43 tok/s/user at 8 users (warm, AWQ-INT4).** | B_legacy vs A_warm |
| 2 | vLLM prefix-cache hit rate 98.5% | Warm runs only. Including the cold run, the saved metrics give **90.1%**. | `results/vllm-baseline/phase1-vastai-a10/vllm-metrics-final.txt` |
| 3 | PHASE1_RESULTS_QVAC.md:62-63: "server counts not used" | `usage_reported: true` on every request | raw jsonl |
| 4 | `wedge.py`: QVAC has no metrics endpoint | `--metrics` exists (15 series) | §11 |
| 5 | First pass: "on vLLM the API stayed green" under freeze | True only when freezing the EngineCore child. A whole-group freeze hangs `/health`. | failure.txt |
| 6 | Launch prompt: llama.cpp slots are separate caches, and the RAM cache might bridge them | Slots are separate, and **neither `-cram` nor `-kvu` bridges them** (1/N hit rate) | F_cross ×3 configs |
| 7 | Launch-prompt estimate "~30 warm / ~15 cold at 5 users on an A10" | Measured **26 warm / 16 cold**. Close; the cold estimate holds. | §4 |
| 8 | *This session:* the first Q8_0 pass | **Invalid: it served Q4_K_M.** `h2h_serve.sh` sourced env.sh after the caller's `GGUF=…`, so the override was lost. Fixed (caller overrides win). Data quarantined; Q8_0 re-measured on a second A10 with a Q4_K_M anchor (§6b). | `logs/cmds/q8-*.cmd` show `-m …Q4_K_M.gguf` |
| 9 | *This session:* the first vLLM failure-probe block | The probe helper passed a header as curl's timeout. Its "normal request after storm: 000 timeout" is an artifact. Corrected runs are `vllm-fail2/3`. | `vllm/failure.txt` |

## 14. Open items

- `wedge.py` misses mid-stream stalls. It needs a fix plus a regression test.
- vLLM SIGTERM drain takes 117 s and sends no `[DONE]`. Set `terminationGracePeriodSeconds` accordingly, and check how the gateway handles upstream EOF.
- vLLM cold at 8 users is 19.6, just under 20. Decide whether cold bursts at 8 users are in scope for R1. Chunked-prefill tuning is untested.
- QVAC with OpenSSL enabled (native TLS) is untested. A QVAC 24 h soak was not run; the 8 GiB RAM-cache degradation suggests a soak would matter for the default config.
- Re-confirm on the OCI `VM.GPU.A10.1`.

## 15. Cost

| Item | Value |
|---|---|
| Main box | $0.28/hr (incl. 120 GB disk), 9.1 h, destroyed 02:25 UTC: $2.53 |
| Q8_0 box | $0.23/hr, 1.3 h, destroyed 06:45 UTC: $0.48 |
| Teardown | both destroyed; instances and volumes `[]`; both throwaway SSH keys deleted from the account and locally; throwaway API keys redacted from all pulled logs |
| Spent | **$3.01** (credit 4.40 → 1.39) |
| Lost to harness bugs | ~$0.15 (boot-timer regex wait, idle gap after the vLLM matrix, wrong-revision boot) |
