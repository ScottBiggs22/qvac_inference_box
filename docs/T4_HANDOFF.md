# T4 benchmark: handoff (final, 2026-11-03)

**State: complete. No box is running** (`vastai show instances`, `show volumes`, `show ssh-keys` were all `[]` after the last destroy). Nothing is committed.
Results and write-up: `docs/T4_RESULTS.md` (summary and decision guide) and `docs/HEAD_TO_HEAD.md` §16 (full tables).

## What exists (all uncommitted)

| Path | What |
|---|---|
| `docs/T4_RESULTS.md` | Verdict, decision guide for ACA, what was measured |
| `docs/HEAD_TO_HEAD.md` §16 | T4 tables 16.1-16.6 appended to the A10 doc (a tracked file, edited) |
| `results/head-to-head-t4/` | Raw data from three boxes, API key redacted (0 matches); `CURVE_T4.md`, `COMPARE_A10_vs_T4.md`, `sweep.jsonl`, `parity_night3/`, logs |
| `docs/t4_session_scripts.patch` | All script changes vs `bench/devops-formula` @ 20f89ae; `git apply --check` OK. New: `t4_sweep.sh`, `t4_suites.sh` (with `extras-fa/flex/rep` selectors), `t4_ctx_curve.sh`, `t4_parity.sh`, `h2h_compare.py`, `h2h_curve.py`; `CTX`/`PROMPT_TOKENS` in `h2h_serve.sh`/`h2h_bench.sh`; T4 warning in `verify_box.sh` |

To restore the scripts: `git checkout bench/devops-formula && git apply docs/t4_session_scripts.patch`. The scratch worktree under `/private/tmp/...` is temporary.

## Quarantined data (never quote)
`vllm-c8k-u085/old_killed_run_night1/`, `vllm-c4k-u085/old_killed_night2/`, `parity_night3/quality_vllm.*` (tool family invalid: flags were missing), and from the A10 work `qvac-q8-INVALID-served-q4km` and the 3090 data.

## Not done (optional)
- Failure/wedge probes and the gateway suite on the T4.
- A profile of the vLLM attention kernels to confirm the "kernel inefficiency" inference in §16.5 (it is from timings and arithmetic only).
- More repeats (spread is about 10%, up to about 25% at 1 user for QVAC).
- Re-confirm on the real ACA T4 host (model load from Azure Files will change cold-start figures).

## Decisions for the team
1. Real prompt length on ACA: about 1k tokens favours vLLM (at the 20 tok/s line at 5 users); about 7k favours QVAC (neither reaches 20).
2. Whether 20 tok/s per user at 5 users is a hard bar. If so on this card, shorten the context or reduce users.
3. If vLLM is chosen, it needs `--enable-auto-tool-choice --tool-call-parser hermes`, a `startupProbe` of at least 240 s, and `--dtype half`.

## Corrections made along the way
- The earlier note "vLLM misses the DevOps bar at 18.9" was the 7000-token figure, not the DevOps shape; vLLM on the DevOps shape meets the 1-user bar and sits at the 5-user line.
- The first vLLM "warm" boot (190 s) had empty compile caches; the warm figure is 72 s.
- The first vLLM tool-call result (0/5) was a missing-flag bug in my parity script, not an engine difference.

## Operational lessons (for the next vast.ai session)
T4 offers vanish in seconds (use a poller that creates immediately); machine 149854 stalled pulling the image; 150014 and 155649 worked; the `ssh` that launches a background job can hang, so redirect all
fds and verify with a second connection; stop a run by killing the chain parent first, then loadgen, by PID; redact the API key on the box before packing (vLLM's server log prints it);
do not leave the box idle after the last stage (the chain finished 40 minutes before it was noticed this time).
