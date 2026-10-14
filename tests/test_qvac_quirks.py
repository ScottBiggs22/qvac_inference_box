"""Stub fixtures for QVAC Fabric behaviors that have no vLLM analog.

The context-length and vision cases are DOCUMENTED QVAC behaviors, not yet
verified against a real server (docs/QVAC_FORK.md's checklist). The
reasoning-content split is MEASURED (docs/QVAC_FIXES_RESULTS.md). These tests
pin what the stub does, and that the gateway passes it through.
"""
from __future__ import annotations

import json

import httpx


def _body(**over):
    b = {"model": "Qwen/Qwen3-8B-GGUF", "messages": [{"role": "user", "content": "hi"}]}
    b.update(over)
    return b


class TestContextLengthExceeded:
    def test_prompt_plus_completion_over_ctx_size_is_rejected(self, live_stub):
        """A default-sized request must not silently truncate.

        --stub-ctx-size 50 makes this trivial to trigger without a huge
        prompt: 64 default-parsed max_tokens alone already exceeds it.
        """
        base = live_stub("--stub-ctx-size", "50")
        r = httpx.post(f"{base}/v1/chat/completions", json=_body(), timeout=5.0)
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "context_length_exceeded"

    def test_a_request_within_budget_still_succeeds(self, live_stub):
        """The same small ctx_size does not reject a request that fits it."""
        base = live_stub("--stub-ctx-size", "50")
        r = httpx.post(f"{base}/v1/chat/completions",
                       json=_body(max_tokens=5), timeout=5.0)
        assert r.status_code == 200

    def test_the_stub_defaults_to_a_properly_configured_size(self, live_stub):
        """The gateway's own MAX_COMPLETION_TOKENS default (2048) must not
        trip this by accident when a test does not care about context length
        at all -- see StubState.ctx_size's docstring.
        """
        base = live_stub()
        r = httpx.post(f"{base}/v1/chat/completions", json=_body(), timeout=5.0)
        assert r.status_code == 200


class TestVisionContentIsSilentlyDropped:
    def test_an_image_part_does_not_error(self, live_stub):
        base = live_stub()
        body = _body(messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }])
        r = httpx.post(f"{base}/v1/chat/completions", json=body, timeout=5.0)
        assert r.status_code == 200

    def test_the_image_is_invisible_to_the_output_and_the_token_count(self, live_stub):
        """The real signature of "silently dropped": identical output and
        identical prompt_tokens whether or not an image is attached, because
        nothing downstream ever saw it -- not an error, not a placeholder, not
        an extra token.
        """
        base = live_stub()
        text_only = _body(messages=[{"role": "user", "content": "hi"}])
        with_image = _body(messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }])
        r1 = httpx.post(f"{base}/v1/chat/completions", json=text_only, timeout=5.0).json()
        r2 = httpx.post(f"{base}/v1/chat/completions", json=with_image, timeout=5.0).json()
        assert r1["choices"][0]["message"]["content"] == r2["choices"][0]["message"]["content"]
        assert r1["usage"]["prompt_tokens"] == r2["usage"]["prompt_tokens"]


class TestReasoningContentSplit:
    """QVAC's default --reasoning-format auto, measured on 2026-10-13
    (docs/QVAC_FIXES_RESULTS.md): think text arrives in `reasoning_content`,
    the answer alone in `content`, and `enable_thinking: false` produces no
    reasoning at all -- not even the empty `<think></think>` wrapper that
    `--reasoning-format none` leaves in `content`. The gateway is a
    passthrough; these pin that it stays one for the new field.
    """

    ARGS = ("--stub-think", "--stub-reasoning-format", "split")

    def _post(self, base, key, **over):
        return httpx.post(f"{base}/v1/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json=_body(max_tokens=8, **over),
                          timeout=10.0)

    def test_stub_splits_think_text_out_of_content(self, live_stub):
        base = live_stub(*self.ARGS)
        msg = httpx.post(f"{base}/v1/chat/completions", json=_body(max_tokens=8),
                         timeout=5.0).json()["choices"][0]["message"]
        assert msg["reasoning_content"]
        assert "<think>" not in msg["content"]

    def test_enable_thinking_false_yields_no_reasoning_and_no_wrapper(self, live_stub):
        base = live_stub(*self.ARGS)
        msg = httpx.post(f"{base}/v1/chat/completions",
                         json=_body(max_tokens=8,
                                    chat_template_kwargs={"enable_thinking": False}),
                         timeout=5.0).json()["choices"][0]["message"]
        assert "reasoning_content" not in msg
        assert "<think>" not in msg["content"]

    def test_gateway_passes_reasoning_content_through(self, live_gateway):
        base, key = live_gateway(*self.ARGS)
        r = self._post(base, key)
        assert r.status_code == 200
        msg = r.json()["choices"][0]["message"]
        assert msg["reasoning_content"] and "<think>" not in msg["content"]
        off = self._post(base, key, chat_template_kwargs={"enable_thinking": False})
        assert "reasoning_content" not in off.json()["choices"][0]["message"]

    def test_gateway_streams_reasoning_deltas_before_content(self, live_gateway):
        base, key = live_gateway(*self.ARGS)
        kinds = []
        with httpx.stream("POST", f"{base}/v1/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json=_body(max_tokens=8, stream=True),
                          timeout=10.0) as r:
            assert r.status_code == 200
            for line in r.iter_lines():
                if not line.startswith("data: ") or line.endswith("[DONE]"):
                    continue
                choices = json.loads(line[6:]).get("choices") or []
                delta = choices[0].get("delta", {}) if choices else {}
                if delta.get("reasoning_content"):
                    kinds.append("r")
                elif delta.get("content"):
                    kinds.append("c")
        assert "r" in kinds and "c" in kinds
        assert kinds.index("c") > max(i for i, k in enumerate(kinds) if k == "r")
