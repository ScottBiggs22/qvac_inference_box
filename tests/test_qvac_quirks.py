"""Stub fixtures for QVAC Fabric behaviors that have no vLLM analog.

Both of these are DOCUMENTED QVAC behaviors, not yet verified against a real
server (docs/QVAC_FORK.md's checklist) -- these tests pin what the stub does,
which is the gateway's only current point of contact with either behavior.
"""
from __future__ import annotations

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
