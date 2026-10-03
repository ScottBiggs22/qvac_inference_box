#!/usr/bin/env bash
# Run the head-to-head load matrix against whatever engine h2h_serve.sh has up.
# See docs/QVAC_COMPARISON_LAUNCH_PROMPT.md and the plan's Phase 2 table.
#
#   bash scripts/h2h_bench.sh OUTDIR SUITE [SUITE...]
#
# The SAME suite definitions run against both engines: both serve `qwen3-8b`
# on 127.0.0.1:8000 with the same throwaway key, so the loadgen command lines
# are byte-identical and any difference in the numbers is the engine.
#
# Suites:
#   smoke     1 request: confirms prompt calibration, usage, cached_tokens /
#             timings, and a non-empty /metrics diff before paid time is spent.
#   A_warm    7000+512 (exact tokens), levels 1,2,3,4,5,8, 6 req/worker,
#             3 repeats, every level warmed at its own concurrency.
#   A_cold    same, every request a full prefix-cache miss.
#   B_legacy  the exact historical shape (--ctx-tokens 7000, i.e. ~5.3k
#             tokens; levels 1,3,5,8; 4 req; warmup 2 once), linking the new
#             numbers to the 48 tok/s/user bar.
#   C_short   500+256 at 1,5,8 (FAQ-style traffic).
#   D_long    7600+512 at 1,5 (context ceiling: 8112 of 8192).
#   E_sat     7000+512 at 12,16, 2 req/worker. Not part of the verdict.
#   F_partial shared system block + ~1000-token unique user turn, 1,5,8.
#   F_cross   cross-slot test: fresh prefix per level, primed by ONE request,
#             then N concurrent; levels 2,4,8, 3 repeats.
#   A_warm_short / A_cold_short   7000+512 at 1,5,8, 1 repeat (config variants)
#   R5        7000+512 at 5 only (reasoning-mode check)
#
# Each suite writes OUTDIR/SUITE.jsonl and OUTDIR/SUITE.out. nvidia-smi is
# sampled once a second into OUTDIR/gpu.csv for the whole invocation.
set -uo pipefail

H2H=${H2H:-/root/h2h}
REPO=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck disable=SC1091
[ -f "$H2H/env.sh" ] && . "$H2H/env.sh"
PORT=${PORT:-8000}
PY=${PY:-python3}

out=${1:?usage: $0 OUTDIR SUITE [SUITE...]}
shift
mkdir -p "$out"

common=(--url "http://127.0.0.1:$PORT/v1" --direct --api-key "$API_KEY"
        --model qwen3-8b --metrics-url "http://127.0.0.1:$PORT/metrics"
        --model-len 8192 --timeout 600)
main_shape=(--prompt-tokens 7000 --max-tokens 512 --ignore-eos)
warm_each=(--warmup 1 --warmup-per-level --warmup-concurrency level)

nvidia-smi --query-gpu=timestamp,utilization.gpu,utilization.memory,memory.used,power.draw,clocks.sm,clocks.mem,temperature.gpu,clocks_throttle_reasons.active \
  --format=csv -l 1 >> "$out/gpu.csv" 2>/dev/null &
smi=$!
trap 'kill $smi 2>/dev/null' EXIT

run() {
  local suite=$1; shift
  echo "=== $suite  $(date -u +%FT%TZ)" | tee -a "$out/timeline.txt"
  "$PY" -u "$REPO/scripts/loadgen.py" "${common[@]}" --tag "$suite" \
    -o "$out/$suite.jsonl" "$@" 2>&1 | tee "$out/$suite.out"
  echo "=== $suite done rc=${PIPESTATUS[0]}  $(date -u +%FT%TZ)" | tee -a "$out/timeline.txt"
}

for suite in "$@"; do
  case "$suite" in
    smoke)     run smoke "${main_shape[@]}" --levels 1 --requests 1 --warmup 0 ;;
    A_warm)    run A_warm "${main_shape[@]}" --levels 1,2,3,4,5,8 --requests 6 \
                 --repeats 3 "${warm_each[@]}" ;;
    A_cold)    run A_cold "${main_shape[@]}" --cache-mode cold \
                 --levels 1,2,3,4,5,8 --requests 6 --repeats 3 --warmup 2 ;;
    B_legacy)  run B_legacy --ctx-tokens 7000 --max-tokens 512 \
                 --levels 1,3,5,8 --requests 4 --warmup 2 ;;
    C_short)   run C_short --prompt-tokens 500 --max-tokens 256 --ignore-eos \
                 --levels 1,5,8 --requests 6 "${warm_each[@]}" ;;
    D_long)    run D_long --prompt-tokens 7600 --max-tokens 512 --ignore-eos \
                 --levels 1,5 --requests 3 "${warm_each[@]}" ;;
    E_sat)     run E_sat "${main_shape[@]}" --levels 12,16 --requests 2 \
                 "${warm_each[@]}" ;;
    F_partial) run F_partial "${main_shape[@]}" --cache-mode partial \
                 --user-turn-tokens 1000 --levels 1,5,8 --requests 4 \
                 "${warm_each[@]}" ;;
    F_cross)   run F_cross "${main_shape[@]}" --fresh-prefix-per-level \
                 --levels 2,4,8 --requests 1 --repeats 3 \
                 --warmup 1 --warmup-per-level --warmup-concurrency 1 ;;
    # Shorter variants for engine-config comparisons (Q8_0, KV quant, -kvu).
    A_warm_short) run A_warm_short "${main_shape[@]}" --levels 1,5,8 --requests 6 \
                 "${warm_each[@]}" ;;
    A_cold_short) run A_cold_short "${main_shape[@]}" --cache-mode cold \
                 --levels 1,5,8 --requests 6 --warmup 2 ;;
    A_warm_r2) run A_warm_r2 "${main_shape[@]}" --levels 1,2,3,4,5,8 --requests 6 \
                 --repeats 2 "${warm_each[@]}" ;;
    A_cold_r2) run A_cold_r2 "${main_shape[@]}" --cache-mode cold \
                 --levels 1,2,3,4,5,8 --requests 6 --repeats 2 --warmup 2 ;;
    R5)        run R5 "${main_shape[@]}" --levels 5 --requests 4 "${warm_each[@]}" ;;
    *) echo "unknown suite $suite" >&2 ;;
  esac
done

# Slot state (llama.cpp only; vLLM 404s) and a final metrics snapshot.
curl -s -H "Authorization: Bearer $API_KEY" "http://127.0.0.1:$PORT/slots" \
  > "$out/slots_final.json" 2>/dev/null || true
curl -s -H "Authorization: Bearer $API_KEY" "http://127.0.0.1:$PORT/metrics" \
  > "$out/metrics_final.txt" 2>/dev/null || true
