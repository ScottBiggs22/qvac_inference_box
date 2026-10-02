#!/usr/bin/env bash
# Failure / wedge / gateway-integration probes for one engine (axes 7 and 8).
#
#   bash scripts/h2h_failure.sh ENGINE NAME OUTDIR
#
# Expects the engine on :8000 (h2h_serve.sh) and the real gateway on :8080
# (/root/h2h/gateway.sh). Runs, in order, so the destructive steps come last:
#   1. SSE frame shape, direct and through the gateway (first/last frames)
#   2. oversized request (9000-token prompt > 8192), direct and via gateway
#   3. client-disconnect storm: 16 streams cut after 1 s; is the engine still
#      healthy, does a normal request still complete, are slots released?
#   4. freeze: kill -STOP the engine's process group mid-stream. Probe the
#      engine's /health and /v1/models and the gateway's /readyz every 10 s for
#      150 s (the gateway's wedge window is 120 s), then SIGCONT and recover.
#   5. SIGTERM mid-stream: what the client receives, how long the engine takes
#      to exit; then crash-and-restart time to /health.
# Everything is appended to OUTDIR/failure.txt with timestamps.
set -uo pipefail
. /root/h2h/env.sh
engine=${1:?engine}; name=${2:?name}; out=${3:?outdir}
mkdir -p "$out"; log=$out/failure.txt
GW=$(cat /root/h2h/gw_key)
E=http://127.0.0.1:8000; G=http://127.0.0.1:8080
say() { echo "[$(date -u +%T.%3N)] $*" | tee -a "$log"; }
# code URL TIMEOUT [extra curl args...] -> "HTTP time" (000 = no response)
code() { local u=$1 t=$2; shift 2
  curl -s -o /dev/null -m "$t" -w '%{http_code} %{time_total}s' "$@" "$u" 2>/dev/null; }
body() { python3 - "$@" <<'PY'
import json, sys
words = int(sys.argv[1]); stream = sys.argv[2] == "1"; mt = int(sys.argv[3])
print(json.dumps({"model": "qwen3-8b", "max_tokens": mt, "stream": stream,
                  "stream_options": {"include_usage": True} if stream else None,
                  "messages": [{"role": "user", "content": ("ledger " * words) + "Summarise."}]}))
PY
}

STEPS=" ${STEPS:-sse oversized storm freeze sigterm} "
has() { [[ "$STEPS" == *" $1 "* ]]; }
say "=== $engine / $name failure probes (steps:$STEPS freeze=${FREEZE:-group})"

# 1. SSE shape
body 5 1 8 > /tmp/b_small.json
body 3000 1 512 > /tmp/b_mid.json
has sse && for tgt in direct gateway; do
  if [ $tgt = direct ]; then u=$E/v1/chat/completions; k=$API_KEY; else u=$G/v1/chat/completions; k=$GW; fi
  curl -sN -m 60 -H "Authorization: Bearer $k" -H 'Content-Type: application/json' \
    -d @/tmp/b_small.json "$u" > "$out/sse_$tgt.txt"
  say "SSE $tgt: $(grep -c '^data:' "$out/sse_$tgt.txt") frames"
  say "  first: $(grep '^data:' "$out/sse_$tgt.txt" | head -1 | cut -c1-240)"
  say "  last-2: $(grep '^data:' "$out/sse_$tgt.txt" | tail -2 | head -1 | cut -c1-300)"
  say "  last: $(grep '^data:' "$out/sse_$tgt.txt" | tail -1)"
done

# 2. oversized
body 9000 0 16 > /tmp/b_big.json
has oversized && for tgt in direct gateway; do
  if [ $tgt = direct ]; then u=$E/v1/chat/completions; k=$API_KEY; else u=$G/v1/chat/completions; k=$GW; fi
  r=$(curl -s -m 60 -w ' HTTP %{http_code}' -H "Authorization: Bearer $k" -H 'Content-Type: application/json' -d @/tmp/b_big.json "$u")
  say "oversized $tgt: ${r: -9} $(echo "$r" | cut -c1-260)"
done

# 3. disconnect storm
if has storm; then
say "disconnect storm: 16 streams cut at 1 s"
for i in $(seq 1 16); do
  curl -sN -m 1 -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
    -d @/tmp/b_mid.json $E/v1/chat/completions > /dev/null 2>&1 &
done
wait
sleep 5
say "  after storm: health $(code $E/health 5)"
say "  normal request: $(code $E/v1/chat/completions 60 -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' -d @/tmp/b_small.json)"
if [ "$engine" = qvac ]; then
  say "  slots processing: $(curl -s -H "Authorization: Bearer $API_KEY" $E/slots | python3 -c 'import json,sys; s=json.load(sys.stdin); print(sum(1 for x in s if x.get("is_processing")), "of", len(s))')"
else
  say "  running/waiting: $(curl -s $E/metrics | grep -E '^vllm:num_requests_(running|waiting)' | awk '{print $1"="$2}' | tr '\n' ' ')"
fi
fi

# 4. freeze
if has freeze; then
pid=$(cat /root/h2h/run/engine.pid)
# FREEZE=group stops the whole engine (API server too); FREEZE=core stops only
# vLLM's EngineCore child, leaving its HTTP server live -- the wedge shape
# where /health stays green. QVAC is one process, so only `group` applies.
if [ "${FREEZE:-group}" = core ]; then
  fpid=$(ps -o pid=,cmd= --ppid "$pid" | grep -i enginecore | awk '{print $1}' | head -1)
  stop() { kill "-$1" "$fpid"; }
  say "freeze target: EngineCore pid $fpid (API server $pid stays running)"
else
  stop() { kill "-$1" -- "-$pid"; }
  say "freeze target: whole process group $pid"
fi
curl -sN -m 400 -H "Authorization: Bearer $GW" -H 'Content-Type: application/json' \
  -d @/tmp/b_mid.json $G/v1/chat/completions > "$out/freeze_stream.txt" 2>&1 &
cpid=$!
sleep 3
say "freeze: SIGSTOP"
stop STOP
for t in 0 10 20 30 40 50 60 70 80 90 100 110 120 130 140 150; do
  say "  t+${t}s engine /health $(code $E/health 3) | /v1/models $(code $E/v1/models 3 -H "Authorization: Bearer $API_KEY") | gw /readyz $(curl -s -m 8 -o /dev/null -w '%{http_code}' $G/readyz) | wedge $(curl -s -m 8 $G/metrics | awk '/^bkn301_gateway_upstream_wedge_(suspected|stall_seconds)/ {printf "%s=%s ", $1, $2}' | sed 's/bkn301_gateway_upstream_//g; s/{upstream="0"}//g')"
  sleep 7
done
grep -iE "wedge|stall|breaker" /root/h2h/logs/gateway_*.log | tail -5 | sed 's/^/  gwlog: /' | tee -a "$log"
stop CONT
say "freeze: SIGCONT sent"
sleep 5
say "  after CONT: health $(code $E/health 5) | gw readyz $(code $G/readyz 8)"
wait $cpid 2>/dev/null
say "  frozen client stream: $(grep -c '^data:' "$out/freeze_stream.txt") frames, last: $(tail -c 160 "$out/freeze_stream.txt" | tr '\n' ' ')"
fi

# 5. SIGTERM mid-stream, then restart
if has sigterm; then
curl -sN -m 120 -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d @/tmp/b_mid.json $E/v1/chat/completions > "$out/sigterm_stream.txt" 2>&1 &
cpid=$!
sleep 4
t0=$(date +%s.%N)
say "SIGTERM -> $pid"
kill -TERM "$pid"
while kill -0 "$pid" 2>/dev/null; do sleep 0.1; done
say "  engine exited after $(python3 -c "import time; print(round(time.time()-$t0,2))") s"
wait $cpid; rc=$?
say "  client: curl rc=$rc, $(grep -c '^data:' "$out/sigterm_stream.txt") frames, [DONE]=$(grep -c '\[DONE\]' "$out/sigterm_stream.txt"), tail: $(tail -c 160 "$out/sigterm_stream.txt" | tr '\n' ' ')"
say "  gw readyz with engine down: $(code $G/readyz 8)"
rm -f /root/h2h/run/engine.pid
t0=$(date +%s.%N)
GGUF=${GGUF} bash /root/repo/scripts/h2h_serve.sh start "$engine" "$name-restart" ${RESTART_FLAGS:-} > /dev/null
until curl -sf $E/health > /dev/null; do sleep 0.1; done
say "  restart to /health: $(python3 -c "import time; print(round(time.time()-$t0,2))") s"
sleep 2
say "  gw readyz after restart: $(code $G/readyz 8)"
fi
say "=== done"
