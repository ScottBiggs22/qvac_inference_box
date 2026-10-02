#!/usr/bin/env python3
"""Quality-parity probe for the head-to-head (axis 5).

    python3 scripts/h2h_quality.py run  --label vllm-awq  -o q_vllm.jsonl
    python3 scripts/h2h_quality.py run  --label qvac-q4km -o q_q4km.jsonl
    python3 scripts/h2h_quality.py compare q_vllm.jsonl q_q4km.jsonl [q_q8.jsonl]

Three families of case, all synthetic (no corpus, no fixture, no log):
  answer   short questions with a known answer: generated arithmetic plus
           fixed general-knowledge and logic items. Scored for correctness and,
           across engines, exact-match agreement of the raw text.
  json     `response_format: json_schema` requests. Scored: parses, has the
           required keys with the right types, no extra prose.
  tool     `tools` + `tool_choice: auto` requests that clearly call for one
           function. Scored: a tool call was returned, right name, arguments
           parse as JSON and carry the expected values.

Temperature 0, thinking disabled via `chat_template_kwargs.enable_thinking=
false` (both engines accept it per request), so the comparison is of the
answer, not of a 500-token think block that would dominate exact-match.
Different quantizations (AWQ-INT4 vs Q4_K_M vs Q8_0) are EXPECTED to
disagree on some exact text; correctness is the number that matters, and
agreement is reported to show how far apart they are.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time

import httpx


def answer_cases() -> list[dict]:
    rng = random.Random(20261002)
    cases = []
    for i in range(24):
        a, b = rng.randint(12, 999), rng.randint(12, 999)
        op = ["+", "-", "*"][i % 3]
        val = {"+": a + b, "-": a - b, "*": a * b}[op]
        cases.append({"id": f"arith-{i}", "kind": "answer",
                      "prompt": f"Compute {a} {op} {b}. Reply with only the number.",
                      "expect": str(val)})
    fixed = [
        ("What is the chemical symbol for gold? Reply with only the symbol.", "Au"),
        ("How many sides does a hexagon have? Reply with only the number.", "6"),
        ("What is the capital of Japan? Reply with one word.", "Tokyo"),
        ("Which planet is closest to the Sun? Reply with one word.", "Mercury"),
        ("What is 2 to the power of 10? Reply with only the number.", "1024"),
        ("If all bloops are razzies and all razzies are lazzies, are all bloops "
         "lazzies? Reply yes or no.", "yes"),
        ("Spell the word 'ledger' backwards. Reply with only the result.", "regdel"),
        ("How many minutes are in 3.5 hours? Reply with only the number.", "210"),
        ("What is the largest prime number below 50? Reply with only the number.", "47"),
        ("Sort these numbers ascending and reply with them comma-separated, no "
         "spaces: 9,2,7,4", "2,4,7,9"),
        ("A ledger has a balance of 1200. It is debited 450 and credited 300. "
         "What is the balance? Reply with only the number.", "1050"),
        ("What is the square root of 144? Reply with only the number.", "12"),
        ("How many days are in a leap year? Reply with only the number.", "366"),
        ("Convert 0.25 to a percentage. Reply like 10%.", "25%"),
        ("What is the next number: 3, 6, 12, 24, ? Reply with only the number.", "48"),
        ("Translate 'thank you' into Spanish. Reply with only the translation.",
         "gracias"),
    ]
    for i, (p, e) in enumerate(fixed):
        cases.append({"id": f"fixed-{i}", "kind": "answer", "prompt": p, "expect": e})
    return cases


SCHEMA = {
    "type": "object",
    "properties": {
        "account_id": {"type": "string"},
        "amount": {"type": "number"},
        "currency": {"type": "string"},
        "direction": {"type": "string", "enum": ["debit", "credit"]},
    },
    "required": ["account_id", "amount", "currency", "direction"],
    "additionalProperties": False,
}


def json_cases() -> list[dict]:
    items = [
        ("Account ACC-1001 was debited 250.75 USD.", "ACC-1001", 250.75, "USD", "debit"),
        ("Credit of 1,000 EUR to account ZX-77.", "ZX-77", 1000, "EUR", "credit"),
        ("Account Q9 debited by 12 GBP for a fee.", "Q9", 12, "GBP", "debit"),
        ("We credited 75.5 CHF to account LM-3.", "LM-3", 75.5, "CHF", "credit"),
        ("Debit: account RT-42, 3000 JPY.", "RT-42", 3000, "JPY", "debit"),
    ]
    return [{"id": f"json-{i}", "kind": "json",
             "prompt": f"Extract the transaction as JSON. Text: {t}",
             "expect": {"account_id": a, "amount": m, "currency": c, "direction": d}}
            for i, (t, a, m, c, d) in enumerate(items)]


TOOLS = [
    {"type": "function", "function": {
        "name": "get_balance",
        "description": "Get the current balance of an account.",
        "parameters": {"type": "object",
                       "properties": {"account_id": {"type": "string"}},
                       "required": ["account_id"]}}},
    {"type": "function", "function": {
        "name": "convert_currency",
        "description": "Convert an amount between currencies.",
        "parameters": {"type": "object",
                       "properties": {"amount": {"type": "number"},
                                      "from": {"type": "string"},
                                      "to": {"type": "string"}},
                       "required": ["amount", "from", "to"]}}},
]


def tool_cases() -> list[dict]:
    return [
        {"id": "tool-0", "kind": "tool", "prompt": "What is the balance of account ACC-1001?",
         "expect": {"name": "get_balance", "args": {"account_id": "ACC-1001"}}},
        {"id": "tool-1", "kind": "tool", "prompt": "Convert 100 USD to EUR.",
         "expect": {"name": "convert_currency",
                    "args": {"amount": 100, "from": "USD", "to": "EUR"}}},
        {"id": "tool-2", "kind": "tool", "prompt": "Check the balance on account ZX-77 please.",
         "expect": {"name": "get_balance", "args": {"account_id": "ZX-77"}}},
        {"id": "tool-3", "kind": "tool", "prompt": "How much is 2500 JPY in GBP?",
         "expect": {"name": "convert_currency",
                    "args": {"amount": 2500, "from": "JPY", "to": "GBP"}}},
        {"id": "tool-4", "kind": "tool", "prompt": "I need the balance for account LM-3.",
         "expect": {"name": "get_balance", "args": {"account_id": "LM-3"}}},
    ]


_THINK = re.compile(r"<think>.*?</think>", re.S)


def norm(s: str) -> str:
    s = _THINK.sub("", s or "").strip().strip(".").strip()
    # Strip thousands separators only: "1,050" -> "1050", but a list like
    # "2,4,7,9" must stay a list.
    return s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s) else s


def score(case: dict, msg: dict) -> dict:
    content = msg.get("content") or ""
    if case["kind"] == "answer":
        got = norm(content)
        exp = case["expect"]
        ok = got.lower() == exp.lower() or (exp.lower() in got.lower().split())
        return {"correct": ok, "answer": got}
    if case["kind"] == "json":
        try:
            obj = json.loads(_THINK.sub("", content).strip())
        except json.JSONDecodeError:
            return {"correct": False, "parses": False}
        exp = case["expect"]
        keys_ok = set(obj) == set(exp)
        types_ok = (isinstance(obj.get("account_id"), str)
                    and isinstance(obj.get("amount"), (int, float))
                    and isinstance(obj.get("currency"), str)
                    and obj.get("direction") in ("debit", "credit"))
        vals_ok = keys_ok and all(
            (abs(float(obj[k]) - float(v)) < 1e-6) if k == "amount" else obj[k] == v
            for k, v in exp.items())
        return {"correct": bool(vals_ok), "parses": True, "schema_ok": keys_ok and types_ok}
    calls = msg.get("tool_calls") or []
    if not calls:
        return {"correct": False, "tool_called": False}
    fn = calls[0].get("function") or {}
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {"correct": False, "tool_called": True, "args_parse": False}
    exp = case["expect"]
    vals_ok = all(
        (abs(float(args.get(k, "nan")) - float(v)) < 1e-6) if isinstance(v, (int, float))
        else str(args.get(k, "")).upper() == str(v).upper()
        for k, v in exp["args"].items())
    return {"correct": fn.get("name") == exp["name"] and vals_ok, "tool_called": True,
            "name": fn.get("name"), "args_parse": True}


def run(args: argparse.Namespace) -> int:
    key = args.api_key or os.environ.get("API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    cases = answer_cases() + json_cases() + tool_cases()
    url = f"{args.url.rstrip('/')}/chat/completions"
    with httpx.Client(headers=headers, timeout=300) as c, open(args.output, "w") as fh:
        fh.write(json.dumps({"kind": "header", "label": args.label, "url": args.url,
                             "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                        time.gmtime())}) + "\n")
        for case in cases:
            body = {"model": args.model, "temperature": 0.0, "max_tokens": 256,
                    "messages": [{"role": "user", "content": case["prompt"]}],
                    "chat_template_kwargs": {"enable_thinking": False}}
            if case["kind"] == "json":
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "txn", "schema": SCHEMA, "strict": True}}
            if case["kind"] == "tool":
                body["tools"] = TOOLS
                body["tool_choice"] = "auto"
            t0 = time.perf_counter()
            r = c.post(url, json=body)
            ms = (time.perf_counter() - t0) * 1000.0
            rec = {"kind": "case", "id": case["id"], "family": case["kind"],
                   "status": r.status_code, "latency_ms": round(ms, 1)}
            if r.status_code == 200:
                msg = r.json()["choices"][0]["message"]
                rec["content"] = msg.get("content")
                rec["tool_calls"] = msg.get("tool_calls")
                rec.update(score(case, msg))
            else:
                rec["correct"] = False
                rec["error_body"] = r.text[:300]
            fh.write(json.dumps(rec) + "\n")
            print(f"{case['id']:>10}  {r.status_code}  correct={rec.get('correct')}")
    return 0


def compare(paths: list[str]) -> int:
    # Answer cases are re-scored from the saved text, so a scorer fix applies
    # to runs already captured without re-querying the engine.
    cases = {c["id"]: c for c in answer_cases()}
    runs = []
    for p in paths:
        lines = [json.loads(x) for x in open(p)]
        cs = {x["id"]: x for x in lines if x["kind"] == "case"}
        for i, x in cs.items():
            if x["family"] == "answer" and x.get("status") == 200:
                x.update(score(cases[i], {"content": x.get("content")}))
        runs.append((lines[0]["label"], cs))
    ids = list(runs[0][1])
    print(f"{'family':>8}  " + "  ".join(f"{lab:>14}" for lab, _ in runs))
    for fam in ("answer", "json", "tool"):
        row = []
        for _, cs in runs:
            fam_cases = [cs[i] for i in ids if i in cs and cs[i]["family"] == fam]
            ok = sum(1 for x in fam_cases if x.get("correct"))
            row.append(f"{ok}/{len(fam_cases)}")
        print(f"{fam:>8}  " + "  ".join(f"{x:>14}" for x in row))
    base_label, base = runs[0]
    for lab, cs in runs[1:]:
        same = [i for i in ids if i in cs and cs[i]["family"] == "answer"
                and (cs[i].get("content") or "").strip()
                == (base[i].get("content") or "").strip()]
        n = sum(1 for i in ids if base[i]["family"] == "answer")
        print(f"exact-text agreement {base_label} vs {lab} (answer family): {len(same)}/{n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--url", default="http://127.0.0.1:8000/v1")
    r.add_argument("--model", default="qwen3-8b")
    r.add_argument("--api-key", default="")
    r.add_argument("--label", required=True)
    r.add_argument("-o", "--output", required=True)
    c = sub.add_parser("compare")
    c.add_argument("paths", nargs="+")
    args = ap.parse_args()
    return run(args) if args.cmd == "run" else compare(args.paths)


if __name__ == "__main__":
    sys.exit(main())
