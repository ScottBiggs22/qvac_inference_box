#!/usr/bin/env python3
"""Structured-output / thinking matrix against whatever QVAC server is up.

    python3 scripts/qvac_json_matrix.py --label rf-auto_rea-auto -o matrix.jsonl

One server configuration per invocation (the caller restarts llama-server with
each `--reasoning-format` x `--reasoning` combination and passes it as
--label); this sends every request shape against it and appends one record per
request, keeping the status and the body either way. See
docs/QVAC_FIXES_LAUNCH_PROMPT.md Part A.1.

Request axes:
  enable_thinking   absent | false | true   (chat_template_kwargs, per request)
  response_format   none | json_object | json_schema strict | json_schema
                    non-strict | json_schema trivial
plus a tool call per enable_thinking value, for regression.

All prompts are synthetic (the head-to-head's own json/tool cases).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h2h_quality import SCHEMA, TOOLS, json_cases, tool_cases  # noqa: E402

TRIVIAL = {"type": "object", "properties": {"ok": {"type": "boolean"}},
           "required": ["ok"]}

FORMATS = {
    "none": None,
    "json_object": {"type": "json_object"},
    "schema_strict": {"type": "json_schema",
                      "json_schema": {"name": "txn", "schema": SCHEMA, "strict": True}},
    "schema_nonstrict": {"type": "json_schema",
                         "json_schema": {"name": "txn", "schema": SCHEMA, "strict": False}},
    "schema_trivial": {"type": "json_schema",
                       "json_schema": {"name": "ok", "schema": TRIVIAL}},
}
THINKING = {"absent": None, "false": False, "true": True}


def check_json(text: str, fmt: str) -> dict:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"parses": False}
    out = {"parses": True}
    if fmt in ("schema_strict", "schema_nonstrict"):
        out["schema_ok"] = (isinstance(obj, dict)
                            and set(obj) == set(SCHEMA["required"])
                            and obj.get("direction") in ("debit", "credit")
                            and isinstance(obj.get("amount"), (int, float)))
    elif fmt == "schema_trivial":
        out["schema_ok"] = isinstance(obj, dict) and isinstance(obj.get("ok"), bool)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--model", default="qwen3-8b")
    ap.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    ap.add_argument("--label", required=True, help="server flag combination")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--max-tokens", type=int, default=1024)
    args = ap.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    url = f"{args.url.rstrip('/')}/chat/completions"
    jprompt = json_cases()[0]["prompt"]
    tcase = tool_cases()[0]

    shapes = []
    for tname, tflag in THINKING.items():
        for fname in FORMATS:
            prompt = (jprompt if fname != "schema_trivial"
                      else "Is water wet? Answer as JSON.")
            if fname == "none":
                prompt = "What is the capital of Japan? Reply with one word."
            shapes.append((tname, tflag, fname, prompt, False))
        shapes.append((tname, tflag, "tool", tcase["prompt"], True))

    with httpx.Client(headers=headers, timeout=600) as c, open(args.output, "a") as fh:
        for tname, tflag, fname, prompt, tool in shapes:
            body: dict = {"model": args.model, "temperature": 0.0,
                          "max_tokens": args.max_tokens,
                          "messages": [{"role": "user", "content": prompt}]}
            if tflag is not None:
                body["chat_template_kwargs"] = {"enable_thinking": tflag}
            if tool:
                body["tools"] = TOOLS
                body["tool_choice"] = "auto"
            elif FORMATS[fname] is not None:
                body["response_format"] = FORMATS[fname]
            t0 = time.perf_counter()
            r = c.post(url, json=body)
            rec = {"label": args.label, "thinking": tname, "format": fname,
                   "status": r.status_code,
                   "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
            if r.status_code == 200:
                ch = r.json()["choices"][0]
                msg = ch["message"]
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                rec.update({"finish": ch.get("finish_reason"),
                            "content": content[:400],
                            "content_has_think": "<think>" in content,
                            "reasoning_chars": len(reasoning),
                            "tool_calls": msg.get("tool_calls")})
                if tool:
                    calls = msg.get("tool_calls") or []
                    rec["tool_ok"] = bool(calls) and \
                        calls[0]["function"]["name"] == tcase["expect"]["name"]
                elif fname != "none":
                    rec.update(check_json(content, fname))
            else:
                rec["error_body"] = r.text[:400]
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{args.label:<24} think={tname:<6} {fname:<16} {r.status_code} "
                  f"{rec.get('parses', rec.get('tool_ok', ''))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
