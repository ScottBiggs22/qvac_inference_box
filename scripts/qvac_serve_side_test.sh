#!/usr/bin/env bash
# Part D (docs/QVAC_FIXES_LAUNCH_PROMPT.md): a time-boxed look at `qvac serve
# --openai` (npm @qvac/cli), QVAC's OWN server, as opposed to the fork's
# llama-server that every other measurement in this repo uses.
#
#   bash scripts/qvac_serve_side_test.sh install        node + @qvac/cli, list Qwen3 constants
#   bash scripts/qvac_serve_side_test.sh run CONST N     serve CONST with config.parallel=N,
#                                                       loadgen 7000+512 at 1 and N users
#
# Output: /root/h2h/out/qvac-fixes/serve/. Run from a file, under nohup.
set -uo pipefail
H2H=/root/h2h; REPO=/root/repo; O=$H2H/out/qvac-fixes/serve; W=/root/qserve
mkdir -p $O $W
. $H2H/env.sh
step() { echo "STEP $* $(date -u +%T)"; }

case "${1:-}" in
  install)
    step install
    export DEBIAN_FRONTEND=noninteractive
    if ! command -v node > /dev/null || [ "$(node -v | cut -c2- | cut -d. -f1)" -lt 20 ]; then
      curl -fsSL https://deb.nodesource.com/setup_22.x | bash - > /dev/null 2>&1
      apt-get install -y -qq nodejs > /dev/null 2>&1
    fi
    apt-get install -y -qq libvulkan1 vulkan-tools mesa-vulkan-drivers > /dev/null 2>&1
    echo "  node $(node -v) npm $(npm -v)"
    npm install -g @qvac/cli > $O/npm_install.log 2>&1; echo "  npm rc=$?"
    qvac --version 2>&1 | head -2
    qvac serve --help > $O/serve_help.txt 2>&1
    vulkaninfo --summary > $O/vulkaninfo.txt 2>&1; echo "  vulkaninfo rc=$? $(grep -m2 deviceName $O/vulkaninfo.txt)"
    root=$(npm root -g)
    ls $root/@qvac > $O/qvac_pkgs.txt 2>&1
    find $root/@qvac -path '*prebuilds*' -maxdepth 6 2>/dev/null | head -20 > $O/prebuilds.txt
    grep -rhoE '"?QWEN3_[A-Z0-9_]+"?' $root/@qvac 2>/dev/null | tr -d '"' | sort -u > $O/qwen3_constants.txt
    cat $O/qwen3_constants.txt
    step INSTALL_DONE
    ;;
  run)
    const=${2:?SDK constant}; n=${3:?parallel}
    tag=p$n
    step "run $const parallel=$n"
    cat > $W/qvac.config.json <<EOF
{ "serve": { "models": { "qwen3-8b": {
  "model": "$const", "default": true, "preload": true,
  "config": { "ctx_size": 8192, "parallel": $n } } } } }
EOF
    cp $W/qvac.config.json $O/config-$tag.json
    rm -rf ~/.qvac/kv-cache
    cd $W
    t0=$(date +%s.%N)
    setsid nohup qvac serve --openai -c $W/qvac.config.json --api-key "$API_KEY" \
      > $O/serve-$tag.log 2>&1 < /dev/null &
    pid=$!
    for _ in $(seq 1 900); do
      curl -sf -H "Authorization: Bearer $API_KEY" localhost:11434/v1/models > /dev/null && break
      kill -0 $pid 2>/dev/null || break; sleep 1
    done
    echo "  ready after $(python3 -c "import time;print(round(time.time()-$t0,1))") s; pid alive: $(kill -0 $pid 2>/dev/null && echo yes || echo no)"
    curl -s -o /dev/null -w "  /health -> %{http_code}\n" localhost:11434/health
    curl -s -o /dev/null -w "  /metrics -> %{http_code}\n" localhost:11434/metrics
    nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader | sed 's/^/  gpu after load: /'
    nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | sed 's/^/  gpu app: /'
    nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used --format=csv -l 1 > $O/gpu-$tag.csv 2>/dev/null &
    smi=$!
    python3 -u $REPO/scripts/loadgen.py --url http://127.0.0.1:11434/v1 --direct --api-key "$API_KEY" \
      --model qwen3-8b --model-len 8192 --timeout 900 --tag serve-$tag \
      --prompt-tokens ${PROMPT_TOKENS:-7000} --max-tokens ${MAX_TOKENS:-512} --ignore-eos \
      --levels "1,$n" --requests 2 --warmup 1 --warmup-per-level --warmup-concurrency level \
      -o $O/load-$tag.jsonl > $O/load-$tag.out 2>&1
    echo "  loadgen rc=$?"; tail -8 $O/load-$tag.out
    kill $smi 2>/dev/null
    sort -t, -k3 -n $O/gpu-$tag.csv | tail -1 | sed 's/^/  peak gpu row: /'
    # PRD §5.4: does prompt text land on disk?
    du -sh ~/.qvac/kv-cache 2>/dev/null | sed 's/^/  kv-cache size: /'
    find ~/.qvac/kv-cache -type f 2>/dev/null | head -5 > $O/kvcache_files-$tag.txt
    n_hits=$(grep -rlaF "Summarise position" ~/.qvac/kv-cache 2>/dev/null | wc -l)
    echo "  kv-cache files: $(find ~/.qvac/kv-cache -type f 2>/dev/null | wc -l), files containing plaintext prompt text: $n_hits"
    kill -TERM -- -$pid 2>/dev/null || kill $pid; sleep 3; kill -KILL -- -$pid 2>/dev/null
    step RUN_DONE
    ;;
  *) echo "usage: $0 install | run CONST N" >&2; exit 2 ;;
esac
