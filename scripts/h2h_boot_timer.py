#!/usr/bin/env python3
"""Start-up timing for one engine on the head-to-head box (axis 6).

    python3 scripts/h2h_boot_timer.py vllm vllm-boot --mode cold_full -o boot.jsonl
    python3 scripts/h2h_boot_timer.py qvac q4km-boot --mode warm -o boot.jsonl -- -cram 0

Modes:
  warm        weights in page cache, compile caches intact (a restart).
  cold_weights  weights evicted from page cache (posix_fadvise DONTNEED, with
              residency verified by mincore), compile caches intact.
  cold_full   cold_weights plus vLLM's torch.compile / inductor / triton caches
              removed: the closest a reused node gets to a fresh pod. (A truly
              fresh pod also downloads weights; that is timed separately, once,
              when the weights are first fetched.)

Measured from the moment the process is spawned (h2h_serve.sh's t_start):
  t_log_ready   the engine's own "ready" log line
  t_health      first 200 from /health (what a k8s startupProbe sees)
  t_first_token first completion of a 1-token request (what a user sees)
Polling is every 100 ms, so each figure is good to about +-0.1 s. vLLM's
reference (PHASE1_RESULTS.md §4) was 1 s polling by hand.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import mmap
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

H2H = Path(os.environ.get("H2H", "/root/h2h"))
REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()

VLLM_COMPILE_CACHES = [HOME / ".cache" / "vllm", HOME / ".triton",
                       Path("/tmp/torchinductor_root"), HOME / ".cache" / "flashinfer"]


def env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    p = H2H / "env.sh"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def model_files(engine: str, env: dict[str, str]) -> list[Path]:
    if engine == "qvac":
        return [Path(os.environ.get("GGUF") or env.get("GGUF",
                                                       "/root/models/Qwen3-8B-Q4_K_M.gguf"))]
    repo = (os.environ.get("VLLM_MODEL") or env.get("VLLM_MODEL", "Qwen/Qwen3-8B-AWQ"))
    hub = Path(os.environ.get("HF_HOME", HOME / ".cache" / "huggingface")) / "hub"
    d = hub / ("models--" + repo.replace("/", "--")) / "blobs"
    return sorted(p for p in d.glob("*") if p.is_file())


_libc = ctypes.CDLL(None, use_errno=True)
_libc.mmap.restype = ctypes.c_void_p
_libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                       ctypes.c_int, ctypes.c_int, ctypes.c_long]
_libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
_MAP_FAILED = ctypes.c_void_p(-1).value


def resident_fraction(paths: list[Path]) -> float | None:
    """Fraction of the files' pages currently in the page cache (mincore).

    This is the check that makes "cold" mean cold: posix_fadvise is advisory,
    so the eviction is verified rather than assumed.
    """
    page = os.sysconf("SC_PAGE_SIZE")
    total = res = 0
    for p in paths:
        size = p.stat().st_size
        if size == 0:
            continue
        n = (size + page - 1) // page
        vec = (ctypes.c_ubyte * n)()
        fd = os.open(p, os.O_RDONLY)
        try:
            a = _libc.mmap(None, size, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
            if a is None or a == _MAP_FAILED:
                return None
            try:
                if _libc.mincore(a, size, vec) != 0:
                    return None
                res += sum(1 for b in vec if b & 1)
                total += n
            finally:
                _libc.munmap(a, size)
        finally:
            os.close(fd)
    return round(res / total, 4) if total else None


def evict(paths: list[Path]) -> None:
    for p in paths:
        fd = os.open(p, os.O_RDONLY)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
    try:  # best effort; read-only in most containers
        Path("/proc/sys/vm/drop_caches").write_text("1\n")
    except OSError:
        pass


def http(url: str, key: str | None = None, body: dict | None = None,
         timeout: float = 5.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 method="POST" if body else "GET")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - loopback
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:                                        # noqa: BLE001
        return 0, b""


def gpu_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0])
    except Exception:                                        # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("engine", choices=("vllm", "qvac"))
    ap.add_argument("name")
    ap.add_argument("--mode", choices=("warm", "cold_weights", "cold_full"), required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--port", type=int, default=8000)
    # Engine flags go after a literal `--`; split by hand because argparse's
    # REMAINDER after positionals swallows the options as well.
    argv = sys.argv[1:]
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    args = ap.parse_args(argv)
    env = env_file()
    key = os.environ.get("API_KEY") or env.get("API_KEY")
    serve = REPO / "scripts" / "h2h_serve.sh"

    subprocess.run(["bash", str(serve), "stop"], check=False)
    files = model_files(args.engine, env)
    rec: dict = {"kind": "boot", "engine": args.engine, "name": args.name,
                 "mode": args.mode, "extra": extra,
                 "model_files": [str(p) for p in files],
                 "model_bytes": sum(p.stat().st_size for p in files),
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if args.mode in ("cold_weights", "cold_full"):
        evict(files)
        removed = []
        if args.mode == "cold_full" and args.engine == "vllm":
            for d in VLLM_COMPILE_CACHES:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
                    removed.append(str(d))
        rec["compile_caches_removed"] = removed
    rec["resident_before_start"] = resident_fraction(files)

    subprocess.run(["bash", str(serve), "start", args.engine, args.name, *extra],
                   check=True)
    cur = json.loads((H2H / "run" / "current.json").read_text())
    t0 = float(cur["t_start"])
    log = Path(cur["log"])
    ready = re.compile(cur["ready_re"])
    base = f"http://127.0.0.1:{args.port}"
    t_log = t_health = None
    deadline = time.time() + args.timeout
    pos = 0
    # Stop on /health; give the log line at most 5 s more. A ready pattern
    # that never matches (log format drift between builds) must not turn a
    # 2-second boot into a 20-minute timeout.
    while time.time() < deadline and (t_log is None or t_health is None):
        if t_health is not None and time.time() - t_health > 5.0:
            break
        if t_log is None and log.exists():
            with log.open("r", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read()
                pos = fh.tell()
            if ready.search(chunk):
                t_log = time.time()
        if t_health is None and http(f"{base}/health", timeout=1.0)[0] == 200:
            t_health = time.time()
        time.sleep(0.1)
    if t_health is None:
        rec["error"] = "health_timeout"
        print(json.dumps(rec))
        with open(args.output, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        return 1

    body = {"model": "qwen3-8b", "max_tokens": 1, "temperature": 0.0,
            "messages": [{"role": "user", "content": "Say ok."}]}
    status, _ = http(f"{base}/v1/chat/completions", key, body, timeout=args.timeout)
    t_first = time.time()
    t1 = time.time()
    status2, _ = http(f"{base}/v1/chat/completions", key, body, timeout=60)
    second_ms = (time.time() - t1) * 1000.0

    rec.update({
        "t_log_ready_s": round(t_log - t0, 2) if t_log else None,
        "t_health_s": round(t_health - t0, 2),
        "t_first_token_s": round(t_first - t0, 2) if status == 200 else None,
        "first_request_status": status,
        "second_request_ms": round(second_ms, 1) if status2 == 200 else None,
        "gpu_used_mib_after_load": gpu_used_mib(),
        "resident_after_load": resident_fraction(files),
        "log": str(log),
    })
    print(json.dumps(rec))
    with open(args.output, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
