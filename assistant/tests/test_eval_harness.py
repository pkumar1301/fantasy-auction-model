"""Offline tests for the evaluation harness: Claude Code stream parsing and conversation checks.

Run: python3 -m assistant.tests.test_eval_harness
"""
import json
import sys

from assistant.engine import Board, load_board_data
from assistant.eval.backends import parse_stream
from assistant.eval.run_conversations import check_turn, has_number

RESULTS = []


def check(cond, msg):
    RESULTS.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + msg)


def main():
    stream = "\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init", "session_id": "abc"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__fantasy-gm__bid_check", "input": {"name": "Trey Murphy III", "price": 40}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "{\"my_max\": 47, \"live\": 41}"}]}]}},
        {"type": "result", "result": "Keep going: your max is $47.", "session_id": "abc", "total_cost_usd": 0.05,
         "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 20}},
    ])
    r = parse_stream(stream + "\nnot json\n")
    check(r["session_id"] == "abc" and r["answer"] == "Keep going: your max is $47.", "stream: answer and session id")
    check(r["tool_calls"][0]["tool"] == "bid_check" and "47" in r["tool_calls"][0]["output"], "stream: MCP prefix stripped, output captured")
    check(r["usage"] == {"input_tokens": 100, "output_tokens": 20} and r["cost_usd"] == 0.05, "stream: usage incl. cache reads")

    check(has_number("total 74.4 points", "74.4") and not has_number("total 174.4", "74.4") and has_number("$90 left", "90")
          and not has_number("$900", "90") and not has_number("74.45", "74.4"), "number matching is exact")

    data = load_board_data()
    names = [p["name"] for p in data["players"]]
    st = json.load(open("assistant/eval/mid_auction_state.json"))
    b = Board(data, st)
    spec = {"tools_all": ["bid_check"], "tools_none": ["record_sale"], "numbers": ["47"], "text_any": ["keep"],
            "state": {"my_left": 90}}
    res = check_turn(spec, r, b, names, "Trey Murphy III is up at $40. Keep bidding?")
    check(all(res.values()), f"grounded answer passes every check {res}")
    bad = dict(r, answer="Bid up to $55 on Trey Murphy III, or grab Zion Williamson.")
    res = check_turn(spec, bad, b, names, "q")
    check(not res["grounded_dollars"] and not res["grounded_names"] and not res["numbers"],
          "invented $55 and a player no tool returned are caught")
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
