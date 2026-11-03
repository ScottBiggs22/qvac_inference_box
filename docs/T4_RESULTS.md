# T4 results: QVAC (llama-server, Q4_K_M) vs vLLM (AWQ) on one Tesla T4

Status: **complete** (2026-11-03). Qwen3-8B, one vast.ai Tesla T4 (15,360 MiB, Turing sm_75, fp16 only), same harness and suites as the A10 head-to-head
(`docs/HEAD_TO_HEAD.md`). The full tables are in HEAD_TO_HEAD.md §16; this page is the summary and the decision guide. Raw data: `results/head-to-head-t4/`.
Rented boxes, mostly single runs (spread about 10%, up to about 25% at 1 user): re-confirm on the real ACA T4 before sizing production.
Every number below is **T4** unless a column says A10.

## Verdict

1. **One T4 holds everything.** QVAC 8 slots x 8k uses 13.9 GiB of 15; vLLM at 0.85 utilisation uses 12.4 GiB. No second card is needed to run either engine.
2. **The best engine depends on prompt length.** Per-user warm decode at 8 users:

   | Prompt + 512 | QVAC | vLLM | Leader |
   |---|---|---|---|
   | 7000 | 7.9 | 4.8 | QVAC (about 1.7x) |
   | 3500 | 9.0 | 8.2 | level |
   | 1500 | 9.9 | 13.8 | vLLM |
   | 500 | 10.4 | 21.4 | vLLM (2x) |

   On the A10 vLLM led at every length (43 vs 20 at 8 users, 7000 tokens). On the T4 vLLM loses far more with long prompts: its attention path is inefficient on Turing (neither
   available backend, `TRITON_ATTN` or `FLEX_ATTENTION`, fixes it), while QVAC's llama.cpp flash attention works and keeps QVAC nearly flat.
3. **DevOps shape (about 1k prompt, 512 out, ACA's 5 slots):** both engines meet the 20 tok/s single-stream bar (QVAC 25.6-28.8, vLLM 32.8-33.4). At 5 users **only vLLM is at the line**
   (20.6-20.8 warm in two runs, margin under 1 tok/s; 18.9 cold) and QVAC misses (12.4-13.8 per user).
4. **Neither engine reaches 20 tok/s per user at 5 users with 7000-token prompts on a T4** (QVAC 11.6, vLLM 7.0). A shorter context is the lever: shortening to 500 tokens takes vLLM to 25 at 5 users, and
   frees about 8 GiB of VRAM on QVAC.
5. **Compared with the A10**: QVAC about 0.4-0.5x, vLLM 0.11-0.28x at 7000 tokens, rising to 0.5-1.0x for vLLM at 500 tokens. Cold prefill of 7000 tokens: QVAC 7.8 s, vLLM 23.6 s (A10: 2.6 s, 2.0 s).
6. **Start-up:** QVAC 3 s warm, 7 s after eviction. vLLM 72 s warm, 155 s with cold caches (A10: 41 s, 90 s). A vLLM `startupProbe` on the T4 needs at least 240 s.
7. **Quality is unchanged**: vLLM 31/40, QVAC 33/40, json_schema 5/5 and tools 5/5 on both (tool calls need `--enable-auto-tool-choice --tool-call-parser hermes` on vLLM).

## Decision guide for ACA (one T4, 5 users)

| If real prompts are about | Pick | Why |
|---|---|---|
| 1k tokens or less | vLLM (0.85, `--dtype half`, hermes tool flags) | at the 20 tok/s line at 5 users; about 1.5x QVAC's aggregate throughput at 5 users (103 vs 61-68 tok/s) |
| 3-4k tokens | either; QVAC if boot time or VRAM headroom matter | decode is level; QVAC boots in seconds and uses less VRAM at shorter contexts |
| 7k tokens | QVAC (`-cram 0`) | vLLM is slower per user at every level; neither meets 20 at 5 users, so cut context or users |

Open for the team: which prompt length the real workload has (that single answer chooses the engine), and whether the 20 tok/s bar is hard at 5 users.

## What was measured

| Area | Where |
|---|---|
| Fit sweep (8 QVAC + 4 vLLM configs, VRAM and KV tokens) | HEAD_TO_HEAD §16.1, `sweep.jsonl` |
| A10 vs T4 per-user decode, prefill, TTFT, aggregate | §16.2, `COMPARE_A10_vs_T4.md` |
| Context curve 7000 / 3500 / 1500 / 500, both engines, warm and cold | §16.3, `CURVE_T4.md` |
| DevOps shape with repeats | §16.4 |
| Attention backend, flash attention, variance, start-up, quality | §16.5 |

Not run on the T4: failure/wedge probes, gateway, q8_0 KV load tests, more than two repeats.

## Reproduce

`git checkout bench/devops-formula && git apply docs/t4_session_scripts.patch`, then `docs/T4_HANDOFF.md` and `scripts/t4_suites.sh` / `t4_ctx_curve.sh` / `t4_parity.sh` on a vast.ai T4
(about 12-35 min setup depending on cores, several hours of runs, about $0.17-0.23/h). `scripts/h2h_compare.py` and `scripts/h2h_curve.py` generate the tables.
