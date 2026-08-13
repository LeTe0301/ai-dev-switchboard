#!/usr/bin/env python3
"""
Spike for 6c: can a local Ollama model reliably drive the lead's four-tool
loop? Stdlib only, same call shape app.py already uses for DESC_LLM.

Measures, per prompt: well-formed tool call / wrong tool / prose fallback
(no tool call at all) / malformed args / transport error.
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://100.70.98.74:11434/v1"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen3:8b"

TOOLS = [
    {"type": "function", "function": {
        "name": "delegate",
        "description": "Give one self-contained task to a named teammate agent.",
        "parameters": {"type": "object", "properties": {
            "agent": {"type": "string", "enum": ["claude", "codex", "aider"]},
            "task": {"type": "string", "description": "The full self-contained task."}},
            "required": ["agent", "task"]}}},
    {"type": "function", "function": {
        "name": "fact_check",
        "description": "Verify a claim against the project's own documentation.",
        "parameters": {"type": "object", "properties": {
            "claim": {"type": "string"}}, "required": ["claim"]}}},
    {"type": "function", "function": {
        "name": "ask_user",
        "description": "Ask the human a question when something is genuinely unresolved.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "header": {"type": "string"},
            "options": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "description": {"type": "string"}}}}},
            "required": ["question", "options"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Conclude the task with a summary.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"}}, "required": ["summary"]}}},
]

SYSTEM = ("You are the lead of a team of coding agents working on one project. "
          "You must respond by calling exactly one tool. Never answer in prose.")

# (prompt, expected tool) -- chosen to exercise each of the four branches
CASES = [
    ("Add a --verbose flag to app.py. Hand the implementation to a teammate.", "delegate"),
    ("Have someone write regression tests for the upload wizard.", "delegate"),
    ("Get codex to review the diff on the auth module.", "delegate"),
    ("Before I plan, confirm: does app.py run as an unprivileged system account?", "fact_check"),
    ("Verify the claim that engines are configuration rather than code.", "fact_check"),
    ("The spec doesn't say whether retries belong in the client or the caller. "
     "I cannot proceed without knowing.", "ask_user"),
    ("Two valid database schemas are possible and the choice is the user's.", "ask_user"),
    ("All teammates reported success and tests pass. Wrap up.", "finish"),
    ("The work is complete. Summarize what the team did.", "finish"),
    ("Delegate the CSS refactor, it is self-contained.", "delegate"),
]


def call(messages):
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS,
                       "stream": False, "temperature": 0}).encode()
    req = urllib.request.Request(f"{BASE}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def classify(payload, expected):
    try:
        msg = payload["choices"][0]["message"]
    except (KeyError, IndexError):
        return "malformed_response", ""
    calls = msg.get("tool_calls") or []
    if not calls:
        return "prose_fallback", (msg.get("content") or "")[:60]
    fn = calls[0].get("function", {})
    name = fn.get("name", "?")
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except ValueError:
        return "malformed_args", name
    if not isinstance(args, dict):
        return "malformed_args", name
    if name != expected:
        return "wrong_tool", f"{name} (wanted {expected})"
    return "ok", name


def main():
    print(f"endpoint : {BASE}\nmodel    : {MODEL}\ncases    : {len(CASES)}\n")
    tally, lat = {}, []
    for i, (prompt, expected) in enumerate(CASES, 1):
        t0 = time.time()
        try:
            payload = call([{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": prompt}])
            verdict, detail = classify(payload, expected)
        except Exception as e:
            verdict, detail = "transport_error", type(e).__name__
        dt = time.time() - t0
        lat.append(dt)
        tally[verdict] = tally.get(verdict, 0) + 1
        flag = "ok  " if verdict == "ok" else "FAIL"
        print(f"  {i:2}. [{flag}] {verdict:18} {detail:34} {dt:5.1f}s  <- {prompt[:44]}")

    n = len(CASES)
    ok = tally.get("ok", 0)
    print(f"\n  {'RESULT':10} {ok}/{n} well-formed correct tool calls "
          f"({100*ok//n}%)")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        if k != "ok":
            print(f"  {'':10} {v}x {k}")
    if lat:
        print(f"  {'latency':10} mean {sum(lat)/len(lat):.1f}s  max {max(lat):.1f}s")


if __name__ == "__main__":
    main()
