#!/usr/bin/env bash
# The whole QVAC half of the head-to-head, queued so the GPU never idles.
# Run detached on the box:  setsid nohup bash scripts/h2h_qvac_all.sh STEP... &
# With no STEP arguments, runs every step in priority order. Progress lines
# start with "STEP" in /root/h2h/logs/qvac_all.out.
set -uo pipefail
. /root/h2h/env.sh
REPO=/root/repo
O=/root/h2h/out
Q4=/root/models/Qwen3-8B-Q4_K_M.gguf
Q8=/root/models/Qwen3-8B-Q8_0.gguf
step() { echo "STEP $* $(date -u +%T)"; }
up() {  # up NAME [flags...]   (GGUF / NP from the environment)
  bash $REPO/scripts/h2h_serve.sh start qvac "$@" > /dev/null
  for _ in $(seq 1 600); do curl -sf localhost:8000/health > /dev/null && break; sleep 0.2; done
  curl -sf localhost:8000/health > /dev/null || { echo "  $1: did not come up"; tail -5 /root/h2h/logs/$1.log; return 1; }
  echo "  $1 up: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
}
bench() { local out=$1; shift; bash $REPO/scripts/h2h_bench.sh "$out" "$@" > /dev/null 2>&1; echo "  bench $out $*: done"; }

steps=${*:-boots main cram0full kvvariants sat q8 reasoning quality gateway failure obs}
for s in $steps; do
case $s in
  boots)
    step boots
    mkdir -p $O/qvac-q4km
    for m in warm cold_weights warm cold_weights warm cold_weights warm; do
      i=$((${i:-0}+1))
      OUTSUB=qvac-q4km GGUF=$Q4 bash /root/h2h/boot_bg.sh qvac "q4km-boot$i-$m" "$m" > /dev/null 2>&1
    done
    tail -7 $O/qvac-q4km/boot.jsonl | python3 -c "
import json,sys
for l in sys.stdin:
    r=json.loads(l); print('  ', r['name'], r.get('resident_before_start'), r.get('t_health_s'), r.get('t_first_token_s'), r.get('gpu_used_mib_after_load'), r.get('error'))"
    ;;
  main)
    step main
    GGUF=$Q4 up q4km-main || continue
    bench $O/qvac-q4km smoke A_warm A_cold F_partial F_cross B_legacy C_short D_long E_sat
    ;;
  cram0full)
    # The fork's host-RAM prompt cache (-cram, default 8 GiB, with
    # --cache-idle-slots) serially copies ~1 GiB of slot KV per arriving
    # request and degrades once full. Full sweep with it off: QVAC's best case.
    step cram0full
    GGUF=$Q4 up q4km-cram0full -cram 0 || continue
    for suite in A_warm A_cold; do
      bash $REPO/scripts/h2h_bench.sh $O/qvac-q4km-cram0 "${suite}_r2" > /dev/null 2>&1
      echo "  bench $O/qvac-q4km-cram0 ${suite}_r2: done"
    done
    ;;
  kvvariants)
    # Prefix-cache plumbing: host-RAM prompt cache off, and unified KV on.
    step kvvariants
    GGUF=$Q4 up q4km-cram0 -cram 0 && bench $O/qvac-q4km-cram0 F_cross F_partial
    GGUF=$Q4 up q4km-kvu -kvu && bench $O/qvac-q4km-kvu F_cross A_warm_short A_cold_short
    # Quantized KV at the same 8 slots: speed effect.
    GGUF=$Q4 up q4km-kvq8 -ctk q8_0 -ctv q8_0 && bench $O/qvac-q4km-kvq8 A_warm_short A_cold_short
    ;;
  sat)
    # Capacity: how many 8k slots fit, f16 vs q8_0 KV; failure mode past 8.
    step sat
    for np in 12 16; do
      NP=$np GGUF=$Q4 up q4km-np$np && bench $O/qvac-q4km-np$np E_sat \
        || echo "  np$np f16 KV: failed to load (see log)"
    done
    NP=16 GGUF=$Q4 up q4km-np16-kvq8 -ctk q8_0 -ctv q8_0 && bench $O/qvac-q4km-np16-kvq8 E_sat
    NP=20 GGUF=$Q4 up q4km-np20-kvq8 -ctk q8_0 -ctv q8_0 || echo "  np20 q8 KV: failed to load"
    NP=24 GGUF=$Q4 up q4km-np24-kvq8 -ctk q8_0 -ctv q8_0 || echo "  np24 q8 KV: failed to load"
    ;;
  q8)
    step q8
    mkdir -p $O/qvac-q8
    for m in cold_weights warm; do
      OUTSUB=qvac-q8 GGUF=$Q8 bash /root/h2h/boot_bg.sh qvac "q8-boot-$m" "$m" > /dev/null 2>&1
    done
    tail -2 $O/qvac-q8/boot.jsonl | python3 -c "
import json,sys
for l in sys.stdin:
    r=json.loads(l); print('  ', r['name'], r.get('t_health_s'), r.get('gpu_used_mib_after_load'), r.get('error'))"
    bench $O/qvac-q8 smoke A_warm_short A_cold_short
    ;;
  reasoning)
    step reasoning
    GGUF=$Q4 up q4km-reason-off --reasoning off && bench $O/qvac-q4km-reasoning-off R5
    ;;
  quality)
    step quality
    for v in q4km q8; do
      g=$Q4; [ $v = q8 ] && g=$Q8
      GGUF=$g up $v-quality || continue
      python3 $REPO/scripts/h2h_quality.py run --label qvac-$v --api-key "$API_KEY" \
        -o $O/qvac-$v/quality.jsonl > /dev/null 2>&1
    done
    python3 $REPO/scripts/h2h_quality.py compare $O/vllm/quality.jsonl $O/qvac-q4km/quality.jsonl $O/qvac-q8/quality.jsonl | sed 's/^/  /'
    ;;
  gateway)
    step gateway
    GGUF=$Q4 up q4km-gw || continue
    bash /root/h2h/gateway.sh stop; sleep 1
    bash /root/h2h/gateway.sh start qvac > /dev/null 2>&1 < /dev/null; sleep 4
    echo "  gw readyz: $(curl -s localhost:8080/readyz | cut -c1-200)"
    bash /root/h2h/gw_bench.sh $O/qvac-q4km
    tail -6 $O/qvac-q4km/G_gateway.out | sed 's/^/  /'
    ;;
  failure)
    step failure
    GGUF=$Q4 bash $REPO/scripts/h2h_failure.sh qvac q4km-fail $O/qvac-q4km > /dev/null 2>&1
    echo "  failure probes done"
    ;;
  obs)
    # Observability + log hygiene: metrics series, /slots, and whether prompt
    # text reaches disk under -v / --log-file.
    step obs
    GGUF=$Q4 up q4km-verbose -v --log-file /root/h2h/logs/qvac_verbose_file.log || continue
    marker="CANARY$(openssl rand -hex 6)"
    curl -s -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
      -d "{\"model\":\"qwen3-8b\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"$marker say ok\"}]}" \
      localhost:8000/v1/chat/completions > /dev/null
    sleep 1
    echo "  canary in -v stdout log: $(grep -c "$marker" /root/h2h/logs/q4km-verbose.log)"
    echo "  canary in --log-file: $(grep -c "$marker" /root/h2h/logs/qvac_verbose_file.log 2>/dev/null || echo n/a)"
    GGUF=$Q4 up q4km-default || continue
    curl -s -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
      -d "{\"model\":\"qwen3-8b\",\"max_tokens\":8,\"messages\":[{\"role\":\"user\",\"content\":\"$marker default\"}]}" \
      localhost:8000/v1/chat/completions > /dev/null
    sleep 1
    echo "  canary in default log: $(grep -c "$marker" /root/h2h/logs/q4km-default.log)"
    curl -s -H "Authorization: Bearer $API_KEY" localhost:8000/metrics > $O/qvac-q4km/metrics_series.txt
    curl -s -H "Authorization: Bearer $API_KEY" localhost:8000/slots > $O/qvac-q4km/slots.json
    echo "  metrics series: $(grep -c '^# TYPE' $O/qvac-q4km/metrics_series.txt); slot prompt text in /slots: $(grep -c "$marker" $O/qvac-q4km/slots.json)"
    echo "  /metrics without key: $(curl -s -o /dev/null -w %{http_code} localhost:8000/metrics)  /slots without key: $(curl -s -o /dev/null -w %{http_code} localhost:8000/slots)"
    # Native TLS
    openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
      -keyout /root/h2h/tls.key -out /root/h2h/tls.crt > /dev/null 2>&1
    PORT=8443 GGUF=$Q4 bash $REPO/scripts/h2h_serve.sh start qvac q4km-tls \
      --ssl-key-file /root/h2h/tls.key --ssl-cert-file /root/h2h/tls.crt > /dev/null
    sleep 8
    echo "  TLS /health: $(curl -sk -o /dev/null -w %{http_code} https://localhost:8443/health)  plain http on TLS port: $(curl -s -o /dev/null -w %{http_code} http://localhost:8443/health)"
    bash $REPO/scripts/h2h_serve.sh stop > /dev/null
    ;;
esac
done
step ALL_DONE
