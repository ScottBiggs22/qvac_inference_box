#!/usr/bin/env bash
# QVAC fixes session (docs/QVAC_FIXES_LAUNCH_PROMPT.md, Part A) on a rented box.
# Run from a FILE on the box, under nohup (docs/VAST_AI_OPERATIONS.md §7):
#
#   bash scripts/qvac_fixes_box.sh setup     apt, pinned build, Q4_K_M weights, env.sh
#   bash scripts/qvac_fixes_box.sh matrix    --reasoning-format x --reasoning, json matrix
#   bash scripts/qvac_fixes_box.sh prod RF   quality + b3 + loadgen under RF, and a
#                                            `none` loadgen baseline on the same card
#
# Output lands in /root/h2h/out/qvac-fixes/; pull that directory back.
set -uo pipefail
H2H=/root/h2h; REPO=/root/repo; O=$H2H/out/qvac-fixes
Q4=/root/models/Qwen3-8B-Q4_K_M.gguf
mkdir -p $H2H/run $H2H/logs $O /root/models
step() { echo "STEP $* $(date -u +%T)"; }

up() {  # up NAME [flags]; REASONING_FORMAT from env
  local n=$1; shift
  GGUF=$Q4 bash $REPO/scripts/h2h_serve.sh start qvac "$n" "$@" > /dev/null
  for _ in $(seq 1 600); do curl -sf localhost:8000/health > /dev/null && break; sleep 0.2; done
  curl -sf localhost:8000/health > /dev/null || { echo "  $n did not come up"; tail -5 $H2H/logs/$n.log; return 1; }
  echo "  $n up: $(cat $H2H/run/$n.cmd)"
}

case "${1:-}" in
  setup)
    step setup
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq cmake build-essential git \
      libcurl4-openssl-dev python3-pip curl time > /dev/null
    pip install -q httpx fastapi pydantic 'huggingface_hub[cli]' > /dev/null 2>&1
    [ -f $H2H/env.sh ] || echo "API_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(24))')" > $H2H/env.sh
    ( hf download Qwen/Qwen3-8B-GGUF Qwen3-8B-Q4_K_M.gguf \
        --revision 7c41481f57cb95916b40956ab2f0b139b296d974 --local-dir /root/models > $H2H/logs/dl.log 2>&1
      sha256sum $Q4 > $O/gguf_sha256.txt ) &
    dl=$!
    [ -d /root/qvac ] || git clone -q https://github.com/tetherto/qvac-fabric-llm.cpp /root/qvac
    cd /root/qvac && git checkout -q 7bb2161c690befa1bb44ff9b20e4eea2da6c22b8 \
      && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release > $H2H/logs/configure.log 2>&1 \
      && /usr/bin/time -v cmake --build build --config Release -j "$(nproc)" --target llama-server > $H2H/logs/build.log 2>&1
    echo "  build rc=$? $(/root/qvac/build/bin/llama-server --version 2>&1 | grep -i version)"
    wait $dl
    cat $O/gguf_sha256.txt
    grep -q d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785 $O/gguf_sha256.txt \
      && echo "  Q4_K_M sha ok" || echo "  Q4_K_M sha MISMATCH"
    step SETUP_DONE
    ;;
  matrix)
    . $H2H/env.sh
    rm -f $O/matrix.jsonl
    for rf in none auto deepseek; do
      for rea in auto off; do
        n=m-rf-$rf-rea-$rea
        step "$n"
        REASONING_FORMAT=$rf up "$n" -cram 0 --reasoning "$rea" || continue
        python3 $REPO/scripts/qvac_json_matrix.py --label "rf=$rf rea=$rea" \
          --api-key "$API_KEY" -o $O/matrix.jsonl > $O/$n.out 2>&1
        cp $H2H/logs/$n.log $O/$n.server.log
      done
    done
    bash $REPO/scripts/h2h_serve.sh stop > /dev/null
    step MATRIX_DONE
    ;;
  prod)
    . $H2H/env.sh
    rf=${2:?reasoning format}
    step "prod rf=$rf"
    REASONING_FORMAT=$rf up prod-$rf -cram 0 || exit 1
    python3 $REPO/scripts/h2h_quality.py run --label "qvac-q4km-rf-$rf" --api-key "$API_KEY" \
      -o $O/quality-rf-$rf.jsonl > $O/quality-rf-$rf.out 2>&1
    python3 $REPO/scripts/b3_probe.py --url http://127.0.0.1:8000/v1 --direct --model qwen3-8b \
      --api-key "$API_KEY" -o $O/b3-rf-$rf.jsonl > $O/b3-rf-$rf.out 2>&1
    bash $REPO/scripts/h2h_bench.sh $O/load-rf-$rf A_warm_short A_cold_short > /dev/null 2>&1
    if [ "$rf" != none ]; then
      step "baseline rf=none (same card, new loadgen)"
      REASONING_FORMAT=none up prod-none -cram 0 || exit 1
      bash $REPO/scripts/h2h_bench.sh $O/load-rf-none A_warm_short A_cold_short > /dev/null 2>&1
    fi
    cp $H2H/run/*.cmd $O/ 2>/dev/null
    bash $REPO/scripts/h2h_serve.sh stop > /dev/null
    step PROD_DONE
    ;;
  *) echo "usage: $0 setup | matrix | prod RF" >&2; exit 2 ;;
esac
