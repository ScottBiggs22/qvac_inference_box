#!/usr/bin/env bash
# Q8_0 rerun with a Q4_K_M anchor, on a fresh A10 box (vllm/vllm-openai:v0.28.0
# image for the identical CUDA 13.0 toolchain). One unattended pass:
#   setup: apt deps, clone + build the pinned QVAC, download both GGUFs
#   Q4_K_M anchor: smoke, A_warm_short, A_cold_short  (default config, as the
#     first box's Q4_K_M runs) -- compared against the first box to expose
#     card-to-card variance before anything is said about Q8_0
#   Q8_0: boots (warm / cold_weights), smoke, A_warm_short, A_cold_short,
#     A_warm_short with -cram 0, quality
# Every engine launch is verified against the GGUF it was asked for (the bug
# that invalidated the first Q8_0 pass), and aborts the step if they differ.
set -uo pipefail
H2H=/root/h2h; REPO=/root/repo; O=$H2H/out
Q4=/root/models/Qwen3-8B-Q4_K_M.gguf; Q8=/root/models/Qwen3-8B-Q8_0.gguf
mkdir -p $H2H/run $H2H/logs $O /root/models
step() { echo "STEP $* $(date -u +%T)"; }
. $H2H/env.sh

step setup
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq cmake build-essential libcurl4-openssl-dev time > /dev/null
( cd /root/models
  for f in Qwen3-8B-Q4_K_M.gguf Qwen3-8B-Q8_0.gguf; do
    hf download Qwen/Qwen3-8B-GGUF "$f" --revision 7c41481f57cb95916b40956ab2f0b139b296d974 --local-dir /root/models > /dev/null 2>&1
  done
  sha256sum *.gguf ) > $O/gguf_sha256.txt 2>&1 &
dl=$!
git clone -q https://github.com/tetherto/qvac-fabric-llm.cpp /root/qvac && cd /root/qvac \
  && git checkout -q 7bb2161c690befa1bb44ff9b20e4eea2da6c22b8 \
  && cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DCMAKE_BUILD_TYPE=Release > $H2H/logs/configure.log 2>&1 \
  && /usr/bin/time -v cmake --build build --config Release -j "$(nproc)" --target llama-server > $H2H/logs/build.log 2>&1
echo "  build rc=$? $(/root/qvac/build/bin/llama-server --version 2>&1 | head -1)"
wait $dl
cat $O/gguf_sha256.txt | sed 's/^/  /'
grep -q d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785 $O/gguf_sha256.txt || { echo "  Q4_K_M sha mismatch"; exit 1; }
grep -q 408b955510e196121c1c375201744783b5c9a43c7956d73fc78df54c66e883d6 $O/gguf_sha256.txt || { echo "  Q8_0 sha mismatch"; exit 1; }

up() {  # up GGUF NAME [flags]
  local g=$1 n=$2; shift 2
  GGUF=$g bash $REPO/scripts/h2h_serve.sh start qvac "$n" "$@" > /dev/null
  for _ in $(seq 1 600); do curl -sf localhost:8000/health > /dev/null && break; sleep 0.2; done
  curl -sf localhost:8000/health > /dev/null || { echo "  $n did not come up"; return 1; }
  grep -q -- "-m $g" $H2H/run/$n.cmd || { echo "  $n: WRONG MODEL $(grep -o -- '-m [^ ]*' $H2H/run/$n.cmd)"; return 1; }
  echo "  $n up on $(basename $g): $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
}
bench() { local out=$1; shift; bash $REPO/scripts/h2h_bench.sh "$out" "$@" > /dev/null 2>&1; echo "  bench $(basename $out) $*: done"; }

step anchor_q4km
up $Q4 q4km-anchor && bench $O/qvac-q4km-anchor smoke A_warm_short A_cold_short

step q8
mkdir -p $O/qvac-q8
cat > $H2H/boot_bg.sh <<'EOS'
#!/usr/bin/env bash
# usage: boot_bg.sh ENGINE NAME MODE [-- engine flags]; GGUF / OUTSUB from env
cd /root/repo
python3 scripts/h2h_boot_timer.py "$1" "$2" --mode "$3" -o "/root/h2h/out/${OUTSUB:-$1}/boot.jsonl" --timeout 1200 "${@:4}"
echo "BOOT_EXIT $?"
EOS
for m in cold_weights warm cold_weights warm; do
  OUTSUB=qvac-q8 GGUF=$Q8 bash $H2H/boot_bg.sh qvac "q8-boot-$m" "$m" > /dev/null 2>&1
done
grep -o -- "-m [^ ]*" $H2H/run/q8-boot-*.cmd | sed 's/^/  /'
up $Q8 q8-main && bench $O/qvac-q8 smoke A_warm_short A_cold_short
up $Q8 q8-cram0 -cram 0 && bench $O/qvac-q8-cram0 A_warm_short
up $Q8 q8-quality && python3 $REPO/scripts/h2h_quality.py run --label qvac-q8 --api-key "$API_KEY" \
  -o $O/qvac-q8/quality.jsonl > /dev/null 2>&1 && echo "  quality done"
bash $REPO/scripts/h2h_serve.sh stop > /dev/null
step ALL_DONE
