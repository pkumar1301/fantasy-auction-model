"""Score multi-turn conversations (Muse's Part D scripts, assistant/eval/conversations.jsonl).

  python3 -m assistant.eval.run_conversations --backend claude-code        # Claude subscription, no API key
  python3 -m assistant.eval.run_conversations --backend api                # Claude API (ANTHROPIC_API_KEY)
  python3 -m assistant.eval.run_conversations --backend claude-code --runs 3 --skip-live --only D1,D4b

Every conversation starts from assistant/eval/mid_auction_state.json (my team: Maxey $62, Murray $48).
Expected values were checked by hand at tool level by the test agent (Muse, 2026-11-25). Per turn:
  tools_all / tools_any / tools_none   which tools must, may or must not be called
  numbers                              numbers that must appear in the answer (e.g. "90", "74.4")
  names_all / names_any                players that must appear
  text_any / text_all / text_none      regexes (case-insensitive) that must / must all / must not match
  state                                auction state after the turn (my_left, my_open, weight_FT ...)
  grounded                             always: every $ figure and player named comes from a tool result in
                                       the conversation so far (or the question)
A turn passes when every check passes; a conversation passes when every turn passes.
Results: assistant/eval/conv_results_<time>.json and a printed summary.
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from assistant.engine import load_board_data, norm_name
from assistant.eval.backends import make_backend
from assistant.eval.run_eval import dollars, named_players, numbers_in

HERE = Path(__file__).resolve().parent


def has_number(ans, n):
    return re.search(r"(?<![\d.])\$?" + re.escape(n) + r"(?![\d]|\.\d)", ans) is not None


def check_turn(spec, r, board, all_names, question, earlier_outputs=""):
    """earlier_outputs: tool results from previous turns of the same conversation, which the model can see
    and may legitimately quote (e.g. the budget from turn 1 in turn 2)."""
    ans, calls = r["answer"], r["tool_calls"]
    used = [c["tool"] for c in calls]
    outs = earlier_outputs + " " + " ".join(c.get("output", "") for c in calls)
    res = {}
    if "tools_all" in spec:
        res["tools_all"] = all(t in used for t in spec["tools_all"])
    if "tools_any" in spec:
        res["tools_any"] = any(t in used for t in spec["tools_any"])
    if "tools_none" in spec:
        res["tools_none"] = not any(t in used for t in spec["tools_none"])
    if "numbers" in spec:
        res["numbers"] = all(has_number(ans, n) for n in spec["numbers"])
    named = {norm_name(n) for n in named_players(ans, all_names)}
    if "names_all" in spec:
        res["names_all"] = all(norm_name(n) in named for n in spec["names_all"])
    if "names_any" in spec:
        res["names_any"] = any(norm_name(n) in named for n in spec["names_any"])
    if "text_any" in spec:
        res["text_any"] = any(re.search(p, ans, re.I) for p in spec["text_any"])
    if "text_all" in spec:
        res["text_all"] = all(re.search(p, ans, re.I) for p in spec["text_all"])
    if "text_none" in spec:
        res["text_none"] = not any(re.search(p, ans, re.I) for p in spec["text_none"])
    if "state" in spec:
        me = board.me
        got = {"my_left": me.left, "my_open": me.open, **{f"weight_{c}": board.st["weights"][c] for c in board.cats}}
        res["state"] = all(abs(got[k] - v) < 1e-9 for k, v in spec["state"].items())
    nums = numbers_in(outs) | {int(x) for x in re.findall(r"\$\s?(\d{1,3})", question)}
    res["grounded_dollars"] = all(any(abs(d - n) <= 1 for n in nums) for d in dollars(ans))
    res["grounded_names"] = all(n in norm_name(outs) or n in norm_name(question) for n in named)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="claude-code", choices=["api", "claude-code"])
    ap.add_argument("--model", help="e.g. sonnet / opus for claude-code, or a full model id")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only")
    ap.add_argument("--skip-live", action="store_true", help="skip conversations that need live news sites")
    ap.add_argument("--file", default=str(HERE / "conversations.jsonl"), help="conversation set (JSONL)")
    a = ap.parse_args()
    convs = [json.loads(l) for l in Path(a.file).read_text().splitlines() if l.strip()]
    if a.only:
        keep = set(a.only.split(","))
        convs = [c for c in convs if c["id"] in keep]
    if a.skip_live:
        convs = [c for c in convs if not c.get("live")]
    state = json.loads((HERE / "mid_auction_state.json").read_text())
    all_names = [p["name"] for p in load_board_data()["players"]]
    backend = make_backend(a.backend, a.model)
    out, summary = [], []
    for run in range(1, a.runs + 1):
        passed = tok_in = tok_out = 0
        cost = 0.0
        for conv in convs:
            s = backend.session(state)
            turns, seen = [], ""
            try:
                for spec in conv["turns"]:
                    r = s.ask(spec["q"])
                    chk = check_turn(spec, r, s.board(), all_names, spec["q"], seen)
                    seen += " " + " ".join(c.get("output", "") for c in r["tool_calls"])
                    tok_in += r["usage"]["input_tokens"]
                    tok_out += r["usage"]["output_tokens"]
                    cost += r.get("cost_usd") or 0
                    turns.append({"q": spec["q"], "checks": chk, "pass": all(chk.values()),
                                  "tools": [c["tool"] for c in r["tool_calls"]], "answer": r["answer"],
                                  "seconds": r["seconds"]})
            finally:
                s.close()
            ok = all(t["pass"] for t in turns)
            passed += ok
            out.append({"run": run, "id": conv["id"], "title": conv["title"], "pass": ok, "turns": turns})
            fails = [f"turn {i + 1}: " + ",".join(k for k, v in t["checks"].items() if not v)
                     for i, t in enumerate(turns) if not t["pass"]]
            print(f"run {run} {conv['id']:4s} {'PASS' if ok else 'FAIL'}  {conv['title']}" +
                  (f"   [{'; '.join(fails)}]" if fails else ""))
        summary.append({"run": run, "passed": passed, "total": len(convs), "input_tokens": tok_in,
                        "output_tokens": tok_out, "cost_usd_estimate": round(cost, 2)})
    path = HERE / f"conv_results_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps({"backend": a.backend, "model": a.model, "summary": summary, "results": out}, indent=1))
    print()
    for s in summary:
        print(f"run {s['run']}: {s['passed']}/{s['total']} conversations passed · "
              f"{s['input_tokens']:,} in / {s['output_tokens']:,} out tokens · est. ${s['cost_usd_estimate']}")
    print("Saved", path)


if __name__ == "__main__":
    main()
