#!/usr/bin/env bash
# Start or stop one inference engine on the head-to-head box, with timing.
# See docs/QVAC_COMPARISON_LAUNCH_PROMPT.md.
#
#   bash scripts/h2h_serve.sh start vllm  NAME [extra engine flags...]
#   bash scripts/h2h_serve.sh start qvac  NAME [extra engine flags...]
#   bash scripts/h2h_serve.sh stop
#
# Both engines listen on 127.0.0.1:8000 and serve the model name `qwen3-8b`,
# so every loadgen invocation is byte-identical between them.
#
# WHY A FILE AND A PID FILE (docs/VAST_AI_OPERATIONS.md §7): `pkill -f` matches
# the ssh session's own command line and has killed measurement runs before.
# The engine is started in its own session (setsid) and stopped by process
# group from the recorded pid, so no pattern match is ever needed.
#
# Paths and the throwaway key come from /root/h2h/env.sh:
#   API_KEY=...            throwaway, generated on the box
#   VLLM_MODEL=Qwen/Qwen3-8B-AWQ   VLLM_REV=4da05a8e...
#   GGUF=/root/models/Qwen3-8B-Q4_K_M.gguf   (override per run: GGUF=... bash ...)
#   QVAC_BIN=/root/qvac/build/bin/llama-server
set -uo pipefail

H2H=${H2H:-/root/h2h}
RUN=$H2H/run
LOGS=$H2H/logs
mkdir -p "$RUN" "$LOGS"
# Per-run overrides set by the caller (GGUF=... bash h2h_serve.sh ...) must
# win over env.sh's defaults. Sourcing env.sh unguarded silently reset GGUF to
# Q4_K_M, so the first head-to-head's "Q8_0" runs all served Q4_K_M.
_gguf=${GGUF:-}; _port=${PORT:-}; _qvac_bin=${QVAC_BIN:-}
# shellcheck disable=SC1091
[ -f "$H2H/env.sh" ] && . "$H2H/env.sh"
GGUF=${_gguf:-${GGUF:-}}; QVAC_BIN=${_qvac_bin:-${QVAC_BIN:-}}
PORT=${_port:-${PORT:-8000}}

now() { date +%s.%N; }

stop_engine() {
  if [ -f "$RUN/engine.pid" ]; then
    pid=$(cat "$RUN/engine.pid")
    if kill -0 "$pid" 2>/dev/null; then
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -KILL -- "-$pid" 2>/dev/null || true
    fi
    rm -f "$RUN/engine.pid"
  fi
  # Wait for the GPU to actually be free, so the next engine starts clean and
  # its VRAM reading is its own.
  for _ in $(seq 1 60); do
    used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
    [ "${used:-0}" -lt 500 ] && break
    sleep 1
  done
  echo "stopped; GPU memory used: ${used:-?} MiB"
}

case "${1:-}" in
  stop) stop_engine; exit 0 ;;
  start) ;;
  *) echo "usage: $0 start {vllm|qvac} NAME [flags...] | stop" >&2; exit 2 ;;
esac

engine=${2:?engine}
name=${3:?name}
shift 3

stop_engine >/dev/null

case "$engine" in
  vllm)
    # PHASE1_RESULTS.md §2 / VAST_AI_RUNBOOK.md:166-174, plus:
    #   --served-model-name qwen3-8b       identical loadgen --model on both engines
    #   --api-key                          QVAC runs with one; parity
    #   --enable-prompt-tokens-details     per-request cached_tokens in usage
    cmd=(vllm serve "${VLLM_MODEL:-Qwen/Qwen3-8B-AWQ}"
         --revision "${VLLM_REV:-4da05a8edb55c6046cce958586c33b61da07bb79}"
         --max-model-len 8192 --gpu-memory-utilization 0.90
         --no-enable-log-requests --no-enable-log-outputs
         --served-model-name qwen3-8b --api-key "$API_KEY"
         --enable-prompt-tokens-details
         --host 127.0.0.1 --port "$PORT" "$@")
    ready_re='Application startup complete'
    ;;
  qvac)
    # PHASE1_RESULTS_QVAC.md: -c is the TOTAL across slots, so -np 8 -c 65536
    # is 8192 tokens per slot. REASONING_FORMAT defaults to `auto`, the
    # production value. The head-to-head ran `none` (think text in `content`),
    # which breaks json_schema under enable_thinking:false with HTTP 400
    # (docs/QVAC_FIXES_RESULTS.md). loadgen now times `reasoning_content` too,
    # so perf and production run the same flags.
    np=${NP:-8}
    cmd=("${QVAC_BIN:-/root/qvac/build/bin/llama-server}"
         -m "${GGUF:-/root/models/Qwen3-8B-Q4_K_M.gguf}"
         -c $((8192 * np)) -np "$np" -ngl 99 -fa on
         --api-key "$API_KEY" --alias qwen3-8b --metrics
         --reasoning-format "${REASONING_FORMAT:-auto}"
         --host 127.0.0.1 --port "$PORT" "$@")
    ready_re='model loaded|listening on'
    ;;
  *) echo "unknown engine $engine" >&2; exit 2 ;;
esac

log=$LOGS/$name.log
printf '%q ' "${cmd[@]}" | sed "s/$API_KEY/REDACTED/g" > "$RUN/$name.cmd"
echo >> "$RUN/$name.cmd"
t_start=$(now)
setsid nohup "${cmd[@]}" > "$log" 2>&1 < /dev/null &
pid=$!
disown
echo "$pid" > "$RUN/engine.pid"
printf '{"name":"%s","engine":"%s","pid":%s,"t_start":%s,"ready_re":"%s","log":"%s"}\n' \
  "$name" "$engine" "$pid" "$t_start" "$ready_re" "$log" > "$RUN/current.json"
echo "started $engine as $name (pid $pid), log $log"
