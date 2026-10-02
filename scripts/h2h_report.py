#!/usr/bin/env python3
"""Turn head-to-head loadgen jsonl into markdown comparison tables.

    python3 scripts/h2h_report.py results/head-to-head-a10 [--suites A_warm,A_cold]

Reads every `<engine>/<suite>.jsonl` under the root. For each suite and level,
reports the mean across repeats (and stdev where there were several) of:
per-user decode p50 / p05, wall and steady-state aggregate, TTFT p50 / p95 /
p99, ITL p50 / p95, prefill, cache hit, queue time, failures.

Prefill and cache hit come from the best available source, named in the
table: llama.cpp's per-request `timings` (server-measured prefill of the
uncached tokens, `cache_n`), else vLLM's /metrics diff (mean
request_prefill_time, prefix_cache hit rate).
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def load(path: Path) -> tuple[dict, list[dict], list[dict]]:
    lines = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    return (lines[0], [x for x in lines if x.get("kind") == "level"],
            [x for x in lines if x.get("kind") == "request"])


def mean_sd(vals: list[float]) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "-"
    m = statistics.fmean(vals)
    if len(vals) > 1:
        return f"{m:.1f} ±{statistics.stdev(vals):.1f}"
    return f"{m:.1f}"


def m(vals: list[float], nd: int = 0) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "-"
    return f"{statistics.fmean(vals):.{nd}f}"


def prefill_and_cache(lv: dict) -> tuple[float | None, float | None, float | None, str]:
    md = lv.get("metrics_derived") or {}
    if lv.get("server_prefill_ms_p50") is not None:
        return (lv["server_prefill_ms_p50"], lv.get("cache_hit_ratio"),
                None, "timings")
    return (md.get("mean_ms:vllm:request_prefill_time_seconds"),
            md.get("vllm_prefix_cache_hit_rate", lv.get("cache_hit_ratio")),
            md.get("mean_ms:vllm:request_queue_time_seconds"), "metrics")


def table(engine: str, suite: str, header: dict, levels: list[dict]) -> str:
    shape = (f"prompt={header.get('prompt_tokens_target') or '~' + str(header.get('ctx_tokens_requested')) + '(words)'}"
             f" +{header.get('max_tokens')} out, cache={header.get('cache_mode')}")
    out = [f"#### {engine} / {suite}  ({shape})", "",
           "| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 "
           "| ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    src = None
    for c in sorted({x["concurrency"] for x in levels}):
        ls = [x for x in levels if x["concurrency"] == c]
        pcs = [prefill_and_cache(x) for x in ls]
        src = pcs[0][3]
        hit = [p[1] for p in pcs if p[1] is not None]
        out.append(
            f"| {c} | {len(ls)} | {mean_sd([x['per_user_decode_tok_s_p50'] for x in ls])} "
            f"| {m([x.get('per_user_decode_tok_s_p05') for x in ls], 1)} "
            f"| {m([x['aggregate_decode_tok_s'] for x in ls], 1)} "
            f"| {m([x.get('aggregate_steady_tok_s') for x in ls], 1)} "
            f"| {m([x['ttft_ms_p50'] for x in ls])} | {m([x['ttft_ms_p95'] for x in ls])} "
            f"| {m([x.get('ttft_ms_p99') for x in ls])} "
            f"| {m([x.get('itl_ms_p50') for x in ls], 1)} | {m([x.get('itl_ms_p95') for x in ls], 1)} "
            f"| {m([p[0] for p in pcs])} "
            f"| {(f'{100 * statistics.fmean(hit):.1f}%') if hit else '-'} "
            f"| {m([p[2] for p in pcs])} | {sum(x['failures'] for x in ls)} |")
    out.append("")
    out.append(f"_prefill/cache source: {src}; prompt tokens actually sent: "
               f"{sorted({x.get('prompt_tokens_min') for x in levels} | {x.get('prompt_tokens_max') for x in levels})}_")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root")
    ap.add_argument("--suites", default=None)
    args = ap.parse_args()
    want = set(args.suites.split(",")) if args.suites else None
    root = Path(args.root)
    for eng in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(eng.glob("*.jsonl")):
            if f.stem == "boot" or (want and f.stem not in want):
                continue
            try:
                header, levels, _ = load(f)
            except Exception:                                # noqa: BLE001
                continue
            if header.get("kind") != "header" or not levels:
                continue
            print(table(eng.name, f.stem, header, levels))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
