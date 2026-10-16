# Security features and behavior — QVAC box (`bkn301-qvac-inference`)

**Status:** Current as of 2026-10-15. Based on a read-through of the code and docs; nothing was re-run to produce this document unless marked *measured*.
**Applies to:** this repo — the authenticating gateway (`gateway/`) plus deployment artefacts for the QVAC Fabric `llama-server` engine (`deploy/`).
**Sister doc:** [`bkn301-inference/docs/SECURITY.md`](../../bkn301-inference/docs/SECURITY.md) (vLLM box). **One-page version for DevOps:** [`SECURITY_DEVOPS_SUMMARY.md`](SECURITY_DEVOPS_SUMMARY.md).

Status tags used throughout:

| Tag | Meaning |
|---|---|
| **[IMPLEMENTED]** | In code or a manifest in this repo. |
| **[DESIGN ONLY]** | Documented as the plan; nothing built. |
| **[NOT DONE]** | Absent. |
| **[UNVERIFIED]** | Claimed or built but never exercised on the real path. |

**The short version.** The gateway is a reasonably hardened authenticating edge. The QVAC engine behind it is much weaker than vLLM: no native TLS, no mTLS, a single static bearer key, and nothing in the repo that actually enforces network isolation. The mTLS answer is a sidecar that is designed but not built. Most of the risk sits in that gap, and in the assumption that a private subnet protects the engine.

---

## 1. Trust model

```
caller (C# app) ──TLS 1.3──> edge (Cloudflare Tunnel / LB+WAF)   [DESIGN ONLY]
                              │
                              ▼
                      gateway :8080  (auth, quotas, audit)        [IMPLEMENTED]
                              │  mTLS (sidecar)                   [DESIGN ONLY]
                              ▼
                    QVAC llama-server :8000 (private subnet)      [isolation NOT ENFORCED]
```

- Rented GPU boxes (vast.ai) are treated as **untrusted**: synthetic prompts only, no production secrets, no real gateway keys (PRD §4.8, `docs/INFERENCE_SERVICE_PRD.md:786-835`; teardown checklist in `docs/VAST_AI_OPERATIONS.md`).
- The A10 cannot do confidential computing, so there is no cryptographic isolation from the infrastructure operator (PRD:923-927). End-to-end payload encryption was considered and rejected as no net gain (PRD:894-902).
- Redact-before-send and encrypt-persisted-data (PRD §5.4 items 3–4) are app/platform-side and **[NOT DONE]** in this repo.

## 2. TLS / mTLS

| Hop | Status | Detail |
|---|---|---|
| Client → edge | **[DESIGN ONLY]** | TLS 1.3 at Cloudflare Tunnel or LB+WAF (PRD:857-867). No edge config exists in the repo. |
| Edge → gateway | **[NOT DONE]** | uvicorn runs plain HTTP (`deploy/Dockerfile.gateway:42`); `main.py` has no TLS. |
| Gateway → engine (client side) | **[IMPLEMENTED]** | `gateway/upstream/client.py:73-96` builds an `ssl.SSLContext` by hand with CA bundle + client cert. Hand-built because httpx 0.28.1 silently drops `cert=` when `verify=<str>` is passed. One shared `httpx.AsyncClient` carries chat, health and probe traffic, so nothing bypasses it. Only active for `https://` entries in `UPSTREAM_URLS`. |
| Half-set cert/key | **[IMPLEMENTED]** | Raises at config load (`gateway/config.py`, `UPSTREAM_CLIENT_CERT`). |
| Gateway → engine (server side) | **[NOT DONE]** | The measured CUDA build of `llama-server` exits with "built without SSL support" if given `--ssl-*` flags, and has no client-CA option (`docs/QVAC_FORK.md:200-202`; `docs/HEAD_TO_HEAD.md:610-611`). |
| mTLS sidecar | **[DESIGN ONLY]** | `docs/QVAC_FORK.md:204-224` (decision 2026-10-13, matching the vLLM repo). nginx or envoy in the QVAC pod with `ssl_verify_client on`; `llama-server` bound to `127.0.0.1:8000`. SSE needs `proxy_buffering off`, HTTP/1.1, and `proxy_read_timeout` ≥ 120 s. kubelet probes can't present a client cert, so use a plain side port serving only `/health`, or an `exec` probe on loopback. No sidecar config exists anywhere in the repo. |
| OpenSSL-enabled engine build | **[UNVERIFIED]** | Alternative to the sidecar (`-DLLAMA_OPENSSL=ON`); never built or tested. |

**Tests:** `tests/test_mtls.py:247-296` covers rejection with no client cert, rejection when the server cert is from the wrong CA, success with the right chain, and fail-closed when unconfigured. It proves wiring with self-signed certs and a bare `ssl` socket — **not** a live engine. The PRD §4.8 security review "has to run against the real deployment path" and has not.

**Interim posture** for the vast.ai test was plain HTTP plus bearer token on a private path (`docs/QVAC_FORK.md:189-199`). Certificate issuance and rotation are "left to DevOps."

## 3. Authentication and authorization

### Gateway (`gateway/auth/`)

- **[IMPLEMENTED] Two credential types** on `Authorization: Bearer`, dispatched by shape (`auth/__init__.py:155-196`): three dot-separated segments not starting with `bkn_` is a JWT; everything else is an API key. Missing/empty/non-Bearer gives a generic 401 with `WWW-Authenticate: Bearer`. The rejection reason is never returned to the caller (audit-only).
- **[IMPLEMENTED] API keys** (`auth/keys.py`): format `bkn_live_<keyid>_<secret>` (or `bkn_test_…`); `keyid` = 6 random bytes hex, secret = `token_urlsafe(32)`. Only `argon2id(secret + pepper)` is stored (argon2id rather than bcrypt to avoid the 72-byte truncation). Plaintext is returned once at mint time. Per-key `expires_at`, `revoked`, and `scopes` are enforced on every verify.
- **[IMPLEMENTED] Enumeration resistance:** an unknown keyid still runs a dummy argon2 verify (`keys.py:197-202, 217-224`).
- **[IMPLEMENTED] Argon2 DoS bound:** verification is offloaded to a thread pool limited by `AUTH_VERIFY_CONCURRENCY=6` (`auth/__init__.py:29-79`). Unbounded offload was measured to cause ~2.5 GiB of transient allocation, which is why this exists.
- **[IMPLEMENTED] JWT** (`auth/jwt.py`): asymmetric algorithms only (RS/ES 256/384/512); a symmetric `JWT_ALGORITHM` raises at construction. Decode pins a single-element `algorithms` list (algorithm-confusion guard). Required claims `exp, iat, sub, iss, aud`; `iss` and `aud` verified; 30 s leeway. Disabled (every JWT rejected) unless `JWT_PUBLIC_KEY` is set.
- **[IMPLEMENTED] Scopes:** `chat` required on `/v1/chat/completions` (`*` is a wildcard). `/v1/models` requires a valid principal but no scope. Per-model scoping from PRD §5.3 is **[NOT DONE]**.
- **[IMPLEMENTED] Surface reduction:** only `/v1/chat/completions` and `/v1/models` are proxied. The engine's `/slots`, `/props`, `/v1/completions`, `/v1/embeddings` are not reachable through the gateway.

### Caveats

- **Pepper is optional.** `API_KEY_PEPPER` unset logs a WARNING at startup; it does not fail. A deployment can run unpeppered.
- **"Every key expires" is not enforced.** `mint_key` sets an expiry only if `ttl_sec` is passed (`keys.py:143`), though a comment says otherwise.
- **"JWT lifetime ≤ 5 minutes" is a minting convention, not a gateway check.** The gateway verifies `exp` but does not cap `exp − iat`. There is deliberately no JWT revocation.
- **Revocation needs a restart.** `KeyStore.reload()` exists but nothing calls it on a signal or timer.
- **Key store writes are non-atomic** (`keys.py:173-176`, plain `write_text`), with no `chmod 600`.
- **No key-management CLI.** Keys are minted with a README one-liner or `scripts/dev_key.sh`. Rotation with an overlap window (PRD §5.3) is **[NOT DONE]**.
- **Minor timing leak:** revoked/expired keys return before the argon2 hash runs, so latency distinguishes "valid keyid, revoked" from "wrong secret."

### Engine (QVAC `llama-server`)

- **[IMPLEMENTED] Single static bearer key.** `UPSTREAM_API_KEY` is sent as `Authorization: Bearer` on every upstream call (`client.py:240-242`); the engine side is `--api-key` (`deploy/k8s/probes.yaml:71-72`). This is the engine's **only** auth.
- **[IMPLEMENTED, measured]** `/health` needs no auth. `/v1/models`, `/metrics`, `/slots` return 401 without the key (`docs/QVAC_FORK.md:225-227`; `docs/HEAD_TO_HEAD.md:607`). `/slots` is on by default (`docs/PHASE1_RESULTS_QVAC.md:52`) — restrict it at the network layer.
- **Key in argv (undocumented elsewhere).** The Secret `qvac-api-key` is env-sourced, but `probes.yaml` interpolates it into the command line as `--api-key $(QVAC_API_KEY)`. It is therefore readable via `/proc/<pid>/cmdline` or `ps` inside the pod and by anything with node access.

## 4. Network exposure

| Item | Value | Source |
|---|---|---|
| Gateway bind | `0.0.0.0:8080` (deliberate; container netns is the boundary) | `config.py`, `.env.example:12-13` |
| Image CMD | `--host 0.0.0.0 --port 8080 --workers 1` (env host/port have no effect in-container) | `Dockerfile.gateway:42` |
| Compose gateway | published on loopback only: `127.0.0.1:8080:8080` | `docker-compose.yml:34-37` |
| Compose stub | `expose: 8001` only, not published | `docker-compose.yml:20-24` |
| Engine (k8s) | `--host 0.0.0.0 --port 8000` | `probes.yaml:73-76` |
| Engine (vast.ai scripts) | `127.0.0.1:8000` | `scripts/h2h_serve.sh:83,99` |
| CORS | none, by design (server-to-server) | `gateway/main.py` |

- **[IMPLEMENTED]** Compose publishes loopback only; no CORS.
- **[DESIGN ONLY]** Edge is Cloudflare Tunnel or LB+WAF: no inbound ports on the GPU host, global rate limit, TLS termination (PRD:857-859).
- **[NOT DONE]** No firewall, `ufw`/`iptables`, NetworkPolicy, gateway Deployment/Service/Ingress, or trusted-proxy handling. README says `/metrics` "needs a network policy" but none is supplied.
- **Important caveat:** the README says the engine is "reachable solely by the gateway," but the k8s args bind `0.0.0.0` and nothing in the repo enforces the restriction. It rests entirely on private-subnet placement plus a NetworkPolicy that does not exist.

## 5. Container and privilege hardening

**There are no systemd units anywhere in this repo.** The process model is a container (Kubernetes/compose); on vast.ai, processes are launched by `setsid nohup` as root on a box the docs treat as untrusted (`scripts/h2h_serve.sh:109`).

- **[IMPLEMENTED] Non-root gateway:** `useradd --system --no-create-home --shell /usr/sbin/nologin gateway`, then `USER gateway`; `/app/logs` is the only writable path (`Dockerfile.gateway:21-30`).
- **[IMPLEMENTED] Multi-stage build:** `build-essential` stays in the build stage; CI asserts no `gcc`/`cc`/`make` and uid ≠ 0 in the runtime image (`ci.yml:35-50`).
- **[IMPLEMENTED] Stub isolation:** the unauthenticated stub is only in the `dev` target, excluded from the package (`pyproject.toml`) and from the build context (`.dockerignore` allowlist). A final `FROM runtime AS default` stage makes a bare `docker build` ship the gateway. CI asserts the stub is not importable.
- **[IMPLEMENTED]** No cloud SDK in the image (enforced by `tests/test_config.py`); single worker; loopback `HEALTHCHECK`.
- **[NOT DONE]** No `securityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, `capabilities.drop`), seccomp/AppArmor, read-only rootfs, resource limits, or compose `cap_drop`/`no-new-privileges`. The Dockerfile has no numeric UID, so Kubernetes `runAsNonRoot` can't verify the named user.
- **Engine pod user is unspecified;** `probes.yaml` uses a placeholder image.
- **[UNVERIFIED] CI has never run.** `pyproject.toml:21-28` says the repo had no remote when the `image` job was written. The README treats a CI failure as "a blocker"; that is a claim about workflow code, not an observed result.

## 6. Secrets handling

- **[IMPLEMENTED] Env vars are the only config surface.** `UPSTREAM_API_KEY`, `API_KEY_PEPPER`, `JWT_PUBLIC_KEY` (public), and mTLS material by **file path** so it can come from a mounted Secret. Full table: README "Secrets and config to provision."
- **[IMPLEMENTED] Ignore rules:** `.gitignore` covers `.env*` (except `.env.example`), `keys.json`, `deploy/local-keys.json`, `logs/`, `*.jsonl`; `.dockerignore` is an allowlist and also excludes `keys.json`/`.env`. `results/**` is deliberately re-included (committed evidence, including throwaway-key audit logs).
- **[IMPLEMENTED] Dev-only literal pepper** `local-dev-pepper-not-a-secret` in `docker-compose.yml:43`; must match `scripts/dev_key.sh`.
- **[IMPLEMENTED] Pre-transfer hygiene for rented boxes:** `scripts/pack_for_box.sh` builds the tarball from a throwaway git index, applies a deny-list (`.env*`, `*keys.json`, `*api_key*`, logs, models, data), runs a shape-aware credential scan (`sk-ant-…`, `bkn_live_<12hex>_<43>`), refuses to run inside `baas-poc-templates`, and prints the manifest. `scripts/verify_box.sh` has a matching `hygiene_scan` with `--self-test` and `--scan-only`. Tests: `test_pack_for_box.py`, `test_verify_box.py`.
- **[IMPLEMENTED] Command-record redaction:** `scripts/h2h_serve.sh:106` seds the key out of the saved `.cmd` file. *Not exhaustively checked that nothing under `results/` leaks a key.*
- **[NOT DONE]** Secret rotation mechanism ("left to DevOps"); secret scanning in CI (scans exist only in the two box scripts; no gitleaks/trufflehog); `HF_TOKEN` handling is not addressed.

## 7. Model and image integrity

- **[IMPLEMENTED, bench scripts only] Weights pinned and hash-verified.** `Qwen/Qwen3-8B-GGUF` @ `7c41481f57cb95916b40956ab2f0b139b296d974`, `Qwen3-8B-Q4_K_M.gguf`, sha256 `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`. `scripts/qvac_fixes_box.sh:33-44` and `scripts/h2h_q8_rerun.sh:24-36` verify and exit non-zero on mismatch.
- **[NOT DONE] No integrity check in the deployment path.** `probes.yaml:40` mounts `/models/Qwen3-8B-Q4_K_M.gguf` with no init container, fetch step, or hash check. The check lives only in bench scripts.
- **[NOT DONE]** PRD §5.4 item 5 (checksums at image build, image scanning, SBOM). GGUF is not safetensors, so the PRD's "safetensors only" clause needs restating for this fork.
- **[NOT DONE] Engine image is a placeholder:** `REPLACE_ME/qvac-fabric:UNPINNED` (`probes.yaml:34`). The measured binary was built from `tetherto/qvac-fabric-llm.cpp` @ `7bb2161c690befa1bb44ff9b20e4eea2da6c22b8` with `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86` on CUDA 13.0. The build is **not hermetic** (fetches a web-UI bundle) and has **no SSL**.
- **Gateway image:** Python deps pinned with exact `==`; **no** hash-locked lockfile (transitives float), **no** `pip-audit`/Trivy/SBOM, base `python:3.12-slim` pinned by **tag not digest**, CI actions pinned by tag only (`actions/checkout@v4`, `setup-python@v5`).
- **Minor:** the allowlist name `Qwen/Qwen3-8B-GGUF` differs from the engine alias `qwen3-8b` (`probes.yaml:69-70`).

## 8. Logging, audit and redaction

- **[IMPLEMENTED] Metadata-only audit log** (`gateway/audit.py`): timestamp, event, keyid, subject, auth_method, model, status, latency_ms, token counts, upstream, reason (closed vocabulary), streamed. Thread-locked append-only JSONL; a write failure logs at ERROR and does not fail the request. Rejected requests are audited. `tests/test_audit_no_prompt_text.py` uses a canary string against the real writer.
- **[IMPLEMENTED] `AUDIT_INCLUDE_PROMPT_TEXT`** defaults false and is not wired to write prompt text; setting it only logs an ERROR.
- **[IMPLEMENTED, measured] Engine logs are clean by default:** a canary prompt was absent from the default log, the `-v` log (1,770 lines), and `--log-file` (`docs/QVAC_FORK.md:234-238`). **`--log-prompts-dir` writes prompts to disk and must never be set** (`probes.yaml:77-78`) — enforced only by the flag's absence.
- **[IMPLEMENTED]** Callers see only generic errors; upstream error bodies are drained (≤64 KiB) and discarded.
- **[NOT DONE]** Audit-log integrity (hash chain/tamper evidence), rotation, retention policy, access control, or shipping to a SIEM. Replicas write separate local files.
- **[NOT DONE] Failed authentications are not audited.** The 401 only increments a metric (`gateway/metrics.py:238-241` acknowledges this); `keys.py:193` still claims otherwise.
- `sub` from a JWT is written to `subject` unsanitised (JSON-encoded, so not injectable, but unbounded length).
- App logging is plain `logging.basicConfig(INFO)`; no redaction filter. Safe only because nothing sensitive is logged.

## 9. Health, metrics and the unauthenticated surface

Unauthenticated gateway endpoints: `/healthz`, `/readyz`, `/metrics`, and FastAPI's `/docs`, `/redoc`, `/openapi.json`.

- **`/healthz`** — liveness only.
- **`/readyz`** — **leaks private upstream URLs.** It returns `"upstreams"` and `"breakers"` keyed by raw replica URL (`routes/health.py:53-57`, `client.py:452-458`, `breaker.py:261-262`). This contradicts the stated policy that URLs stay off unauthenticated endpoints (`main.py`, `metrics.py:25`). On an exception it also returns the exception class name. Not documented as a gap elsewhere. *(Confirmed by reading the code; not exercised at runtime.)*
- **`/metrics`** — unauthenticated by design (`routes/metrics.py:1-8`). Mitigation is what's collected: no `keyid`, no URLs (replicas by index), `model` folded to the allowlist or `other`, bounded labels. `METRICS_ENABLED=false` returns 404. Restriction depends on a NetworkPolicy that does not exist.
- **`/docs`, `/redoc`, `/openapi.json`** — `create_app()` doesn't disable them (`gateway/main.py:142-150`), so the API schema is public. Undocumented.
- **Engine probes:** `probes.yaml` probes the engine's `/health` (no auth needed). A freeze hangs `/health`, as measured.
- **Wedge detector is partial [UNVERIFIED].** It did not fire during a measured freeze (150 s) and misses mid-stream stalls. `UPSTREAM_WEDGE_WINDOW_SEC=120` is an estimate, not a measurement. The engine's `--metrics` is enabled but not scraped.

## 10. Rate limiting and input limits

**Quotas (per API key):** cumulative token budget (checked before forwarding; an in-flight request still finishes) and a concurrency cap (held for the life of a stream). Both are **in-memory and per-process** (reset on restart, not shared across replicas), **unlimited by default** unless set at mint time, and **JWT principals are exempt**. A caller who sets `stream_options.include_usage: false` can evade budget accounting on streams (`chat.py:358-360`).

**Not done:** request-rate limiting of any kind (per key, IP or subject). The PRD assigns it to the edge, which is design-only.

| Limit | Value | Behavior |
|---|---|---|
| Body size | `MAX_BODY_BYTES` = 1 MiB | 413 — but the **whole body is read before the check** (`chat.py:79-82`), so memory is not bounded for an oversized upload (authenticated callers only) |
| Completion tokens | 2048 | `max_tokens` clamped; non-int or < 1 gives 400 |
| Prompt tokens | 7000 | Estimated by whitespace word count; errs low, so CJK/code/base64 can far exceed it. `tools` and other large fields aren't counted |
| Model | `ALLOWED_MODELS` allowlist | 400 `model_not_allowed` |
| `messages` | non-empty list | 400 |
| Upstream timeouts | 5 s connect / 120 s read | bounded retries (2 attempts) on 429/502/503/504 |

Other caveats, all **[UNVERIFIED]** (read from code, not tested):
- The request body is forwarded with only `max_tokens` and `stream_options` modified. `n`, `logit_bias`, `tools`, `response_format` and llama.cpp's `grammar` pass through. A large `n` could multiply slot cost against per-key accounting.
- A non-dict element in `messages` would hit `m.get` and probably produce a 500 rather than a 4xx.
- 7000 prompt + 2048 completion can exceed the engine's 8192 per-slot context (`-c 65536 -np 8`), giving `context_length_exceeded` at the engine.
- 429 responses carry no `Retry-After`; the all-replicas-open 503 does.

## 11. Stale claims still in the repo

Left as-is by design; flagged so nobody trusts them:

1. `README.md` "Current status" (~364-383) says nothing has run against a real QVAC server. Contradicted by the measured A10 sections and `docs/QVAC_FORK.md`.
2. `docs/PROBE_CONTRACT.md` §2 table lists `/v1/models` probes; `deploy/k8s/probes.yaml` (`/health`) is the source of truth.
3. `gateway/audit.py:20-25` and `.env.example:17-19` still say QVAC logging flags are unaudited; they are now measured.
4. `gateway/routes/chat.py` and `stub/server.py` cite a "QVAC context default 1024" that belongs to the unrelated `qvac serve` wrapper.
5. `.github/workflows/ci.yml` builds `bkn301-inference:ci` (sister repo's name).
6. `docs/QVAC_FORK.md:64` still calls `probes.yaml` all-placeholder.

## 12. Open items for DevOps (prioritised)

**P0 — before any non-throwaway traffic**
1. Enforce engine isolation: NetworkPolicy/SG so only the gateway reaches `:8000`; today nothing does.
2. Build the mTLS sidecar (or an OpenSSL engine build) and run the PRD §4.8 security review against the real path.
3. Provide edge TLS 1.3 and a global rate limit (Cloudflare/WAF/LB). The gateway has neither.
4. Stop passing the engine key in argv.

**P1**
5. Make `/readyz` return replica indexes, not URLs; disable `/docs`, `/redoc`, `/openapi.json`.
6. Add `securityContext` (non-root numeric UID, read-only rootfs, drop caps, no privilege escalation), resource limits, and a gateway manifest.
7. Pin images by digest; add a hash-locked lockfile, `pip-audit`/Trivy, SBOM; pin CI actions by SHA. Replace the `UNPINNED` engine image with a reproducible build.
8. Add sha256 verification of the GGUF to the deploy path (init container).
9. Require `API_KEY_PEPPER` (fail, not warn).

**P2**
10. Audit failed authentications; add log rotation, retention policy, shipping.
11. Key reload/rotation tooling, atomic key-store writes, `chmod 600`.
12. Request-rate limiting in the gateway; mandatory per-key quotas; shared quota store for multi-replica.
13. Stream body-size limit; allowlist forwarded request fields.
14. Get CI to run once and record the result.

**Never validated:** mTLS on a live engine; the PRD Phase 2 and external security reviews; CI; wedge detection on an idle freeze; the 24 h soak; Azure `NVadsA10_v5` full-card SKU; the network path from the consuming app to the gateway ("the single largest unknown").
