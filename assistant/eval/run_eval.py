"""Score the assistant on the question set (assistant/eval/questions.jsonl).

  python3 -m assistant.eval.run_eval              all questions (needs ANTHROPIC_API_KEY; live ones need network)
  python3 -m assistant.eval.run_eval --skip-live  skip questions that need live news feeds
  python3 -m assistant.eval.run_eval --only w01,w07

Every question starts from the same mid-auction state (assistant/eval/mid_auction_state.json: 14 sales,
my team has Maxey $62 and Murray $48). Checks per answer:
  right_tool      the expected tool was called
  top_pick        for find_targets: the engine's #1 target is named in the answer
  dollars_ok      every $ figure in the answer appears in that turn's tool results (within $1)
  names_ok        every board player named in the answer appears in that turn's tool results
  refusal_ok      for a made-up player: says he isn't on the board and gives no prices
Results: assistant/eval/results_<time>.csv plus a printed summary with token totals.
Limits: one league state, one run per question (answers can vary between runs), English questions written
by the team, not by real managers.
"""
import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path

from assistant.agent import Agent, Toolbox
from assistant.engine import Board, load_board_data, norm_name

HERE = Path(__file__).resolve().parent


def dollars(text):
    return [int(x) for x in re.findall(r"\$\s?(\d{1,3})(?!\d)", text)]


def numbers_in(payload):
    return {round(float(x)) for x in re.findall(r"-?\d+(?:\.\d+)?", payload)}


def named_players(text, names):
    t = norm_name(text)
    return [n for n in names if norm_name(n) and re.search(r"\b" + re.escape(norm_name(n)) + r"\b", t)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--model")
    a = ap.parse_args()
    qs = [json.loads(l) for l in (HERE / "questions.jsonl").read_text().splitlines() if l.strip()]
    if a.only:
        keep = set(a.only.split(","))
        qs = [q for q in qs if q["id"] in keep]
    if a.skip_live:
        qs = [q for q in qs if not q.get("live")]
    state = json.loads((HERE / "mid_auction_state.json").read_text())
    data = load_board_data()
    all_names = [p["name"] for p in data["players"]]
    rows, tok_in, tok_out = [], 0, 0
    for q in qs:
        board = Board(data, state)
        try:
            ref = Toolbox(Board(data, state)).call(q["tool"], q["ref"])
        except Exception as e:
            ref = {"error": str(e)}
        r = Agent(board, model=a.model).ask(q["q"])
        tok_in += r["usage"]["input_tokens"]
        tok_out += r["usage"]["output_tokens"]
        outs = " ".join(c.get("output", "") for c in r["tool_calls"])
        nums = numbers_in(outs)
        ans = r["answer"]
        row = {"id": q["id"], "question": q["q"], "tools": "|".join(str(c["tool"]) for c in r["tool_calls"]),
               "right_tool": any(c["tool"] == q["tool"] for c in r["tool_calls"])}
        bad_d = [d for d in dollars(ans) if not any(abs(d - n) <= 1 for n in nums)]
        row["dollars_ok"] = not bad_d
        row["bad_dollars"] = bad_d
        bad_n = [n for n in named_players(ans, all_names) if norm_name(n) not in norm_name(outs)]
        row["names_ok"] = not bad_n
        row["bad_names"] = bad_n
        if q["tool"] == "find_targets" and ref.get("targets"):
            row["top_pick"] = norm_name(ref["targets"][0]["name"]) in norm_name(ans)
        if q.get("expect_refusal"):
            row["refusal_ok"] = bool(re.search(r"not (on|in) the board|couldn'?t find|no player|not find|"
                                               r"doesn'?t (appear|exist)|isn'?t on", ans, re.I)) and not dollars(ans)
        row.update(answer=ans, input_tokens=r["usage"]["input_tokens"], output_tokens=r["usage"]["output_tokens"],
                   seconds=r["seconds"])
        rows.append(row)
        flags = {k: v for k, v in row.items() if k in ("right_tool", "dollars_ok", "names_ok", "top_pick", "refusal_ok")}
        print(q["id"], " ".join(f"{k}={'Y' if v else 'N'}" for k, v in flags.items()))
    out = HERE / f"results_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sorted({k for r in rows for k in r}))
        w.writeheader()
        w.writerows(rows)
    print("\nSummary over", len(rows), "questions")
    for k in ("right_tool", "dollars_ok", "names_ok", "top_pick", "refusal_ok"):
        vals = [r[k] for r in rows if k in r]
        if vals:
            print(f"  {k:11s} {sum(vals)}/{len(vals)}")
    print(f"  tokens      {tok_in:,} in / {tok_out:,} out")
    print("Saved", out)


if __name__ == "__main__":
    main()
