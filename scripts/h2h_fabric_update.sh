#!/usr/bin/env bash
# Current QVAC Fabric (v11739.4.0, = npm @qvac/fabric-linux-x64 0.31.0) against
# the build every QVAC number in this repo came from (7bb2161, build 9240), on
# one A10 in the vllm/vllm-openai:v0.28.0 image (CUDA 13.0 nvcc).
# Plan: ~/.claude/plans/lets-rerun-a-limited-federated-cocke.md; write-up in
# docs/FABRIC_UPDATE_A10.md. Modelled on h2h_q8_rerun.sh.
#
# Run from a FILE on the box, under setsid nohup (docs/VAST_AI_OPERATIONS.md §7):
#   bash scripts/h2h_fabric_update.sh setup   apt, both builds, Q4_K_M, env.sh
#   bash scripts/h2h_fabric_update.sh run     the ABBA matrix + quality
# Split in two so a failed new-tree build is seen before paid bench time.
#
# The new build mirrors the npm module's CUDA options
# (tetherto/qvac-registry-vcpkg ports/qvac-fabric/portfile.cmake): graphs, FA,
# NVRTC elementwise fusion. Both trees build without the embedded web UI
# (LLAMA_BUILD_UI=OFF and LLAMA_USE_PREBUILT_UI=OFF; the second is what stops
# the download). The prebuilt-UI download now fails on both tags, and a partial
# download aborts the build. In October it failed without aborting. The UI has
# no bearing on inference. Arch is 86 only (the npm module's A10 cubin is
# 86-real too). Every launch is checked for both the GGUF and the binary.
set -uo pipefail
H2H=/root/h2h; REPO=/root/repo; O=$H2H/out
Q4=/root/models/Qwen3-8B-Q4_K_M.gguf
OLD_SHA=7bb2161c690befa1bb44ff9b20e4eea2da6c22b8
NEW_SHA=2aa8dbece6895c237a1deb19aef0dfc05e3a19df   # tag v11739.4.0
OLD_BIN=/root/qvac-old/build/bin/llama-server
NEW_BIN=/root/qvac-new/build/bin/llama-server
mkdir -p $H2H/run $H2H/logs $O /root/models
step() { echo "STEP $* $(date -u +%T)"; }

build() {  # build DIR SHA [extra cmake flags]
  local d=$1 sha=$2 n; n=$(basename "$d"); shift 2
  [ -d "$d" ] || git clone -q https://github.com/tetherto/qvac-fabric-llm.cpp "$d"
  ( cd "$d" && git fetch -q origin "$sha" 2>/dev/null; git checkout -q "$sha" && rm -rf build \
    && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF "$@" \
         > $H2H/logs/configure-$n.log 2>&1 \
    && /usr/bin/time -v cmake --build build --config Release -j "$(nproc)" --target llama-server \
         > $H2H/logs/build-$n.log 2>&1 )
  local rc=$?
  echo "  $n build rc=$rc"
  { echo "== $n @ $sha  flags: -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF $*"
    "$d/build/bin/llama-server" --version 2>&1 | head -3
    grep -E 'Elapsed \(wall' $H2H/logs/build-$n.log; } >> $O/build_provenance.txt
  return $rc
}

up() {  # up BIN NAME [flags]; extra env for the engine is inherited
  local b=$1 n=$2; shift 2
  QVAC_BIN=$b GGUF=$Q4 bash $REPO/scripts/h2h_serve.sh start qvac "$n" "$@" > /dev/null
  for _ in $(seq 1 900); do curl -sf localhost:8000/health > /dev/null && break; sleep 0.2; done
  curl -sf localhost:8000/health > /dev/null || { echo "  $n did not come up"; tail -5 $H2H/logs/$n.log; return 1; }
  grep -q -- "-m $Q4" $H2H/run/$n.cmd || { echo "  $n: WRONG MODEL"; return 1; }
  grep -q -- "^$b " $H2H/run/$n.cmd || { echo "  $n: WRONG BINARY $(cut -d' ' -f1 $H2H/run/$n.cmd)"; return 1; }
  echo "  $n up ($(basename $(dirname $(dirname $(dirname $b))))): $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
}
bench() { local out=$1; shift; bash $REPO/scripts/h2h_bench.sh "$out" "$@" > /dev/null 2>&1; echo "  bench $(basename $out) $*: done"; }

case "${1:-}" in
  setup)
    step setup
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq cmake build-essential git libcurl4-openssl-dev curl time > /dev/null
    # The image ships libnvrtc.so.13 but not its header or unversioned .so, so
    # CMake finds no CUDA::nvrtc and NVRTC fusion cannot configure.
    apt-get install -y -qq cuda-nvrtc-dev-13-0=13.0.88-1 > /dev/null
    [ -f $H2H/env.sh ] || echo "API_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(24))')" > $H2H/env.sh
    ( hf download Qwen/Qwen3-8B-GGUF Qwen3-8B-Q4_K_M.gguf \
        --revision 7c41481f57cb95916b40956ab2f0b139b296d974 --local-dir /root/models > $H2H/logs/dl.log 2>&1
      sha256sum $Q4 > $O/gguf_sha256.txt ) &
    dl=$!
    nvcc --version 2>&1 | tail -2 > $O/nvcc_version.txt
    build /root/qvac-old $OLD_SHA
    build /root/qvac-new $NEW_SHA -DGGML_CUDA_GRAPHS=ON -DGGML_CUDA_FA=ON -DGGML_CUDA_NVRTC_FUSION=ON
    wait $dl
    sed 's/^/  /' $O/gguf_sha256.txt
    grep -q d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785 $O/gguf_sha256.txt \
      && echo "  Q4_K_M sha ok" || echo "  Q4_K_M sha MISMATCH"
    step SETUP_DONE
    ;;
  run)
    . $H2H/env.sh
    [ -x $OLD_BIN ] && [ -x $NEW_BIN ] || { echo "missing binary"; exit 1; }
    step "1 old A"
    up $OLD_BIN old-a -cram 0 && bench $O/old-cram0-a smoke A_warm_short A_cold_short
    step "2 new A"
    up $NEW_BIN new-a -cram 0 && bench $O/new-cram0-a smoke A_warm_short A_cold_short
    step "3 new B"
    up $NEW_BIN new-b -cram 0 && bench $O/new-cram0-b A_warm_short A_cold_short
    step "4 old B"
    up $OLD_BIN old-b -cram 0 && bench $O/old-cram0-b A_warm_short A_cold_short
    step "5 new, NVRTC fusion off"
    GGML_CUDA_NVRTC_FUSION=0 up $NEW_BIN new-nofusion -cram 0 && bench $O/new-cram0-nofusion A_warm_short
    step "6 new, default flags"
    up $NEW_BIN new-default && bench $O/new-default A_warm_short
    step "7 new, quality"
    up $NEW_BIN new-quality && python3 $REPO/scripts/h2h_quality.py run --label qvac-new --api-key "$API_KEY" \
      -o $O/new-quality.jsonl > $H2H/logs/quality.log 2>&1 && echo "  quality done"
    bash $REPO/scripts/h2h_serve.sh stop > /dev/null
    cp $H2H/run/*.cmd $O/ 2>/dev/null; mkdir -p $O/logs; cp $H2H/logs/*.log $O/logs/ 2>/dev/null
    ls ~/.cache/ggml-nvrtc 2>/dev/null | wc -l | sed 's/^/  nvrtc cached kernels: /'
    step ALL_DONE
    ;;
  *) echo "usage: $0 setup|run" >&2; exit 2 ;;
esac
