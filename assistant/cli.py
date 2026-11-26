"""Terminal chat with the fantasy GM assistant.

  python3 -m assistant.cli                                  chat (needs ANTHROPIC_API_KEY in .env)
  python3 -m assistant.cli --state my_auction.json          start from the board's Export file
  python3 -m assistant.cli --ask "Who should I target at PF?"   one question, then exit
  python3 -m assistant.cli --tool find_targets --args '{"position": "PF", "need_categories": ["3PM"]}'
                                                            call one tool directly, no LLM (works offline)
In chat: /save <file> writes the auction state (importable into docs/index.html), /tools lists tools, /quit.
"""
import argparse
import json
import sys
from pathlib import Path

from .engine import DEFAULT_DATA, Board


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default=str(DEFAULT_DATA), help="players*.js file for a roto board")
    ap.add_argument("--state", help="auction state JSON (the board's Export file)")
    ap.add_argument("--ask", help="ask one question and exit")
    ap.add_argument("--tool", help="call one tool directly (no LLM)")
    ap.add_argument("--args", default="{}", help="JSON arguments for --tool")
    ap.add_argument("--model", help="override ANTHROPIC_MODEL")
    a = ap.parse_args(argv)
    board = Board.from_files(a.board, a.state)

    if a.tool:
        from .agent import Toolbox
        try:
            out = Toolbox(board).call(a.tool, json.loads(a.args))
        except Exception as e:  # same shape the agent sees, instead of a traceback
            print(json.dumps({"error": f"{type(e).__name__}: {e}"}, indent=2))
            return 1
        print(json.dumps(out, indent=2, default=str))
        return 0

    from .agent import Agent
    agent = Agent(board, model=a.model)
    if a.ask:
        r = agent.ask(a.ask)
        print(r["answer"])
        print(f"\n[{len(r['tool_calls'])} tool calls · {r['usage']['input_tokens']} in / "
              f"{r['usage']['output_tokens']} out tokens · {r['seconds']}s]", file=sys.stderr)
        return 0

    print(f"GM assistant ({agent.model}). Ask about targets, bids, punts or player news. /quit to exit.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ("/quit", "/exit"):
            break
        if q == "/tools":
            print(", ".join(s["name"] for s in agent.tools.specs))
            continue
        if q.startswith("/save"):
            path = Path(q.split(maxsplit=1)[1] if " " in q else "auction-state.json")
            path.write_text(json.dumps(board.export_state(), indent=1))
            print(f"saved {path}")
            continue
        r = agent.ask(q)
        print("\nassistant> " + r["answer"])
        print(f"  [{', '.join(c['tool'] or '?' for c in r['tool_calls']) or 'no tools'} · "
              f"{r['usage']['input_tokens']}+{r['usage']['output_tokens']} tokens · {r['seconds']}s]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
