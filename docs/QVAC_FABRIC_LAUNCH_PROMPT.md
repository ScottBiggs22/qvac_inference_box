# Launch prompt — first real QVAC Fabric session on a rented A10

**Written:** 2026-09-30
**Purpose:** get QVAC Fabric actually running on a rented vast.ai A10 (24 GiB)
and work through `docs/QVAC_FORK.md`'s verification checklist, the same way
`bkn301-inference`'s Phase 1 session did for vLLM.

Copy the block below into a new Claude Code session started **from this
repo** (`bkn301-qvac-inference`):

```bash
cd "/Users/scottbiggsbkn301aact/Desktop/BKN Code 2/bkn301-qvac-inference"
claude
```

---

I'm standing up QVAC Fabric (Tether's Vulkan/CUDA/Metal llama.cpp fork) as
the inference engine behind this gateway, replacing vLLM. This repo is a
fork of `bkn301-inference` — read `docs/QVAC_FORK.md` first, in full, before
doing anything else. It has the complete disposition of what changed, what's
decided, and the verification checklist this session exists to work through.
Then skim `docs/HANDOFF.md` §8 for how the fork itself was built.

## What's already decided (don't re-derive these)

- **Model/quant:** `Qwen/Qwen3-8B-GGUF`, **Q4_K_M** quant (~5.03 GiB). PRD
  §3.5 already evaluated this exact HF repo and rejected it for vLLM
  specifically; neither reason applies here. `Q8_0` (~8.71 GiB) is the
  documented fallback if Q4_K_M's quality looks worse than AWQ's in practice.
  Full reasoning: `docs/QVAC_FORK.md` checklist item 5a.
- **TLS/mTLS:** deferred for this test. Plain HTTP between the gateway and
  QVAC Fabric on the rented box's private network — matching
  `bkn301-inference`'s own Phase 1 precedent exactly. Don't spend session
  time on this; it's a real pre-production item (checklist item 6), not a
  blocker for measuring capacity and behavior.
- **Hardware:** a single rented A10, 24 GiB, vast.ai — same provider/workflow
  `bkn301-inference` used for its own Phase 1/2 sessions.

## What's genuinely unknown going in — verify, don't assume

**QVAC Fabric has no pinned image and no confirmed install path yet.** Unlike
vLLM (a published `vllm/vllm-openai` Docker image), nobody has confirmed
whether Tether publishes a ready-to-run image for QVAC Fabric or whether this
requires building from source
(`github.com/tetherto/qvac-fabric-llm.cpp`) on the box. **First real task:**
check that repo's README/releases for a Docker image, a prebuilt binary, or
build instructions, and pick the Vulkan or CUDA backend build (CUDA is the
safer first choice on an A10 you already know is NVIDIA — Vulkan is the more
interesting long-term target but adds a variable this first session doesn't
need).

**Every CLI flag in `deploy/k8s/probes.yaml` (`--openai`, `--model=`,
`--ctx-size=`, `--parallel=`) is inferred from documentation, not verified
against a real `--help` output.** This project has already been burned once
by exactly this class of mistake — the PRD named a vLLM flag
(`--disable-log-requests`) in three places that turned out not to exist in
the pinned version and would have failed at startup. Run the real binary's
`--help` (or equivalent) before trusting any flag name in this repo's docs or
manifests, and correct them in place when they're wrong — don't silently work
around a bad flag name.

## Order of work

Follow `docs/QVAC_FORK.md`'s checklist, adapted to what Phase 1 actually
needs first:

1. **Book the box.** `docs/VAST_AI_OPERATIONS.md` has the booking/session
   mechanics (still accurate — provider-agnostic). Its literal
   `vllm/vllm-openai` image references don't apply; everything else
   (disk sizing, `/workspace` vs. `/root` home-directory trap, cost
   tracking, cleanup discipline) does.
2. **Get QVAC Fabric built/installed and the model downloaded.** This is the
   step with no runbook yet — `docs/VAST_AI_RUNBOOK.md` is written for
   `vllm serve` and is explicitly flagged as not-yet-adapted. Expect to write
   the real equivalent as you go, and **update that runbook with what you
   actually did** once it works, the same way Phase 1 did for vLLM.
3. **Box hygiene.** Run `scripts/verify_box.sh` before pulling the model
   weights. It will need real adjustment — it still checks for an
   AWQ-specific VRAM budget and a `vllm/vllm-openai`-shaped `/workspace`
   assumption (both flagged in-place in the script). Fix what's wrong rather
   than skipping the check.
4. **Launch QVAC Fabric's HTTP server**, `--openai` surface, pointed at the
   downloaded Q4_K_M model, `ctx_size` raised well above QVAC's raw 1024
   default (8192 to match the PRD's requirement). Confirm `GET /v1/models`
   and a real `POST /v1/chat/completions` work with `curl` before involving
   the gateway at all.
5. **Point the gateway at it.** `UPSTREAM_URLS=http://127.0.0.1:<port>/v1`,
   `UPSTREAM_API_KEY` matching whatever QVAC's `--api-key` was set to, no
   mTLS env vars (plain HTTP, per the decision above). Confirm `/readyz` goes
   green and a chat completion round-trips through the gateway.
6. **Work the checklist, in priority order:**
   - **Item 5b first.** Load the model with `parallel=2` (or whatever the
     real flag is called) and watch VRAM (`nvidia-smi`). Does the second
     slot cost roughly one KV-cache's worth of memory, or does VRAM usage
     nearly double? This one number determines whether item 5a's capacity
     math (~116k tokens, ~2.9× headroom) is real or badly wrong — measure it
     before trusting anything downstream of it.
   - **Item 1**, cold-start/warm-restart timing — sets
     `deploy/k8s/probes.yaml`'s `startupProbe`.
   - **Item 2**, the freeze test — repeat `docs/PHASE2_L4_RESULTS.md`'s
     `kill -STOP`/`kill -CONT` experiment against the real QVAC process, with
     a genuinely pending chat request in flight. Confirm (or refute) that
     `GET /v1/models` keeps answering through the freeze, and separately
     whether `gateway/upstream/wedge.py`'s dispatch-based signal actually
     fires — this needs the gateway running against the real server, not the
     stub.
   - **Item 4**, real SSE opening-frame shape — capture one real streamed
     response and compare it to `stub/server.py`'s assumed shape.
   - **Item 8**, request/output-log retention — check whether QVAC Fabric's
     launch flags (or its config file) have anything analogous to vLLM's
     `--no-enable-log-requests`, and whether its default behavior retains
     prompt text anywhere on disk.
   - Then `scripts/loadgen.py` at 1/3/5/8 concurrent (adapt `--model-len`,
     `--api-key` as needed) for the actual tok/s/user KPI-gap number
     management asked for — the whole point of this exercise.
7. **Capture everything into `results/qvac-<box-shape>/`** (mirroring
   `results/vllm-baseline/phase1-vastai-a10/`'s structure: boot logs, a raw
   loadgen output, box-verification output, a `box_provenance.txt`), and
   write up `docs/PHASE1_RESULTS_QVAC.md` following
   `docs/PHASE1_RESULTS.md`'s exact structure and level of rigor — numbers
   with their measurement method stated, corrections to this document's
   placeholders marked as corrections, not silently fixed.
8. **Afterward:** replace every "unverified placeholder" flag this fork
   added (`deploy/k8s/probes.yaml`'s timings, `docs/QVAC_FORK.md`'s
   checklist, `README.md`'s "Running QVAC Fabric" section) with the real
   numbers, and check off what's been measured.

## Hard constraints (carried over, unchanged)

- **A rented box is untrusted.** Synthetic prompts only, throwaway keys
  only. Use `scripts/pack_for_box.sh` for any transfer; never `scp -r`/`rsync`
  of a working tree.
- **No push or merge without explicit approval.** Local commits in this repo
  are fine and expected.
- **Report failures as failures.** If the capacity math doesn't hold up, if
  QVAC's concurrency model turns out worse than hoped, if the freeze test
  doesn't reproduce — write that down plainly. The entire point of this fork
  is measuring the real gap, not producing a reassuring number.
- **Track cost and clean up.** Destroy the instance and remove the SSH key
  when done, same discipline as every prior session on this project.

## One thing worth doing deliberately

**Tell me when something in `docs/QVAC_FORK.md` or this prompt doesn't
survive contact with the real binary.** Every flag name, every assumption
about endpoint behavior, the whole 5a/5b capacity estimate — all of it is
reasoned from documentation and prior vLLM measurements, not from touching a
real QVAC Fabric process. Finding out it's wrong is the actual deliverable
of this session, not a setback to paper over.
