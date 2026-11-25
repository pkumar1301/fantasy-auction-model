"""Offline test of the agent loop with a fake Anthropic client (no API key or network needed).

Checks: tool calls are dispatched, tool errors go back to the model as is_error results, the loop stops
on end_turn, and the turn is logged with token usage.
Run: python3 -m assistant.tests.test_agent_loop
"""
import json
import sys
import types
from types import SimpleNamespace as NS


def _block(**kw):
    b = NS(**kw)
    b.model_dump = lambda: {k: v for k, v in kw.items()}
    return b


class FakeMessages:
    def __init__(self):
        self.calls = []
        self.script = [
            NS(stop_reason="tool_use", usage=NS(input_tokens=100, output_tokens=20), content=[
                _block(type="tool_use", id="t1", name="find_targets",
                       input={"position": "PF", "need_categories": ["3PM"], "budget_left": 40, "open_spots": 1,
                              "limit": 3}),
                _block(type="tool_use", id="t2", name="player_card", input={"name": "Nobody Atall"})]),
            NS(stop_reason="end_turn", usage=NS(input_tokens=300, output_tokens=60),
               content=[_block(type="text", text="Target the top PF on the list.")]),
        ]

    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        return self.script[len(self.calls) - 1]


def main():
    fake = FakeMessages()
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=lambda: NS(messages=fake))
    from assistant.agent import Agent
    from assistant.engine import Board
    agent = Agent(Board(), model="test-model", log=False)
    r = agent.ask("I have $40 and one PF spot, weak in threes. Who?")
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok   " if cond else "FAIL ") + msg)
        ok &= bool(cond)

    check(r["answer"] == "Target the top PF on the list.", "final answer returned")
    check([c["tool"] for c in r["tool_calls"]] == ["find_targets", "player_card"], "both tools dispatched")
    check(r["tool_calls"][1]["error"] is True, "unknown player comes back as a tool error")
    results = fake.calls[1]["messages"][-1]["content"]
    first = json.loads(results[0]["content"])
    check(first["scenario"]["budget_left"] == 40 and len(first["targets"]) == 3, "what-if result sent to model")
    check(results[1]["is_error"] is True, "is_error flag set on the failed tool")
    check(r["usage"] == {"input_tokens": 400, "output_tokens": 80}, "token usage summed")
    check("14-team" in fake.calls[0]["system"][0]["text"], "system prompt filled from board settings")
    check(len(fake.calls[0]["tools"]) == 15, "all 15 tools offered")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
