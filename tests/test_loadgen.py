"""`scripts/loadgen.py` — the Phase 1 instrument.

WHY THESE TESTS ARE END-TO-END AGAINST A REAL SOCKET
====================================================
The app repo's bench harness looks like it measures throughput and does not:
it has no concurrency at all, never takes a first-token timestamp, and its
`decode_tok_s` divides completion tokens by whole-request latency, conflating
prefill with decode. Every one of those defects would pass a unit test of the
arithmetic. So the tests that matter here assert the properties the harness
lacked:

  * the workers genuinely overlap (proved by wall-clock, not by inspection);
  * TTFT and decode rate come out as two separate numbers;
  * server-reported token counts are used when offered and the delta-counting
    fallback is used when they are not, and the difference is visible in the
    output rather than silently smoothed over;
  * a failure is reported as a failure.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parent.parent
LOADGEN = REPO / "scripts" / "loadgen.py"


def _loadgen_module():
    """Import loadgen.py by path.

    It is a script in `scripts/`, not a package module -- deliberately, since
    it has to be runnable on a bare rented box with nothing installed but this
    repo. Loading it by path keeps that true instead of adding a package just
    to make it importable from a test.
    """
    if "_loadgen" in sys.modules:
        return sys.modules["_loadgen"]
    spec = importlib.util.spec_from_file_location("_loadgen", LOADGEN)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: `@dataclass` resolves its own module's namespace
    # through `sys.modules[cls.__module__]`, so a module that is not yet
    # registered makes every dataclass in it fail to build.
    sys.modules["_loadgen"] = module
    spec.loader.exec_module(module)
    return module


def _run(out: Path, url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - interpreter plus literals
        [sys.executable, str(LOADGEN), "--url", f"{url}/v1",
         "--model", "Qwen/Qwen3-8B-GGUF", "-o", str(out), *args],
        cwd=REPO, capture_output=True, text=True, timeout=300,
    )


def _records(out: Path) -> tuple[dict, list[dict], list[dict]]:
    lines = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    header = lines[0]
    return (header,
            [r for r in lines if r.get("kind") == "request"],
            [r for r in lines if r.get("kind") == "level"])


class TestMeasuresWhatItClaims:
    def test_ttft_and_decode_are_separate_numbers(self, live_stub, tmp_path):
        """The defect this tool exists to fix: one number for two questions."""
        url = live_stub("--stub-latency", "1.0")
        out = tmp_path / "lg.jsonl"
        result = _run(out, url, "--levels", "1", "--requests", "2",
                      "--ctx-tokens", "200", "--max-tokens", "32", "--warmup", "0")
        assert result.returncode == 0, result.stdout + result.stderr

        _, requests, levels = _records(out)
        assert requests, "no request records written"
        for r in requests:
            assert r["ok"], r
            assert r["ttft_ms"] is not None
            assert r["decode_ms"] is not None
            assert r["decode_tok_s"] is not None
            # Distinct measurements of distinct phases, not one divided twice.
            assert r["ttft_ms"] != r["decode_ms"]
            # total >= ttft + decode, since total also covers the closing frames.
            assert r["total_ms"] >= r["ttft_ms"] + r["decode_ms"] - 1.0
        assert levels[0]["ttft_ms_p50"] is not None
        assert levels[0]["per_user_decode_tok_s_p50"] is not None

    def test_decode_rate_excludes_the_prefill_token(self, live_stub, tmp_path):
        """(n-1)/decode_window, not n/decode_window.

        The first token is produced BY prefill, so counting it as decode
        inflates the rate -- materially so at the short completion lengths a
        benchmark uses.
        """
        url = live_stub()
        out = tmp_path / "lg.jsonl"
        assert _run(out, url, "--levels", "1", "--requests", "1",
                    "--ctx-tokens", "100", "--max-tokens", "40",
                    "--warmup", "0").returncode == 0
        _, requests, _ = _records(out)
        r = requests[0]
        expected = round((r["completion_tokens"] - 1) / (r["decode_ms"] / 1000.0), 2)
        assert r["decode_tok_s"] == pytest.approx(expected, rel=1e-6)


class TestConcurrencyIsReal:
    def test_workers_overlap(self, live_stub, tmp_path):
        """The property `bench_llm_backends.py` cannot have.

        The stub sleeps `--stub-latency` per request before streaming. Eight
        workers issuing one request each must therefore finish in roughly the
        time one takes, not eight times it. A sequential generator fails this
        by a wide margin, so the threshold does not need to be tight to be
        decisive.
        """
        url = live_stub("--stub-latency", "1.0")
        out = tmp_path / "lg.jsonl"
        assert _run(out, url, "--levels", "1,8", "--requests", "1",
                    "--ctx-tokens", "100", "--max-tokens", "16",
                    "--warmup", "0", "--settle", "0").returncode == 0

        _, _, levels = _records(out)
        one = next(x for x in levels if x["concurrency"] == 1)
        eight = next(x for x in levels if x["concurrency"] == 8)
        assert eight["requests"] == 8
        # Sequential would be ~8x. Allow generous slack for scheduling and CI.
        assert eight["wall_s"] < one["wall_s"] * 4, (
            f"8 concurrent took {eight['wall_s']}s vs {one['wall_s']}s for 1 -- "
            "the workers are not actually running in parallel"
        )

    def test_every_level_runs_every_request(self, live_stub, tmp_path):
        url = live_stub()
        out = tmp_path / "lg.jsonl"
        assert _run(out, url, "--levels", "1,3", "--requests", "2",
                    "--ctx-tokens", "100", "--max-tokens", "16",
                    "--warmup", "0", "--settle", "0").returncode == 0
        _, requests, levels = _records(out)
        assert {x["concurrency"]: x["requests"] for x in levels} == {1: 2, 3: 6}
        assert len(requests) == 8


class TestTokenCounts:
    def test_server_reported_usage_is_used_when_offered(self, live_stub, tmp_path):
        """`stream_options.include_usage` is what the Phase 2 budget needs."""
        url = live_stub()
        out = tmp_path / "lg.jsonl"
        assert _run(out, url, "--levels", "1", "--requests", "1",
                    "--ctx-tokens", "300", "--max-tokens", "24",
                    "--warmup", "0").returncode == 0
        _, requests, levels = _records(out)
        assert requests[0]["usage_reported"] is True
        assert levels[0]["usage_reported_by_server"] is True
        # The stub counts prompt tokens by words; a 300-token request is ~225
        # words, so a real count arrived rather than a zero.
        assert requests[0]["prompt_tokens"] > 100

    async def test_empty_completion_is_a_failure_not_a_fast_request(self):
        """The B3 shape: a stream that completes having produced no content.

        This is the exact failure PRD §6.1 B3 describes -- an unterminated
        `<think>` block consumes the whole `max_tokens` allowance and the answer
        arrives empty. It has to be a failure here, because a zero-token
        response is also the FASTEST possible response: averaged in, a
        uniformly degraded run reads as an excellent one, which is precisely how
        B3 was described as failing silently.
        """
        lg = _loadgen_module()

        def handler(request: httpx.Request) -> httpx.Response:
            # Role frame and finish frame, no content frame anywhere.
            body = (
                'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                '"content":""},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"index":0,"delta":{},'
                '"finish_reason":"length"}]}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=body,
                                  headers={"content-type": "text/event-stream"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await lg.one_request(client, "http://x/v1/chat/completions",
                                       {}, level=1, worker=0)
        assert res.ok is False
        assert res.error == "empty_completion"
        assert res.decode_tok_s is None

    async def test_usage_absent_falls_back_to_counting_deltas(self):
        """Flagged as an estimate, not passed off as a server count."""
        lg = _loadgen_module()

        def handler(request: httpx.Request) -> httpx.Response:
            frames = [
                'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                '"content":""},"finish_reason":null}]}',
            ]
            frames += [
                'data: {"choices":[{"index":0,"delta":{"content":"tok "},'
                '"finish_reason":null}]}'
            ] * 5
            frames.append('data: {"choices":[{"index":0,"delta":{},'
                          '"finish_reason":"stop"}]}')
            frames.append("data: [DONE]")
            return httpx.Response(200, text="\n\n".join(frames) + "\n\n",
                                  headers={"content-type": "text/event-stream"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await lg.one_request(client, "http://x/v1/chat/completions",
                                       {}, level=1, worker=0)
        assert res.ok is True
        assert res.usage_reported is False, "no usage frame was sent"
        assert res.completion_tokens == 5, "should count content frames"

    async def test_usage_frame_with_empty_choices_does_not_crash(self):
        """vLLM's usage frame carries `choices: []`.

        A consumer indexing `choices[0]` unconditionally raises on it, and the
        request would be recorded as an exception rather than a success.
        """
        lg = _loadgen_module()

        def handler(request: httpx.Request) -> httpx.Response:
            body = (
                'data: {"choices":[{"index":0,"delta":{"content":"a "},'
                '"finish_reason":null}]}\n\n'
                'data: {"choices":[{"index":0,"delta":{"content":"b "},'
                '"finish_reason":null}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":11,'
                '"completion_tokens":2,"total_tokens":13}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, text=body,
                                  headers={"content-type": "text/event-stream"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await lg.one_request(client, "http://x/v1/chat/completions",
                                       {}, level=1, worker=0)
        assert res.ok is True
        assert res.usage_reported is True
        assert res.prompt_tokens == 11
        assert res.completion_tokens == 2


class TestFailuresAreReportedAsFailures:
    def test_upstream_503_is_counted_and_exits_nonzero(self, live_stub, tmp_path):
        url = live_stub("--stub-fail-after", "1")
        out = tmp_path / "lg.jsonl"
        result = _run(out, url, "--levels", "3", "--requests", "2",
                      "--ctx-tokens", "100", "--max-tokens", "16", "--warmup", "0")
        assert result.returncode == 1, "a run with failures must not exit 0"
        assert "WARNING" in result.stdout and "failed" in result.stdout
        _, _, levels = _records(out)
        assert levels[0]["failures"] > 0
        assert any("503" in k for k in levels[0]["errors"])


class TestProvenance:
    def test_header_carries_the_rented_box_warning(self, live_stub, tmp_path):
        """PRD §4.8: these numbers must never travel without the caveat.

        DevOps sizes per-environment GPU counts off measured TPS, so a figure
        from a vast.ai host reaching a procurement spreadsheet unlabelled is a
        real risk. Putting it in the artefact means it survives being pasted.
        """
        url = live_stub()
        out = tmp_path / "lg.jsonl"
        assert _run(out, url, "--levels", "1", "--requests", "1",
                    "--ctx-tokens", "100", "--max-tokens", "16",
                    "--warmup", "0").returncode == 0
        header, _, _ = _records(out)
        assert header["kind"] == "header"
        assert "PROVISIONAL" in header["provenance_warning"]
        assert "VM.GPU.A10.1" in header["provenance_warning"]
        assert header["ctx_tokens_requested"] == 100


class TestPreFlight:
    def test_context_plus_output_over_model_len_is_refused(self, tmp_path):
        """Catches on the laptop what would otherwise 400 on every paid request."""
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(LOADGEN), "--ctx-tokens", "7000",
             "--max-tokens", "2048", "--model-len", "8192",
             "-o", str(tmp_path / "x.jsonl")],
            cwd=REPO, capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "exceeds" in result.stderr
        assert not (tmp_path / "x.jsonl").exists()


# ── Head-to-head additions (docs/QVAC_COMPARISON_LAUNCH_PROMPT.md) ───────────

def _sse(frames: list[str]) -> httpx.Response:
    return httpx.Response(200, text="\n\n".join(frames) + "\n\n",
                          headers={"content-type": "text/event-stream"})


class TestServerSideTimings:
    async def test_llamacpp_timings_and_cached_tokens_are_recorded(self):
        """The fields that make prefill and cache hit/miss measurable per
        request. Shapes copied from the real QVAC response in
        results/qvac-rtx3090-placeholder/first_curl.out."""
        lg = _loadgen_module()

        def handler(request: httpx.Request) -> httpx.Response:
            return _sse([
                'data: {"choices":[{"index":0,"delta":{"role":"assistant",'
                '"content":null}}]}',
                'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}',
                'data: {"choices":[{"index":0,"delta":{"content":"b"}}]}',
                'data: {"choices":[{"index":0,"delta":{"content":"c"}}]}',
                'data: {"choices":[{"index":0,"delta":{},"finish_reason":"length"}],'
                '"timings":{"cache_n":6990,"prompt_n":10,"prompt_ms":21.5,'
                '"predicted_n":3,"predicted_ms":30.0}}',
                'data: {"choices":[],"usage":{"prompt_tokens":7000,'
                '"completion_tokens":3,"total_tokens":7003,'
                '"prompt_tokens_details":{"cached_tokens":6990}}}',
                "data: [DONE]",
            ])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await lg.one_request(client, "http://x/v1/chat/completions",
                                       {}, level=1, worker=0)
        assert res.ok
        assert res.server_cache_n == 6990
        assert res.server_prompt_n == 10
        assert res.server_prompt_ms == 21.5
        assert res.server_predicted_ms == 30.0
        assert res.cached_tokens == 6990
        assert res.cached == 6990
        assert res.content_frames == 3
        # Three content frames, two gaps between them.
        assert len(res.itl_ms) == 2
        assert res.itl_ms_p50 is not None

    async def test_vllm_shape_without_timings_still_reports_cached_tokens(self):
        lg = _loadgen_module()

        def handler(request: httpx.Request) -> httpx.Response:
            return _sse([
                'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}',
                'data: {"choices":[{"index":0,"delta":{"content":"b"}}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":100,'
                '"completion_tokens":2,"prompt_tokens_details":{"cached_tokens":96}}}',
                "data: [DONE]",
            ])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await lg.one_request(client, "http://x/v1/chat/completions",
                                       {}, level=1, worker=0)
        assert res.server_cache_n is None and res.server_prompt_ms is None
        assert res.cached == 96

    def test_level_summary_cache_ratio_and_hits(self):
        lg = _loadgen_module()
        mk = lg.RequestResult
        rs = [mk(level=2, worker=0, ok=True, prompt_tokens=1000, cached_tokens=990,
                 completion_tokens=10, decode_tok_s=50.0, ttft_ms=100.0,
                 total_ms=300.0, server_prompt_ms=5.0),
              mk(level=2, worker=1, ok=True, prompt_tokens=1000, cached_tokens=0,
                 completion_tokens=10, decode_tok_s=40.0, ttft_ms=900.0,
                 total_ms=1200.0, server_prompt_ms=800.0)]
        s = lg.summarise(2, rs, wall=2.0)
        assert s.cached_tokens_total == 990
        assert s.cache_hit_ratio == pytest.approx(0.495)
        assert s.requests_cache_hit == 1
        assert s.server_prefill_ms_p50 == pytest.approx(402.5)
        assert s.per_user_decode_tok_s_mean == 45.0


class TestSteadyState:
    def test_window_excludes_ramp_and_tail(self):
        """Two workers: B starts decoding at t=2 and A finishes at t=10, so the
        window is [2, 10]; A decodes 0..10 at 10 tok/s, B 2..12 at 10 tok/s."""
        lg = _loadgen_module()
        mk = lg.RequestResult
        a = mk(level=2, worker=0, ok=True, completion_tokens=101, first_s=0.0, last_s=10.0)
        b = mk(level=2, worker=1, ok=True, completion_tokens=101, first_s=2.0, last_s=12.0)
        assert lg.steady_state_rate([a, b], 2) == pytest.approx(20.0)

    def test_no_overlap_gives_none(self):
        lg = _loadgen_module()
        mk = lg.RequestResult
        a = mk(level=2, worker=0, ok=True, completion_tokens=10, first_s=0.0, last_s=1.0)
        b = mk(level=2, worker=1, ok=True, completion_tokens=10, first_s=2.0, last_s=3.0)
        assert lg.steady_state_rate([a, b], 2) is None


class TestMetricsDiff:
    VLLM_BEFORE = (
        '# HELP x\n'
        'vllm:prefix_cache_queries_total{engine="0"} 1000.0\n'
        'vllm:prefix_cache_hits_total{engine="0"} 900.0\n'
        'vllm:request_prefill_time_seconds_sum{engine="0"} 1.0\n'
        'vllm:request_prefill_time_seconds_count{engine="0"} 2.0\n'
        'vllm:request_prefill_time_seconds_bucket{engine="0",le="0.1"} 1.0\n'
        'vllm:prefix_cache_hits_created{engine="0"} 1.7e9\n'
    )
    VLLM_AFTER = (
        'vllm:prefix_cache_queries_total{engine="0"} 15000.0\n'
        'vllm:prefix_cache_hits_total{engine="0"} 14900.0\n'
        'vllm:request_prefill_time_seconds_sum{engine="0"} 2.0\n'
        'vllm:request_prefill_time_seconds_count{engine="0"} 6.0\n'
        'vllm:request_prefill_time_seconds_bucket{engine="0",le="0.1"} 5.0\n'
    )

    def test_vllm_hit_rate_and_mean_prefill(self):
        lg = _loadgen_module()
        delta = lg.diff_prom(lg.parse_prom(self.VLLM_BEFORE), lg.parse_prom(self.VLLM_AFTER))
        assert not any("_bucket" in k or "_created" in k for k in delta)
        d = lg.derive_metrics(delta)
        assert d["vllm_prefix_cache_hit_rate"] == pytest.approx(14000 / 14000)
        assert d["mean_ms:vllm:request_prefill_time_seconds"] == pytest.approx(250.0)

    def test_llamacpp_hit_rate(self):
        lg = _loadgen_module()
        before = lg.parse_prom("llamacpp:prompt_tokens_total 100\n"
                               "llamacpp:prompt_tokens_cached_total 0\n")
        after = lg.parse_prom("llamacpp:prompt_tokens_total 400\n"
                              "llamacpp:prompt_tokens_cached_total 2700\n")
        d = lg.derive_metrics(lg.diff_prom(before, after))
        assert d["llamacpp_prompt_cache_hit_rate"] == pytest.approx(0.9)


class TestPromptShapes:
    def test_cold_mode_breaks_the_prefix_at_the_first_token(self):
        lg = _loadgen_module()
        shape = lg.PromptShape(cache_mode="cold", sys_words=50)
        a, b = shape.messages(), shape.messages()
        assert a[0]["content"] != b[0]["content"]
        assert a[0]["content"][:12] != b[0]["content"][:12]
        # Constant length in words: the nonce is fixed-width digits.
        assert len(a[0]["content"].split()) == len(b[0]["content"].split())

    def test_warm_mode_shares_the_system_block(self):
        lg = _loadgen_module()
        shape = lg.PromptShape(cache_mode="warm", sys_words=50)
        a, b = shape.messages(), shape.messages()
        assert a[0]["content"] == b[0]["content"]
        assert a[1]["content"] != b[1]["content"]

    def test_partial_mode_has_long_unique_user_turn(self):
        lg = _loadgen_module()
        shape = lg.PromptShape(cache_mode="partial", sys_words=50, user_words=200)
        a, b = shape.messages(), shape.messages()
        assert a[0]["content"] == b[0]["content"]
        assert len(a[1]["content"].split()) > 200
        assert a[1]["content"][:14] != b[1]["content"][:14]

    def test_salt_changes_shared_block(self):
        lg = _loadgen_module()
        s = lg.PromptShape(cache_mode="warm", sys_words=20)
        assert lg.replace(s, salt="11111111").messages()[0]["content"] != \
            lg.replace(s, salt="22222222").messages()[0]["content"]


class TestExactPromptSizing:
    def test_calibrates_to_server_count(self, live_stub, tmp_path):
        """The stub counts prompt tokens as words; calibration must land on
        the target in the SERVER's count, whatever the word/token ratio."""
        url = live_stub()
        out = tmp_path / "lg.jsonl"
        result = _run(out, url, "--levels", "1,2", "--requests", "1",
                      "--prompt-tokens", "700", "--prompt-tolerance", "2",
                      "--max-tokens", "16", "--warmup", "0", "--settle", "0",
                      "--cache-mode", "cold")
        assert result.returncode == 0, result.stdout + result.stderr
        header, requests, levels = _records(out)
        assert header["calibration"]["target"] == 700
        assert abs(header["calibration"]["measured"] - 700) <= 2
        for r in requests:
            assert abs(r["prompt_tokens"] - 700) <= 2, r["prompt_tokens"]
            assert r["prompt_tokens_target"] == 700
            assert r["cache_mode"] == "cold"
        assert levels[0]["prompt_tokens_min"] is not None

    def test_preflight_uses_prompt_tokens(self, tmp_path):
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(LOADGEN), "--prompt-tokens", "7800",
             "--max-tokens", "512", "--model-len", "8192",
             "-o", str(tmp_path / "x.jsonl")],
            cwd=REPO, capture_output=True, text=True,
        )
        assert result.returncode == 2


class TestRepeatsAndWarmup:
    def test_repeats_write_per_repeat_levels_and_an_aggregate(self, live_stub, tmp_path):
        url = live_stub()
        out = tmp_path / "lg.jsonl"
        result = _run(out, url, "--levels", "1,2", "--requests", "1",
                      "--ctx-tokens", "100", "--max-tokens", "16",
                      "--warmup", "1", "--warmup-per-level",
                      "--warmup-concurrency", "level", "--repeats", "3",
                      "--settle", "0", "--tag", "t1")
        assert result.returncode == 0, result.stdout + result.stderr
        lines = [json.loads(x) for x in out.read_text().splitlines()]
        levels = [x for x in lines if x.get("kind") == "level"]
        aggs = [x for x in lines if x.get("kind") == "aggregate"]
        assert sorted((x["repeat"], x["concurrency"]) for x in levels) == \
            [(0, 1), (0, 2), (1, 1), (1, 2), (2, 1), (2, 2)]
        assert {a["concurrency"] for a in aggs} == {1, 2}
        assert all(a["repeats"] == 3 for a in aggs)
        assert all(x["tag"] == "t1" for x in levels)
        # Warmup requests are excluded: 3 repeats x (1 + 2) requests.
        assert len([x for x in lines if x.get("kind") == "request"]) == 9

    def test_itl_only_recorded_on_request(self, live_stub, tmp_path):
        url = live_stub()
        out = tmp_path / "lg.jsonl"
        assert _run(out, url, "--levels", "1", "--requests", "1",
                    "--ctx-tokens", "100", "--max-tokens", "16",
                    "--warmup", "0").returncode == 0
        _, requests, levels = _records(out)
        assert "itl_ms" not in requests[0]
        assert requests[0]["itl_ms_p50"] is not None
        assert levels[0]["itl_ms_p50"] is not None
        out2 = tmp_path / "lg2.jsonl"
        assert _run(out2, url, "--levels", "1", "--requests", "1",
                    "--ctx-tokens", "100", "--max-tokens", "16",
                    "--warmup", "0", "--record-itl").returncode == 0
        _, requests2, _ = _records(out2)
        assert isinstance(requests2[0]["itl_ms"], list)
