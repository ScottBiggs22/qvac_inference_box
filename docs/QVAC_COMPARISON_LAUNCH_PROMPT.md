# Launch prompt — rigorous head-to-head: vLLM vs QVAC Fabric on an A10

**Written:** 2026-10-01, after the first-pass session (`docs/PHASE1_RESULTS_QVAC.md`).
**Purpose:** measure the real gap between the production vLLM stack and QVAC
Fabric, on the **same A10**, on more axes than tok/s/user. This is a
management-directed swap; QVAC is expected to be worse. The deliverable is an
honest, reproducible measurement of *how much*, not a reassuring number.

Start a new Claude Code session from this repo (`bkn301-qvac-inference`) and
paste the block under the line.

---

You are running the head-to-head comparison between vLLM (current production,
`bkn301-inference` `main`) and QVAC Fabric (this repo). Read, in order:
`docs/PHASE1_RESULTS_QVAC.md` (what the first pass found, including corrections
to `docs/QVAC_FORK.md`), `docs/QVAC_FORK.md`, `docs/PHASE1_RESULTS.md` §5 (the
vLLM baseline and its exact methodology), `docs/VAST_AI_OPERATIONS.md`.

## The bar

**vLLM on an A10 held ~48 tok/s/user at 8 concurrent users at 8k context**
(measured 48.11 through the gateway, 48.29 direct; Qwen3-8B-AWQ, 7000-token
prompt + 512 output, `--max-model-len 8192`, warm prefix cache). Hard floor is
R1: ≥20 tok/s/user at 5 concurrent. Management's real question: how close does
QVAC get to the bar, and does it clear the floor?

## What is already known (don't re-measure; do cross-check on the A10)

These come from an **RTX 3090, not an A10** (no A10 was rentable on 2026-10-01).
The 3090 has ~1.5x the A10's memory bandwidth, so they flatter QVAC. Q4_K_M,
`-np 8 -c 65536` (8k per slot), 7000+512, `--reasoning-format none`:

| Conc | warm tok/s/user | cold-prefix tok/s/user |
|---|---|---|
| 1 | 117.2 | 116.6 |
| 5 | 46.5 | 23.0 |
| 8 | 32.8 | 18.6 |

vLLM A10 baseline for the same shape: warm 71.5 / 50.9 / **48.1** at 1/5/8;
cold 72.0 / 28.9 at 1/5 (cold @8 was never measured for vLLM — measure it).
Aggregate @ 8: vLLM 360 tok/s vs QVAC-on-3090 238. Unmeasured estimate: scaling
by bandwidth puts QVAC-on-A10 near ~30 warm / ~15 cold at 5 concurrent, i.e.
**cold may fall below the 20 floor**. Confirm or refute this first.

## Hard rules

- **Same A10, same hour if possible, both engines.** Never compare numbers from
  different cards. If no A10 is rentable, stop and tell the user; do not
  substitute a card silently. Check `vastai search offers 'gpu_name=A10 num_gpus=1'`
  (it returned nothing on 2026-10-01; also try `rentable=true`, and retry later).
- **Context windows must be stated for every number.** The first pass used 8192
  tokens *per slot* for QVAC (`-c = 8192 x np`; `-c` is the **total** across
  slots) and vLLM used `--max-model-len 8192` per sequence with shared dynamic
  KV. Request shape was identical (7000 prompt + 512 out). Keep it that way, and
  report whatever you change.
- Same model family, flagged honestly: vLLM ran **Qwen3-8B-AWQ**, QVAC runs
  **Qwen3-8B Q4_K_M**. Different quantizations; say so, and run Q8_0 on QVAC as
  a quality/speed reference.
- Same harness: `scripts/loadgen.py`, run **on the box**, direct and through the
  gateway. Reuse the identical `--ctx-tokens 7000 --max-tokens 512`, levels
  1,3,5,8, and `--unique-prefix` runs (extend levels to 8 for cold; vLLM's cold
  run only covered 1 and 5).
- Synthetic prompts, throwaway keys only; `scripts/pack_for_box.sh` for
  transfers; no push/merge without approval; local commits are fine; destroy the
  instance and key afterward; track cost (balance was $4.418 after the first
  pass; an A10 is ~$0.26/hr; budget a stop-and-ask at ~$2).
- Report failures as failures. Mark corrections to earlier docs as corrections.

## Hard-won operational notes (from the first pass)

- **Check `cat /sys/fs/cgroup/cpu.max`, not `nproc`.** One box reported 16 CPUs
  but was capped to ~1.9; the CUDA build crawled. Filter offers on
  `cpu_cores_effective>=12`. With real cores the build takes ~10 min.
- QVAC has no prebuilt CUDA binary. Build: `cmake -B build -DGGML_CUDA=ON
  -DCMAKE_CUDA_ARCHITECTURES=86` (A10 is sm_86) on
  `nvidia/cuda:12.4.1-devel-ubuntu22.04` (needs offer `cuda_max_good >= 12.4`).
  Pinned: `tetherto/qvac-fabric-llm.cpp` `7bb2161c690b…`. A10 build artifacts do
  not need rebuilding if you keep the box between engines; vLLM needs its own
  image (`vllm/vllm-openai:v0.28.0`, CUDA 13.0 — see `VAST_AI_OPERATIONS.md` §3)
  so plan two images or two sequential boxes of the same machine class.
- Model: `Qwen/Qwen3-8B-GGUF` @ `7c41481f57cb95916b40956ab2f0b139b296d974`,
  `Qwen3-8B-Q4_K_M.gguf`, sha256 `d98cdcbd…5785`.
- Launch scripts live on the box as files, never inline in an ssh command
  (`pkill -f` kills your own session; see `VAST_AI_OPERATIONS.md` §7).
- zsh does not word-split `$VAR`; `set -- $s` silently fails. Use an ssh config
  file and write values explicitly.
- Qwen3 thinks by default: QVAC returns thinking in `reasoning_content` with
  empty `content`, which makes `loadgen.py` report nonsense TTFT/tok/s. Use
  `--reasoning-format none` to put think text in `content` (matches vLLM with no
  reasoning parser), and state that choice. Also measure `--reasoning off`.
- `/v1/models` requires the API key on QVAC; `/health` does not.
- Real `llama-server` flags: `-m`, `-c`, `-np`, `-ngl 99`, `-fa`, `--api-key`,
  `--alias`, `--metrics`, `--host/--port`, `--ssl-key-file/--ssl-cert-file`,
  `-ctk/-ctv`, `-kvu`. There is **no `--openai`**. `deploy/k8s/probes.yaml` and
  `README.md` still carry the old flags: fix them in place as part of this work.

## Axes to compare (beyond tok/s/user)

Each: state the method, the card, the context window, the quantization.

1. **Per-user decode and TTFT** at 1/3/5/8, warm and cold prefix, p50/p95, at
   7000+512. Also at **shorter prompts** (e.g. 500-token FAQ-style, the real
   traffic shape) and **longer** (up to the context ceiling).
2. **Aggregate throughput and scaling curve**, and where each engine saturates;
   push beyond 8 (12, 16, 24) and find the knee and the failure mode (queueing
   vs rejection vs OOM).
3. **Prefix caching behaviour.** vLLM hit 98.5% on the default shape. Measure
   QVAC's `cache_prompt` / `--cache-reuse` hit rate across slots (does a prefix
   cached in slot A help a request landing in slot B? llama.cpp slots are
   separate caches) and the effect on warm TTFT under 8 concurrent users.
4. **Memory and capacity.** VRAM per slot is already known (1152 MiB per 8k).
   Measure max concurrent 8k sequences actually held, effect of `-ctk q8_0
   -ctv q8_0` on capacity and speed, and compare against vLLM's 92,544-token
   measured KV capacity.
5. **Quality parity.** Same prompts to both engines at temperature 0: exact-match
   / agreement rate, and an eval subset (the repo has `scripts/b3_probe.py` and
   the PRD §3.5/§4.8 eval notes). AWQ-INT4 vs Q4_K_M vs Q8_0. Include structured
   output (`response_format` JSON schema) and tool-call correctness, since the
   gateway forwards both.
6. **Start-up.** True cold start (weights not in page cache, fresh pod disk) and
   warm restart, for both. vLLM's reference is 153 s cold / 45 s warm. QVAC's
   warm boot was 3.2 s; cold is unmeasured. Sets `probes.yaml` `startupProbe`.
7. **Failure and wedge behaviour.** Re-run the `kill -STOP` freeze test (first
   pass: on QVAC `/health` and `/v1/models` hang; on vLLM the API stayed green).
   Also test a stuck decode with live HTTP threads (e.g. an oversized request,
   client disconnect storms, `SIGTERM` mid-stream), crash-and-restart behaviour,
   and whether `gateway/upstream/wedge.py` fires against the **real** QVAC
   server through the real gateway (not the stub).
8. **Gateway integration.** Gateway round-trip, `/readyz`, retry/breaker, SSE
   opening-frame shape (`content:null` vs `""`, trailing `usage` frame, `[DONE]`)
   against `gateway/routes/chat.py` and `stub/server.py`; what overhead the
   gateway adds on QVAC.
9. **Observability.** QVAC `--metrics` series vs vLLM's `/metrics`; `/slots`.
   Can Prometheus alerts equivalent to the vLLM ones be built?
10. **Security and data hygiene.** Request-log retention (first pass: default
    logs contained no prompt text; re-check with `-v`/`--log-file` and slot
    save/restore), native TLS via `--ssl-*-file` and whether mTLS client-cert
    verification is possible (item 6), auth model for k8s probes (item 7).
11. **Operability.** Build reproducibility and image size, upgrade story
    (Tether fork tracks llama.cpp), CUDA/driver requirements, behaviour at 24 h
    soak (memory growth, throughput drift) if budget allows.
12. **Cost per token.** $/hr / aggregate tok/s at 8 concurrent, per engine.

## Order of work

1. Book a single A10 with `cpu_cores_effective>=12`; run `scripts/verify_box.sh`
   (now warns, not fails, on a non-A10; on an A10 it should pass clean).
2. Engine A (vLLM v0.28.0, Qwen3-8B-AWQ, `--max-model-len 8192`
   `--gpu-memory-utilization 0.90`, as in `PHASE1_RESULTS.md` §2): re-measure
   axes 1, 2, 3, 6 on this exact box. Do not trust the old numbers alone; box
   variance matters.
3. Engine B (QVAC Q4_K_M, then Q8_0): same axes, plus 4-11.
4. Gateway-in-the-loop runs for both (axes 7, 8).
5. Capture raw output to `results/head-to-head-a10/<engine>/` (boot logs, raw
   loadgen jsonl, `box_provenance.txt`), write `docs/HEAD_TO_HEAD.md` with the
   comparison tables, the verdict against the 48 tok/s/user @ 8 bar and the
   20 tok/s floor, and a plain list of what QVAC cannot do that vLLM can.
6. Update `deploy/k8s/probes.yaml`, `README.md`, `docs/QVAC_FORK.md` checklist
   with measured values. Teardown per `VAST_AI_OPERATIONS.md` §9.

## One thing to do deliberately

Tell the user when a number surprises you or an assumption here fails against
the real binary. The first pass found six wrong claims in `QVAC_FORK.md`
(`--openai`, `/health`, `/metrics`, TLS, batching, ctx default). Expect more.
