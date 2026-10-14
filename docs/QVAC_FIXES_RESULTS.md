# QVAC Fabric fixes: JSON/thinking, TLS alignment, `qvac serve`

**Date:** 2026-10-13/14
**Plan:** `docs/QVAC_FIXES_LAUNCH_PROMPT.md`, amended before launch. The amendments are
recorded at the end of this doc.
**Raw output:** `results/qvac-fixes/`

**Card:** RTX 3090, 24 GiB, 350 W, driver 590.48.01, EPYC 7532, cgroup cap 12.3 CPUs. No A10
was listed on vast.ai. Part A is a correctness test, so the card does not matter for it.
The loadgen numbers are **3090 numbers** and compare only with each other.

**Build:** `tetherto/qvac-fabric-llm.cpp` @ `7bb2161c6` (build 9240), the same pin as the
head-to-head, `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86`.

**Model:** `Qwen/Qwen3-8B-GGUF` @ `7c41481f`, Q4_K_M, sha256 `d98cdcbd…5785` (verified).

## 1. Verdict

| Question | Answer |
|---|---|
| Did we use QVAC's parallelism correctly? | **Yes.** We ran `llama-server` slots with continuous batching. QVAC's own `config.parallel` belongs to `qvac serve`, a different server, and that one cannot even use CUDA on Linux (§5). |
| Why did `json_schema` return 400? | **Self-inflicted.** Our `--reasoning-format none` combined with an empty-think prefill makes the grammar sampler fail to initialise. **H1 confirmed**, so H2 and H3 were not needed. |
| Fix | Drop `--reasoning-format none`; the default is `auto`. **json_schema 5/5, tools 5/5** on Q4_K_M under production flags. |
| Empty `<think></think>` wrapper | **Also caused by `none`.** Under `auto` it is gone. Raw exact-text agreement with vLLM went from 0/40 to **32/40**. |
| Throughput cost of the fix | **None.** Aggregate tok/s matched within 1% warm. TTFT now includes ~3 parser-held tokens (+28 ms at 1 user). |
| TLS/mTLS | Matches the sister repo: gateway-side wiring only, with identical code. Docs corrected. Sidecar designed in `QVAC_FORK.md` item 6, not built. |
| `qvac serve` | **Not measurable here.** On Linux it has no CUDA backend (Vulkan, HIP or CPU only), and the NVIDIA Vulkan driver would not initialise in vast.ai containers. Nothing to adopt (§5). |
| Tuning sweep (Part E) | **Not run.** No A10 was listed, and only an A10 result could change the R1 verdict. |

QVAC still fails R1 with a cold prefix on the A10 (`HEAD_TO_HEAD.md`). Nothing here changes that.

## 2. JSON / thinking matrix (Part A.1)

Each cell lists json_schema results for strict, non-strict and the trivial schema, in that order.
All requests ran at temperature 0, with the server at `-np 8 -c 65536 -fa on -cram 0`.

| `--reasoning-format` | `--reasoning` | `enable_thinking` absent | `false` | `true` |
|---|---|---|---|---|
| none | auto | 200 ✓ ✓ ✓ | **400 400 400** | 200 ✓ ✓ ✓ |
| none | off | **400 400 400** | **400 400 400** | 200 ✓ ✓ ✓ |
| auto | auto | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ |
| auto | off | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ |
| deepseek | auto | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ |
| deepseek | off | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ | 200 ✓ ✓ ✓ |

✓ means the output parsed and was valid against the schema.

**Server log** (`m-rf-none-*.server.log`): `common_sampler_init: error initializing grammar sampler`,
then `Failed to initialize samplers: std::exception`.

**Trigger:** the 400 fires exactly when `none` meets a prefilled empty think block. That prefill
comes from either `enable_thinking:false` or `--reasoning off`. The head-to-head's quality test
sent `enable_thinking:false`, hence 0/5.

Other findings from the matrix (`results/qvac-fixes/matrix.jsonl`, 108 requests):
- **Tool calls:** 18/18 correct across every row.
- **`json_object` is only enforced while the model is thinking.** With thinking off, under every
  format, it returns fenced markdown (```` ```json … ``` ````), which is not valid JSON. With
  thinking on under `auto`, it parses. **Use `json_schema`.**
- **`--reasoning off` does not override a request.** A per-request `enable_thinking:true` still
  thinks (596 reasoning chars).
- **Thinking under `auto`:** think text arrives in `reasoning_content` and `content` holds only
  the answer. `enable_thinking:false` yields no reasoning and no wrapper.
- **`deepseek` behaves identically to `auto`** for this template.

## 3. Production flags (Part A.2)

Production keeps the head-to-head flags but drops `--reasoning-format none`:
`-m … -c 65536 -np 8 -ngl 99 -fa on -cram 0 --metrics --alias qwen3-8b --api-key …`

The change is applied in `deploy/k8s/probes.yaml`, README and `scripts/h2h_serve.sh` (new
`REASONING_FORMAT` knob, default `auto`).

**Quality** (`quality-rf-auto.jsonl`; vLLM column from `results/head-to-head-a10/vllm/`):

| Test | vLLM AWQ (A10) | QVAC Q4_K_M, `auto` (3090) | QVAC Q4_K_M, `none` (H2H, A10) |
|---|---|---|---|
| Answers (40) | 31/40 | **32/40** | 32/40 |
| json_schema (5) | 5/5 | **5/5** | 0/5 |
| Tools (5) | 5/5 | **5/5** | 5/5 |
| Exact-text agreement with vLLM, raw | — | **32/40** | 0/40 (30/40 stripped) |

**Loadgen, same 3090, same new loadgen, `auto` vs `none`** (7000+512, `A_warm_short` /
`A_cold_short`, 6 requests per worker, 1 repeat):

| | users | per-user tok/s | aggregate | TTFT p50 |
|---|---|---|---|---|
| warm, auto | 1 / 5 / 8 | 114.9 / 47.7 / 31.2 | 112.2 / 233.1 / 246.4 | 113 / 182 / 259 ms |
| warm, none | 1 / 5 / 8 | 114.0 / 46.8 / 31.1 | 112.0 / 232.6 / 246.0 | 85 / 136 / 176 ms |
| cold, auto | 1 / 5 / 8 | 114.1 / 29.1 / 20.3 | 82.5 / 118.0 / 137.8 | 1725 / 3979 / 4562 ms |
| cold, none | 1 / 5 / 8 | 113.3 / 26.1 / 19.5 | 82.4 / 112.0 / 137.8 | 1703 / 2982 / 3584 ms |

- **No throughput regression.** Aggregate and decode rates match. The cold-5 per-user gap
  (29.1 vs 26.1) is within the H2H's cold spread (±1–5) for a single repeat.
- **TTFT definition changed** (`scripts/loadgen.py`). TTFT is now the first delta carrying
  `content` **or** `reasoning_content`. Under `auto`, the first reasoning delta arrives about
  3 tokens later than the raw `<think>` text did under `none`, because the parser holds the
  tag. That is +28 ms at 1 user warm.
- **Unexplained:** the cold TTFT p50 gap at 5 and 8 users (+1.0 s) has identical p95 and comes
  from one repeat. It is probably queue-order noise, but it is not proven. Re-measure on the A10
  with `--repeats 3` if TTFT under cold load matters.
- loadgen now records `reasoning_frames` alongside `content_frames`. A stream with neither
  still fails as `empty_completion` (`tests/test_loadgen.py`).

## 4. Thinking contract: b3 vs vLLM (Part A.4)

From `b3_probe.py`, direct, with `auto`. `b3_probe.py` now reads `reasoning_content` and
reports `thought` (real reasoning, not an empty wrapper) and `empty_answer`.

| Case | vLLM: think / empty / finish | QVAC `auto`: thought / empty / finish |
|---|---|---|
| baseline_no_flag | yes / no / stop | yes (1188 chars) / no / stop |
| flag_false | no / no / stop | no / no / stop |
| flag_false_user_types_think | **no** / no / stop | **no** / no / stop |
| flag_false_user_types_no_think | no / no / stop | no / no / stop |
| flag_true_user_types_no_think | **yes** / no / stop | **no** / no / stop |
| flag_absent_tight_budget (160) | yes, unterminated / **YES** / length | yes, in reasoning_content / **YES** / length |
| flag_false_tight_budget (160) | no / no / stop | no / no / stop |

- **The B3 fix holds on QVAC.** `enable_thinking:false` suppresses thinking, and `/think`
  cannot override it.
- **Difference 1:** with `enable_thinking:true`, QVAC lets a user's `/no_think` switch thinking
  off, where vLLM's flag won. It has no operational effect because the app always sends `false`.
- **Difference 2:** the B3 failure has a new shape. Without the flag at 160 tokens, the budget
  is spent in `reasoning_content` and `content` is **empty**, not an unterminated `<think>`. The
  client's `strip_think("")` returns `""`, and `generate()` maps that to degraded exactly as
  before. The client needs no change.
- **No server-side `--chat-template-kwargs` default.** Keep vLLM parity: the client sends the
  flag, and the gateway stays a passthrough.
- **Stub:** `stub/server.py --stub-think --stub-reasoning-format split` emulates the split. The
  passthrough of `reasoning_content` (JSON and SSE) is pinned in `tests/test_qvac_quirks.py`.

## 5. `qvac serve --openai` side test (Part D)

Time-boxed at about 1 h. **No throughput or VRAM number was obtained**, and the reasons are
findings in their own right.

- **No CUDA on Linux.** `@qvac/cli` 0.15.0 runs the LLM through `@qvac/fabric-linux-x64`, which
  ships ggml **CPU, HIP (AMD) and Vulkan** backends only
  (`serve-box2/fabric_backends_linux_x64.txt`). So on NVIDIA, `qvac serve` means **Vulkan**, not
  the CUDA build measured everywhere else in this repo.
- **Vulkan did not come up on vast.ai.**
  - Box 1 had `NVIDIA_DRIVER_CAPABILITIES=compute,utility`. Box 2 was created with `=all`.
  - On both, the NVIDIA ICD failed: `Could not get 'vkCreateInstance' … for ICD libGLX_nvidia.so.0`
    (`serve-box2/vulkan_loader_errors.txt`). Only `llvmpipe` (CPU) enumerated.
  - On k8s, `qvac serve` would need the graphics driver capability, which the CUDA path does not.
- **Model distribution is peer-to-peer.**
  - `QWEN3_8B_INST_Q4_K_M` resolves to a Hyperdrive core, and the file it fetches is the same
    5,027,783,488 bytes as our pinned GGUF.
  - It arrived at about 3 MB/s, which is roughly 25 min for the model.
  - The config also accepts a filesystem path. The second box could not reach huggingface.co
    (`Network is unreachable`), so that path was not tried.
- **Docs-only facts (unmeasured):** no health endpoint, and `config.parallel` defaults to 1 with
  an FCFS queue. Chat prefixes are cached on disk under `~/.qvac/kv-cache` (4 GiB LRU, 24 h TTL).
  That last one is a PRD §5.4 retention question to resolve before any adoption. It was not
  measured, because no request was served.
- **Verdict:** there is nothing to adopt. `llama-server` with CUDA remains the deployable form of
  QVAC Fabric. A real `qvac serve` measurement needs a host whose NVIDIA Vulkan driver works:
  bare metal or a properly provisioned k8s node. That is not worth the effort unless management
  insists on the npm server specifically.

## 6. TLS / mTLS (Part B)

There is no code change; the wiring is identical to the sister repo's.
- `_upstream_ssl_context` is byte-identical, and `tests/test_mtls.py` test code is identical.
- `pytest tests/test_mtls.py tests/test_config.py` gives 27 passed.

Stale claims corrected:
- "QVAC documents no TLS / no /health / no /metrics" in `gateway/upstream/client.py`, `wedge.py`
  and `config.py`.
- The same claims in `.env.example`, `tests/test_mtls.py`, `tests/conftest.py`, `stub/server.py`,
  README, `docs/PROBE_CONTRACT.md` and `docs/QVAC_FORK.md`.

The sidecar design is in `docs/QVAC_FORK.md` item 6:
- nginx/envoy with `ssl_verify_client on` in front of `127.0.0.1:8000`, with SSE buffering off;
- kubelet probes on a plain `/health`-only side port;
- certs as a mounted Secret, rotation owned by DevOps.

An OpenSSL build (Part B.5) was not attempted.

## 7. Amendments to the launch prompt, and corrections

1. **`--reasoning-format` default:** it is `auto`, not `deepseek`, per this build's `--help`.
2. **The H2H's `--reasoning off` check also ran under `none`,** so it never tested the default
   format (`logs/cmds/q4km-reason-off.cmd`).
3. **The prompt's verification grep could never come back empty.** It matched the prompt itself,
   and it missed upper-case "NO TLS", backticked "no \`/health\`" and "documents neither". It was
   replaced by a case-insensitive grep over code, config and live docs.
4. **Citations:**
   - 43.2 comes from `HEAD_TO_HEAD.md` §1/§4; §13 #1 says "~43".
   - Cold@5 is 15.9 under the default config and 15.7 under `-cram 0`.
   - §11 has no JSON item.
   - The sister gateway also clamps `max_tokens` and forces `include_usage`.
5. **Box packages:** `loadgen.py` imports the stub, so a non-vLLM box needs `fastapi`. The first
   prod pass's loadgen failed on this and was re-run. `qvac_fixes_box.sh` now installs it.
6. **Spend:** the first box sat idle for about 2.9 h after setup while the session was stalled,
   which cost about $0.64 of the total.

## 8. Spend

About **$1.06**, assuming the mid-session top-up was exactly $5.00: credit went from $1.392
to $0.610 before it, and from about $6.39 to $5.337 after.

| Item | Rate | Notes |
|---|---|---|
| Box 1 | $0.22/hr | Contract 55850360, 3090, machine 41710. Ran Parts A and D install. About 3.4 h, including about 2.9 h idle while the session was stalled. |
| Box 2 | $0.19/hr | Contract 55875137, 3090, machine 64274. Vulkan/Part D attempt, about 40 min. |

**Teardown:**
- Both instances are destroyed. `show instances` and `show volumes` return `[]`.
- The throwaway SSH key is deleted from the account and from `~/.ssh`.
- Artifacts were scanned before teardown: no API key, HF token or private key.
